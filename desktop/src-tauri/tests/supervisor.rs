//! Supervisor behaviour against a fake `mynotes serve`: handshake, calls, notifications,
//! timeouts, crash restarts with backoff, the crash-loop stop, protocol refusal and a shutdown
//! that leaves no process behind.
//!
//! The fake server is a short Python program (python3 is on every CI runner and dev machine);
//! `FAKE_MODE` picks its misbehaviour.
#![cfg(unix)]

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use mynotes_desktop_lib::backend::protocol::codes;
use mynotes_desktop_lib::backend::supervisor::{
    failure, BackendStatus, EventSink, Launch, LaunchError, LaunchFn, Supervisor, SupervisorConfig,
};
use mynotes_desktop_lib::selfcheck::wait_gone;
use serde_json::{json, Value};

const FAKE_SERVER: &str = r#"
import json, os, signal, subprocess, sys, threading, time
mode = os.environ.get("FAKE_MODE", "ok")
out_lock = threading.Lock()
def send(message):
    with out_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()
print("fake serve starting", file=sys.stderr, flush=True)
if mode == "crash-at-start":
    print("boom: cannot import mynotes", file=sys.stderr, flush=True)
    sys.exit(7)
if mode == "stubborn":
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
grandchild = None
if os.environ.get("FAKE_GRANDCHILD") == "1":
    grandchild = subprocess.Popen(["sleep", "300"])
    with open(os.environ["FAKE_PID_FILE"], "w") as handle:
        handle.write(f"{os.getpid()} {grandchild.pid}")
def answer(request):
    method, params, rid = request.get("method"), request.get("params") or {}, request.get("id")
    if method == "system.hello":
        if mode == "error-protocol":
            return send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32005, "message": "Unsupported protocol", "data": {"supported": [1]}}})
        protocol = 2 if mode == "bad-protocol" else 1
        return send({"jsonrpc": "2.0", "id": rid, "result": {"protocol": protocol, "pid": os.getpid(), "version": "fake", "methods": []}})
    if method == "system.ping":
        if mode == "silent-ping":
            return
        return send({"jsonrpc": "2.0", "id": rid, "result": {"ok": True}})
    if method == "test.echo":
        return send({"jsonrpc": "2.0", "id": rid, "result": params})
    if method == "test.notify":
        send({"jsonrpc": "2.0", "method": "capture.changed", "params": {"topic": "capture", "hash": "h", "data": {"n": 1}}})
        return send({"jsonrpc": "2.0", "id": rid, "result": {"sent": 1}})
    if method == "test.sleep":
        time.sleep(float(params.get("seconds", 1)))
        return send({"jsonrpc": "2.0", "id": rid, "result": {"slept": True}})
    if method == "test.crash":
        os._exit(3)
    send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": "Unknown method"}})
sys.stdout.write("not json, ignored by the client\n")
sys.stdout.flush()
for line in sys.stdin:
    if not line.strip():
        continue
    request = json.loads(line)
    if "id" not in request:
        continue
    threading.Thread(target=answer, args=(request,), daemon=True).start()
if mode == "stubborn":
    while True:
        time.sleep(1)
"#;

#[derive(Default)]
struct RecordingSink {
    statuses: Mutex<Vec<BackendStatus>>,
    notifications: Mutex<Vec<(String, Value)>>,
}

impl EventSink for RecordingSink {
    fn notification(&self, method: &str, params: &Value) {
        self.notifications.lock().unwrap().push((method.to_owned(), params.clone()));
    }
    fn status(&self, status: &BackendStatus) {
        self.statuses.lock().unwrap().push(status.clone());
    }
}

struct Harness {
    supervisor: Supervisor,
    sink: Arc<RecordingSink>,
    spawns: Arc<AtomicUsize>,
}

fn fast_backoff(_crashes: u32) -> Duration {
    Duration::from_millis(20)
}

fn config() -> SupervisorConfig {
    SupervisorConfig {
        hello_timeout: Duration::from_secs(10),
        ping_interval: Duration::from_secs(60),
        ping_timeout: Duration::from_secs(5),
        shutdown_grace: Duration::from_secs(2),
        ready_wait: Duration::from_secs(10),
        backoff: fast_backoff,
        ..SupervisorConfig::default()
    }
}

fn harness(mode: &str, extra_env: &[(&str, String)], config: SupervisorConfig) -> Harness {
    harness_on(None, mode, extra_env, config)
}

/// `runtime: None` builds with [`Supervisor::new`], which takes the runtime current here, if any.
fn harness_on(
    runtime: Option<tokio::runtime::Handle>,
    mode: &str,
    extra_env: &[(&str, String)],
    config: SupervisorConfig,
) -> Harness {
    let spawns = Arc::new(AtomicUsize::new(0));
    let counter = Arc::clone(&spawns);
    let mut env = vec![("FAKE_MODE".into(), mode.into())];
    env.extend(extra_env.iter().map(|(key, value)| ((*key).into(), value.into())));
    let launch: LaunchFn = Arc::new(move || {
        counter.fetch_add(1, Ordering::SeqCst);
        Ok(Launch {
            program: "python3".into(),
            args: vec!["-u".into(), "-c".into(), FAKE_SERVER.into()],
            env: env.clone(),
            cwd: None,
            kind: "test".into(),
        })
    });
    let sink = Arc::new(RecordingSink::default());
    let supervisor = match runtime {
        Some(runtime) => Supervisor::on_runtime(runtime, config, launch, Arc::clone(&sink) as Arc<dyn EventSink>),
        None => Supervisor::new(config, launch, Arc::clone(&sink) as Arc<dyn EventSink>),
    };
    Harness { supervisor, sink, spawns }
}

fn ready_pid(status: &BackendStatus) -> (u32, Option<u32>, u64) {
    match status {
        BackendStatus::Ready { pid, server_pid, generation, .. } => (*pid, *server_pid, *generation),
        other => panic!("expected ready, got {other:?}"),
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn handshake_calls_and_notifications_round_trip() {
    let h = harness("ok", &[], config());
    h.supervisor.start();
    // A call made while the backend is still starting waits for it instead of failing.
    let early = h.supervisor.call("test.echo", Some(json!({"early": true}))).await.unwrap();
    assert_eq!(early, json!({"early": true}));
    let (pid, server_pid, generation) = ready_pid(&h.supervisor.wait_ready(Duration::from_secs(10)).await);
    assert_eq!(server_pid, Some(pid), "python3 is the direct child here");
    assert_eq!(generation, 1);

    let unicode = json!({"title": "Microeconomia · Lezione 5", "nested": {"list": [1, 2, 3]}});
    assert_eq!(h.supervisor.call("test.echo", Some(unicode.clone())).await.unwrap(), unicode);
    assert_eq!(h.supervisor.call("test.notify", None).await.unwrap(), json!({"sent": 1}));
    let unknown = h.supervisor.call("nope.nothing", None).await.unwrap_err();
    assert_eq!(unknown.code, codes::METHOD_NOT_FOUND);
    {
        let notifications = h.sink.notifications.lock().unwrap();
        assert_eq!(notifications.len(), 1);
        assert_eq!(notifications[0].0, "capture.changed");
        assert_eq!(notifications[0].1["data"]["n"], 1);
    }

    // Concurrent calls are matched to their own answers even when they finish out of order.
    let slow = h.supervisor.call("test.sleep", Some(json!({"seconds": 0.4})));
    let fast = h.supervisor.call("test.echo", Some(json!({"fast": 1})));
    let (slow, fast) = tokio::join!(slow, fast);
    assert_eq!(slow.unwrap(), json!({"slept": true}));
    assert_eq!(fast.unwrap(), json!({"fast": 1}));

    h.supervisor.shutdown().await;
    assert_eq!(h.supervisor.status(), BackendStatus::Stopped);
    assert!(wait_gone(&[pid], Duration::from_secs(3)).is_empty());
    let after = h.supervisor.call("test.echo", None).await.unwrap_err();
    assert_eq!(after.code, codes::BACKEND_UNAVAILABLE);
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn pending_calls_fail_with_backend_restarted_and_the_backend_comes_back() {
    let h = harness("ok", &[], config());
    h.supervisor.start();
    let (first_pid, _, _) = ready_pid(&h.supervisor.wait_ready(Duration::from_secs(10)).await);

    let waiting = {
        let supervisor = h.supervisor.clone();
        tokio::spawn(async move { supervisor.call("test.sleep", Some(json!({"seconds": 30}))).await })
    };
    tokio::time::sleep(Duration::from_millis(200)).await;
    let crash = h.supervisor.call("test.crash", None).await.unwrap_err();
    assert_eq!(crash.code, codes::BACKEND_RESTARTED);
    let pending = waiting.await.unwrap().unwrap_err();
    assert_eq!(pending.code, codes::BACKEND_RESTARTED, "a call in flight learns about the restart");

    // `wait_ready` would return the old status at once; wait for the next generation instead.
    let deadline = Instant::now() + Duration::from_secs(10);
    let (second_pid, generation) = loop {
        if let BackendStatus::Ready { pid, generation, .. } = h.supervisor.status() {
            if generation == 2 {
                break (pid, generation);
            }
        }
        assert!(Instant::now() < deadline, "no restart: {:?}", h.supervisor.status());
        tokio::time::sleep(Duration::from_millis(20)).await;
    };
    assert_ne!(first_pid, second_pid);
    assert_eq!(generation, 2);
    assert_eq!(h.spawns.load(Ordering::SeqCst), 2);
    let saw_restarting = h.sink.statuses.lock().unwrap().iter().any(|s| matches!(s, BackendStatus::Restarting { .. }));
    assert!(saw_restarting, "the UI is told about the restart");
    assert_eq!(h.supervisor.call("test.echo", Some(json!({"again": true}))).await.unwrap(), json!({"again": true}));
    h.supervisor.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn five_crashes_in_the_window_stop_the_loop_with_the_stderr_tail() {
    let h = harness("crash-at-start", &[], config());
    h.supervisor.start();
    let status = h.supervisor.wait_ready(Duration::from_secs(20)).await;
    let BackendStatus::Failed { code, crashes, stderr_tail, message } = status else {
        panic!("expected failed, got {status:?}")
    };
    assert_eq!(code, failure::CRASH_LOOP);
    assert_eq!(crashes, 5);
    assert_eq!(h.spawns.load(Ordering::SeqCst), 5, "no sixth attempt");
    assert!(stderr_tail.iter().any(|line| line.contains("boom: cannot import mynotes")), "{stderr_tail:?}");
    assert!(message.contains("exited"), "{message}");
    let restarts = h
        .sink
        .statuses
        .lock()
        .unwrap()
        .iter()
        .filter(|s| matches!(s, BackendStatus::Restarting { delay_ms: 20, .. }))
        .count();
    assert_eq!(restarts, 4, "four waits between five attempts, using the configured backoff");

    let unavailable = h.supervisor.call("test.echo", None).await.unwrap_err();
    assert_eq!(unavailable.code, codes::BACKEND_UNAVAILABLE);
    assert!(unavailable.message.contains("CRASH_LOOP"));

    // The failure screen's "Try again" starts a fresh loop.
    h.supervisor.restart();
    let again = h.supervisor.wait_ready(Duration::from_secs(20)).await;
    assert!(matches!(again, BackendStatus::Failed { crashes: 5, .. }), "{again:?}");
    assert_eq!(h.spawns.load(Ordering::SeqCst), 10, "counters started from zero");
    h.supervisor.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_protocol_mismatch_is_permanent_not_a_crash_loop() {
    for mode in ["bad-protocol", "error-protocol"] {
        let h = harness(mode, &[], config());
        h.supervisor.start();
        let status = h.supervisor.wait_ready(Duration::from_secs(10)).await;
        assert!(
            matches!(&status, BackendStatus::Failed { code, .. } if code == failure::PROTOCOL_MISMATCH),
            "{mode}: {status:?}"
        );
        tokio::time::sleep(Duration::from_millis(200)).await;
        assert_eq!(h.spawns.load(Ordering::SeqCst), 1, "{mode}: version skew is not retried");
        h.supervisor.shutdown().await;
    }
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_call_that_outlives_its_limit_times_out_and_the_backend_stays_up() {
    let h =
        harness("ok", &[], SupervisorConfig { call_timeout_override: Some(Duration::from_millis(300)), ..config() });
    h.supervisor.start();
    let (pid, _, _) = ready_pid(&h.supervisor.wait_ready(Duration::from_secs(10)).await);
    let started = Instant::now();
    let error = h.supervisor.call("test.sleep", Some(json!({"seconds": 5}))).await.unwrap_err();
    assert_eq!(error.code, codes::TIMEOUT);
    assert!(started.elapsed() < Duration::from_secs(2));
    assert_eq!(h.supervisor.call("test.echo", Some(json!({"ok": 1}))).await.unwrap(), json!({"ok": 1}));
    assert_eq!(ready_pid(&h.supervisor.status()).0, pid, "a slow call is not a crash");
    h.supervisor.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn an_unanswered_ping_restarts_the_backend() {
    let config = SupervisorConfig {
        ping_interval: Duration::from_millis(200),
        ping_timeout: Duration::from_millis(200),
        ..config()
    };
    let h = harness("silent-ping", &[], config);
    h.supervisor.start();
    let (pid, _, _) = ready_pid(&h.supervisor.wait_ready(Duration::from_secs(10)).await);
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        let restarted = h
            .sink
            .statuses
            .lock()
            .unwrap()
            .iter()
            .any(|s| matches!(s, BackendStatus::Restarting { reason, .. } if reason.contains("no answer to ping")));
        if restarted {
            break;
        }
        assert!(Instant::now() < deadline, "no restart after silent pings");
        tokio::time::sleep(Duration::from_millis(50)).await;
    }
    assert!(wait_gone(&[pid], Duration::from_secs(3)).is_empty(), "the hung backend was killed");
    h.supervisor.shutdown().await;
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn shutdown_leaves_no_process_behind_even_grandchildren() {
    let pid_file = std::env::temp_dir().join(format!("mynotes-desktop-fake-pids-{}", std::process::id()));
    let h =
        harness("ok", &[("FAKE_GRANDCHILD", "1".into()), ("FAKE_PID_FILE", pid_file.display().to_string())], config());
    h.supervisor.start();
    let (pid, _, _) = ready_pid(&h.supervisor.wait_ready(Duration::from_secs(10)).await);
    let pids: Vec<u32> =
        std::fs::read_to_string(&pid_file).unwrap().split_whitespace().map(|p| p.parse().unwrap()).collect();
    assert_eq!(pids[0], pid);
    assert!(wait_gone(&pids, Duration::ZERO).len() == 2, "both processes run before shutdown");
    h.supervisor.shutdown().await;
    assert_eq!(wait_gone(&pids, Duration::from_secs(5)), Vec::<u32>::new(), "server and grandchild are gone");
    std::fs::remove_file(pid_file).unwrap();
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_backend_that_ignores_eof_and_sigterm_is_killed() {
    let h = harness("stubborn", &[], SupervisorConfig { shutdown_grace: Duration::from_millis(300), ..config() });
    h.supervisor.start();
    let (pid, _, _) = ready_pid(&h.supervisor.wait_ready(Duration::from_secs(10)).await);
    let started = Instant::now();
    h.supervisor.shutdown().await;
    assert!(started.elapsed() < Duration::from_secs(5), "shutdown took {:?}", started.elapsed());
    assert!(wait_gone(&[pid], Duration::from_secs(3)).is_empty());
}

#[tokio::test(flavor = "multi_thread", worker_threads = 2)]
async fn a_missing_runtime_fails_at_once_with_its_code() {
    let sink = Arc::new(RecordingSink::default());
    let launch: LaunchFn =
        Arc::new(|| Err(LaunchError { code: "NOT_INSTALLED".into(), message: "MyNotes is not installed yet".into() }));
    let supervisor = Supervisor::new(config(), launch, Arc::clone(&sink) as Arc<dyn EventSink>);
    supervisor.start();
    let status = supervisor.wait_ready(Duration::from_secs(5)).await;
    assert!(matches!(&status, BackendStatus::Failed { code, .. } if code == "NOT_INSTALLED"), "{status:?}");
    let error = supervisor.call("system.ping", None).await.unwrap_err();
    assert_eq!(error.code, codes::BACKEND_UNAVAILABLE);
    supervisor.shutdown().await;
}

/// The installer's completion callback (a plain std thread) and the sync `backend_restart`
/// command (Tauri's main thread) call `restart()` outside any Tokio runtime. It used to call
/// `tokio::spawn` there, which panics, and the release build aborts on panic.
#[test]
fn restart_from_a_plain_thread_runs_the_loop_on_the_supervisors_runtime() {
    let runtime = tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build().unwrap();
    let h = harness_on(Some(runtime.handle().clone()), "ok", &[], config());
    let supervisor = h.supervisor.clone();
    std::thread::spawn(move || supervisor.restart()).join().expect("restart() panicked outside a runtime");
    let (pid, _, _) = ready_pid(&runtime.block_on(h.supervisor.wait_ready(Duration::from_secs(10))));
    let supervisor = h.supervisor.clone();
    std::thread::spawn(move || supervisor.restart()).join().expect("a second restart() panicked");
    assert_eq!(h.spawns.load(Ordering::SeqCst), 1, "restart while running keeps the running loop");
    runtime.block_on(h.supervisor.shutdown());
    assert!(wait_gone(&[pid], Duration::from_secs(5)).is_empty(), "the backend is gone after shutdown");
}

#[test]
fn without_any_runtime_start_fails_with_a_status_instead_of_panicking() {
    let h = harness_on(None, "ok", &[], config()); // Built here, outside any runtime.
    let supervisor = h.supervisor.clone();
    std::thread::spawn(move || supervisor.start()).join().expect("start() panicked outside a runtime");
    let status = h.supervisor.status();
    assert!(matches!(&status, BackendStatus::Failed { code, .. } if code == failure::NO_RUNTIME), "{status:?}");
    assert_eq!(h.spawns.load(Ordering::SeqCst), 0);
    assert!(h.sink.statuses.lock().unwrap().iter().any(|s| matches!(s, BackendStatus::Failed { .. })));
}

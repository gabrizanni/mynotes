//! Headless self-check: `mynotes-desktop --self-check` (or `MYNOTES_DESKTOP_SELF_CHECK=1`).
//!
//! Runs the real Rust ↔ Python path without Tauri, GTK or any window: locate Python, start the
//! supervisor, wait for `system.hello`, call `capture.dashboard`, shut down, then confirm that
//! neither the spawned process nor the server process is still alive. Prints one JSON object on
//! stdout and exits 0 when every step passed, 1 otherwise. A backend that answers
//! `capture.dashboard` with a command error (for example an uninitialized workspace) still
//! proves the bridge; the answer kind is reported.
//!
//! The JSON holds no paths, only kinds, versions, process ids and timings.

use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};

use crate::backend::locate;
use crate::backend::supervisor::{BackendStatus, EventSink, Supervisor, SupervisorConfig};

pub const FLAG: &str = "--self-check";
pub const ENV: &str = "MYNOTES_DESKTOP_SELF_CHECK";
const READY_LIMIT: Duration = Duration::from_secs(120);

pub fn requested<S: AsRef<str>>(args: &[S], env_value: Option<&str>) -> bool {
    args.iter().any(|arg| arg.as_ref() == FLAG) || env_value == Some("1")
}

#[derive(Debug, Serialize)]
#[serde(rename_all = "camelCase")]
struct Step {
    name: &'static str,
    ok: bool,
    ms: u128,
    #[serde(flatten)]
    detail: serde_json::Map<String, Value>,
}

fn step(name: &'static str, ok: bool, started: Instant, detail: Value) -> Step {
    let detail = match detail {
        Value::Object(map) => map,
        _ => serde_json::Map::new(),
    };
    Step { name, ok, ms: started.elapsed().as_millis(), detail }
}

#[derive(Default)]
struct CountingSink {
    notifications: AtomicUsize,
    statuses: AtomicUsize,
}

impl EventSink for CountingSink {
    fn notification(&self, _method: &str, _params: &Value) {
        self.notifications.fetch_add(1, Ordering::Relaxed);
    }
    fn status(&self, _status: &BackendStatus) {
        self.statuses.fetch_add(1, Ordering::Relaxed);
    }
}

/// Whether a process id still names a live (non-zombie) process.
#[cfg(unix)]
pub fn process_alive(pid: u32) -> bool {
    let Ok(pid) = libc::pid_t::try_from(pid) else { return false };
    // SAFETY: signal 0 only checks existence and permission.
    let exists = unsafe { libc::kill(pid, 0) } == 0;
    if !exists {
        return false;
    }
    // A reaped-later zombie is not running anymore.
    std::fs::read_to_string(format!("/proc/{pid}/stat"))
        .map(|stat| stat.rsplit_once(')').and_then(|(_, rest)| rest.trim_start().chars().next()) != Some('Z'))
        .unwrap_or(true)
}

#[cfg(not(unix))]
pub fn process_alive(_pid: u32) -> bool {
    false
}

/// Poll until every pid is gone or `limit` passes; returns the ones still alive.
pub fn wait_gone(pids: &[u32], limit: Duration) -> Vec<u32> {
    let deadline = Instant::now() + limit;
    loop {
        let alive: Vec<u32> = pids.iter().copied().filter(|pid| process_alive(*pid)).collect();
        if alive.is_empty() || Instant::now() >= deadline {
            return alive;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
}

async fn checks() -> (bool, Value) {
    let mut steps = Vec::new();
    let begun = Instant::now();

    let started = Instant::now();
    let launch = tokio::task::spawn_blocking(locate::launch_from_environment).await;
    let launch = match launch {
        Ok(Ok(launch)) => {
            steps.push(step("locate", true, started, json!({ "kind": launch.kind })));
            launch
        }
        Ok(Err(error)) => {
            steps.push(step("locate", false, started, json!({ "code": error.code, "message": error.message })));
            return (false, json!({ "steps": steps, "elapsedMs": begun.elapsed().as_millis() }));
        }
        Err(error) => {
            steps.push(step("locate", false, started, json!({ "message": error.to_string() })));
            return (false, json!({ "steps": steps, "elapsedMs": begun.elapsed().as_millis() }));
        }
    };
    let kind = launch.kind.clone();

    let started = Instant::now();
    let sink = Arc::new(CountingSink::default());
    let resolved = launch.clone();
    let supervisor =
        Supervisor::new(SupervisorConfig::default(), Arc::new(move || Ok(resolved.clone())), Arc::clone(&sink) as _);
    supervisor.start();
    let status = supervisor.wait_ready(READY_LIMIT).await;
    let (pid, server_pid) = match &status {
        BackendStatus::Ready { pid, server_pid, protocol, version, .. } => {
            steps.push(step(
                "hello",
                true,
                started,
                json!({ "protocol": protocol, "serverVersion": version, "pid": pid, "serverPid": server_pid }),
            ));
            (Some(*pid), *server_pid)
        }
        other => {
            steps.push(step("hello", false, started, json!({ "status": other })));
            (None, None)
        }
    };

    if pid.is_some() {
        let started = Instant::now();
        let answer = supervisor.call("capture.dashboard", None).await;
        let detail = match &answer {
            Ok(result) => json!({
                "answer": "result",
                "exitCode": result["exitCode"],
                "schemaVersion": result["data"]["schemaVersion"],
                "configured": result["data"]["configured"],
            }),
            Err(error) => json!({ "answer": "error", "code": error.code, "message": error.message }),
        };
        // A JSON-RPC error from the command still crossed the bridge both ways.
        let crossed = answer.is_ok() || answer.as_ref().is_err_and(|error| error.code > -32010 || error.code < -32013);
        steps.push(step("capture.dashboard", crossed, started, detail));
    }

    let started = Instant::now();
    supervisor.shutdown().await;
    let pids: Vec<u32> = pid.into_iter().chain(server_pid).collect();
    let left = tokio::task::spawn_blocking(move || wait_gone(&pids, Duration::from_secs(5))).await.unwrap_or_default();
    steps.push(step(
        "shutdown",
        left.is_empty(),
        started,
        json!({ "processesLeft": left.len(), "finalStatus": supervisor.status() }),
    ));

    let ok = steps.iter().all(|s| s.ok);
    let report = json!({
        "backend": { "kind": kind },
        "notifications": sink.notifications.load(Ordering::Relaxed),
        "steps": steps,
        "elapsedMs": begun.elapsed().as_millis(),
    });
    (ok, report)
}

/// Run the self-check and return the process exit code.
pub fn run() -> i32 {
    let runtime = match tokio::runtime::Builder::new_multi_thread().worker_threads(2).enable_all().build() {
        Ok(runtime) => runtime,
        Err(error) => {
            println!("{}", json!({ "selfCheck": "mynotes-desktop", "ok": false, "error": error.to_string() }));
            return 1;
        }
    };
    let (ok, mut report) = runtime.block_on(checks());
    if let Value::Object(map) = &mut report {
        map.insert("selfCheck".into(), json!("mynotes-desktop"));
        map.insert("version".into(), json!(env!("CARGO_PKG_VERSION")));
        map.insert("platform".into(), json!(std::env::consts::OS));
        map.insert("ok".into(), json!(ok));
    }
    println!("{report}");
    i32::from(!ok)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn requested_by_flag_or_environment() {
        assert!(requested(&["mynotes-desktop", "--self-check"], None));
        assert!(requested(&["mynotes-desktop"], Some("1")));
        assert!(!requested(&["mynotes-desktop"], Some("0")));
        assert!(!requested(&["mynotes-desktop", "--action", "show"], None));
    }

    #[cfg(unix)]
    #[test]
    fn this_process_is_alive_and_an_unused_pid_is_not() {
        assert!(process_alive(std::process::id()));
        assert!(!process_alive(u32::MAX / 2));
    }
}

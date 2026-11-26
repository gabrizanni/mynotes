//! Supervising the `mynotes serve --stdio` child process.
//!
//! One background task owns the child and walks this state machine (see [`BackendStatus`]):
//!
//! ```text
//! idle → starting → ready ──(exit, ping timeout)──→ restarting → starting …
//!          │                                            │
//!          └── protocol mismatch / not installed ──→ failed ←── 5 crashes in 2 min
//! any state ── shutdown() ──→ stopped
//! ```
//!
//! - **Spawn.** The command comes from a [`LaunchFn`] resolved at every (re)start, so a runtime
//!   installed while the app runs is picked up. Unix: own process group (`process_group(0)`),
//!   signalled with `killpg`. Windows: a Job Object with kill-on-close, so the whole tree dies
//!   with the app even after a crash.
//! - **Handshake.** `system.hello {protocol: 1}` within [`SupervisorConfig::hello_timeout`]; a
//!   different protocol (or `-32005`) is a permanent failure, not a crash loop.
//! - **Health.** `system.ping` every 15 s; no answer within the ping timeout kills the process
//!   group and counts as a crash.
//! - **Restart.** Backoff 0.5 s doubling to 30 s ([`restart_delay`]); 5 crashes within 2 minutes
//!   stop the loop in `failed` with the last stderr lines.
//! - **Calls.** Each call waits (bounded) for a ready backend, then for its own answer
//!   ([`super::methods::call_timeout`]). When the process dies every pending call fails with
//!   `BACKEND_RESTARTED`.
//! - **Shutdown.** Close stdin (the server's documented stop signal: running requests finish),
//!   wait [`SupervisorConfig::shutdown_grace`], then SIGTERM and SIGKILL the group.
//!
//! The supervisor knows nothing about Tauri: events leave through an [`EventSink`], which the
//! app implements with Tauri events and the tests with channels.

use std::collections::{HashMap, VecDeque};
use std::ffi::OsString;
use std::path::PathBuf;
use std::process::Stdio;
use std::sync::atomic::{AtomicBool, AtomicI64, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use serde::Serialize;
use serde_json::{json, Value};
use tauri_plugin_log::log;
use tokio::io::{AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::process::{Child, ChildStdin, ChildStdout, Command};
use tokio::sync::{mpsc, oneshot, watch};

use super::methods::call_timeout;
use super::protocol::{
    check_hello, codes, encode, parse_incoming, HelloResult, Incoming, LineDecoder, Notification, Request, RequestId,
    RpcError, PROTOCOL_VERSION,
};

/// Backoff before restart number `consecutive_crashes + 1`: 0.5 s doubling, capped at 30 s.
pub fn restart_delay(consecutive_crashes: u32) -> Duration {
    let millis = 500u64.saturating_mul(1u64 << consecutive_crashes.min(6));
    Duration::from_millis(millis.min(30_000))
}

/// Crashes tolerated inside [`CRASH_WINDOW`] before the app stops restarting.
pub const MAX_CRASHES: usize = 5;
pub const CRASH_WINDOW: Duration = Duration::from_secs(120);
pub const PING_INTERVAL: Duration = Duration::from_secs(15);
/// Stderr lines kept for the failure screen.
pub const STDERR_TAIL_LINES: usize = 40;
const STDERR_LINE_MAX_CHARS: usize = 500;
/// Invalid lines on stdout logged per process before the log goes quiet.
const MAX_LOGGED_PROTOCOL_ERRORS: u32 = 20;

/// What to spawn. Resolved again before every start.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Launch {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    /// Variables added to (or replacing) the inherited environment, such as `PATH`.
    pub env: Vec<(OsString, OsString)>,
    pub cwd: Option<PathBuf>,
    /// Short description for logs and the self-check (`interpreter`, `uv-project`, `test`).
    pub kind: String,
}

/// Why no command could be resolved; `code` reaches the webview (`NOT_INSTALLED` → setup).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LaunchError {
    pub code: String,
    pub message: String,
}

pub type LaunchFn = Arc<dyn Fn() -> Result<Launch, LaunchError> + Send + Sync>;

/// Receives everything the supervisor reports. Implementations must not block.
pub trait EventSink: Send + Sync + 'static {
    fn notification(&self, method: &str, params: &Value);
    fn status(&self, status: &BackendStatus);
}

/// Lifecycle of the backend, emitted as `backend://status` and returned by `backend_status`.
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(tag = "state", rename_all = "camelCase", rename_all_fields = "camelCase")]
pub enum BackendStatus {
    /// Not started yet.
    Idle,
    Starting {
        attempt: u32,
    },
    Ready {
        pid: u32,
        server_pid: Option<u32>,
        protocol: u32,
        version: Option<String>,
        generation: u64,
    },
    Restarting {
        attempt: u32,
        delay_ms: u64,
        reason: String,
    },
    /// The loop gave up; `backend_restart` starts it again.
    Failed {
        code: String,
        message: String,
        crashes: usize,
        stderr_tail: Vec<String>,
    },
    Stopped,
}

impl BackendStatus {
    fn is_terminal(&self) -> bool {
        matches!(self, Self::Failed { .. } | Self::Stopped | Self::Idle)
    }
}

/// Failure codes carried by [`BackendStatus::Failed`].
pub mod failure {
    pub const PROTOCOL_MISMATCH: &str = "PROTOCOL_MISMATCH";
    pub const CRASH_LOOP: &str = "CRASH_LOOP";
    /// No Tokio runtime to run the supervision loop on (a programming error, never a panic).
    pub const NO_RUNTIME: &str = "NO_RUNTIME";
}

#[derive(Debug, Clone)]
pub struct SupervisorConfig {
    /// `uv run` may sync the environment on its first start, so this is generous.
    pub hello_timeout: Duration,
    pub ping_interval: Duration,
    pub ping_timeout: Duration,
    /// Time between closing stdin and signalling the process group.
    pub shutdown_grace: Duration,
    /// How long a call waits for a starting or restarting backend.
    pub ready_wait: Duration,
    pub max_crashes: usize,
    pub crash_window: Duration,
    pub backoff: fn(u32) -> Duration,
    /// Overrides [`call_timeout`] for every method (tests).
    pub call_timeout_override: Option<Duration>,
}

impl Default for SupervisorConfig {
    fn default() -> Self {
        Self {
            hello_timeout: Duration::from_secs(60),
            ping_interval: PING_INTERVAL,
            ping_timeout: Duration::from_secs(10),
            shutdown_grace: Duration::from_secs(5),
            ready_wait: Duration::from_secs(90),
            max_crashes: MAX_CRASHES,
            crash_window: CRASH_WINDOW,
            backoff: restart_delay,
            call_timeout_override: None,
        }
    }
}

type Reply = oneshot::Sender<Result<Value, RpcError>>;

/// One live child process: its stdin queue and the calls waiting for an answer.
struct Connection {
    generation: u64,
    pid: u32,
    writer: mpsc::UnboundedSender<Vec<u8>>,
    pending: Mutex<HashMap<i64, Reply>>,
    closed: AtomicBool,
}

impl Connection {
    fn register(&self, id: i64, reply: Reply) -> Result<(), RpcError> {
        let mut pending = self.pending.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if self.closed.load(Ordering::SeqCst) {
            return Err(restarted());
        }
        pending.insert(id, reply);
        Ok(())
    }

    fn forget(&self, id: i64) {
        self.pending.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).remove(&id);
    }

    fn resolve(&self, id: i64, outcome: Result<Value, RpcError>) -> bool {
        let reply = self.pending.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).remove(&id);
        match reply {
            Some(reply) => {
                let _ = reply.send(outcome);
                true
            }
            None => false,
        }
    }

    /// Mark the connection dead and fail every waiting call with `BACKEND_RESTARTED`.
    fn fail_all(&self) -> usize {
        let drained: Vec<Reply> = {
            let mut pending = self.pending.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
            self.closed.store(true, Ordering::SeqCst);
            pending.drain().map(|(_, reply)| reply).collect()
        };
        let count = drained.len();
        for reply in drained {
            let _ = reply.send(Err(restarted()));
        }
        count
    }

    fn send_bytes(&self, bytes: Vec<u8>) -> Result<(), RpcError> {
        self.writer.send(bytes).map_err(|_| restarted())
    }
}

fn restarted() -> RpcError {
    RpcError::new(codes::BACKEND_RESTARTED, "MyNotes restarted while this request was running")
}

fn unavailable(detail: &str) -> RpcError {
    RpcError::new(codes::BACKEND_UNAVAILABLE, format!("MyNotes is not running ({detail})"))
}

struct Inner {
    config: SupervisorConfig,
    launch: LaunchFn,
    sink: Arc<dyn EventSink>,
    link: watch::Sender<Option<Arc<Connection>>>,
    status: watch::Sender<BackendStatus>,
    stopping: watch::Sender<bool>,
    next_id: AtomicI64,
    generation: AtomicU64,
    stderr_tail: Mutex<VecDeque<String>>,
    task: Mutex<Option<tokio::task::JoinHandle<()>>>,
    /// Where the supervision loop runs. [`Supervisor::start`] is called from Tauri's main
    /// thread (sync commands) and from the installer's plain thread, outside any runtime.
    runtime: Option<tokio::runtime::Handle>,
}

/// Handle to the supervised backend; cheap to clone.
#[derive(Clone)]
pub struct Supervisor {
    inner: Arc<Inner>,
}

/// How one child process ended.
enum Outcome {
    Shutdown,
    Crashed(String),
    Fatal { code: &'static str, message: String },
}

impl Supervisor {
    /// A supervisor whose loop runs on the runtime current at construction, if any.
    pub fn new(config: SupervisorConfig, launch: LaunchFn, sink: Arc<dyn EventSink>) -> Self {
        Self::build(tokio::runtime::Handle::try_current().ok(), config, launch, sink)
    }

    /// A supervisor whose loop always runs on `runtime`, whichever thread calls
    /// [`Supervisor::start`] or [`Supervisor::restart`]. The app passes Tauri's runtime.
    pub fn on_runtime(
        runtime: tokio::runtime::Handle,
        config: SupervisorConfig,
        launch: LaunchFn,
        sink: Arc<dyn EventSink>,
    ) -> Self {
        Self::build(Some(runtime), config, launch, sink)
    }

    fn build(
        runtime: Option<tokio::runtime::Handle>,
        config: SupervisorConfig,
        launch: LaunchFn,
        sink: Arc<dyn EventSink>,
    ) -> Self {
        let (link, _) = watch::channel(None);
        let (status, _) = watch::channel(BackendStatus::Idle);
        let (stopping, _) = watch::channel(false);
        Self {
            inner: Arc::new(Inner {
                config,
                launch,
                sink,
                link,
                status,
                stopping,
                next_id: AtomicI64::new(1),
                generation: AtomicU64::new(0),
                stderr_tail: Mutex::new(VecDeque::new()),
                task: Mutex::new(None),
                runtime,
            }),
        }
    }

    /// Start the supervision loop unless it is already running. Safe from any thread: the loop
    /// runs on the supervisor's runtime, else on the caller's; with neither, the status turns
    /// `failed` instead of panicking (the release build aborts on panic).
    pub fn start(&self) {
        let mut task = self.inner.task.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if task.as_ref().is_some_and(|handle| !handle.is_finished()) {
            return;
        }
        let Some(runtime) = self.inner.runtime.clone().or_else(|| tokio::runtime::Handle::try_current().ok()) else {
            log::error!("[Supervisor] Start refused — {{ reason: no async runtime }}");
            self.inner.set_status(BackendStatus::Failed {
                code: failure::NO_RUNTIME.into(),
                message: "MyNotes could not start its backend loop (no async runtime)".into(),
                crashes: 0,
                stderr_tail: Vec::new(),
            });
            return;
        };
        self.inner.stopping.send_replace(false);
        // Visible at once, so a call made right after `start` waits instead of failing.
        self.inner.set_status(BackendStatus::Starting { attempt: 0 });
        let inner = Arc::clone(&self.inner);
        *task = Some(runtime.spawn(async move { inner.run().await }));
    }

    /// Start again after `failed` (or `stopped`): crash counters begin from zero.
    pub fn restart(&self) {
        log::info!("[Supervisor] Restart requested — {{ status: {:?} }}", self.status());
        self.start();
    }

    pub fn status(&self) -> BackendStatus {
        self.inner.status.borrow().clone()
    }

    /// Wait until the backend is ready or the loop gave up; returns the status reached.
    pub async fn wait_ready(&self, limit: Duration) -> BackendStatus {
        let mut status = self.inner.status.subscribe();
        let reached = tokio::time::timeout(limit, async {
            loop {
                let current = status.borrow_and_update().clone();
                if matches!(
                    current,
                    BackendStatus::Ready { .. } | BackendStatus::Failed { .. } | BackendStatus::Stopped
                ) {
                    return current;
                }
                if status.changed().await.is_err() {
                    return BackendStatus::Stopped;
                }
            }
        })
        .await;
        reached.unwrap_or_else(|_| self.status())
    }

    /// Send one request and wait for its answer.
    pub async fn call(&self, method: &str, params: Option<Value>) -> Result<Value, RpcError> {
        let connection = self.connection().await?;
        let limit = self.inner.config.call_timeout_override.unwrap_or_else(|| call_timeout(method, params.as_ref()));
        self.inner.call_on(&connection, method, params, limit).await
    }

    /// Stop the backend and wait until its process group is gone.
    pub async fn shutdown(&self) {
        log::info!("[Supervisor] Shutdown requested");
        self.inner.stopping.send_replace(true);
        let handle = self.inner.task.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).take();
        if let Some(handle) = handle {
            let _ = handle.await;
        }
        self.inner.set_status(BackendStatus::Stopped);
    }

    /// The live connection, waiting (bounded) while the backend starts or restarts.
    async fn connection(&self) -> Result<Arc<Connection>, RpcError> {
        let mut link = self.inner.link.subscribe();
        let mut status = self.inner.status.subscribe();
        let wait = async {
            loop {
                if let Some(connection) = link.borrow_and_update().clone() {
                    return Ok(connection);
                }
                let current = status.borrow_and_update().clone();
                if current.is_terminal() {
                    return Err(unavailable(&describe(&current)));
                }
                tokio::select! {
                    changed = link.changed() => if changed.is_err() { return Err(unavailable("shutting down")) },
                    changed = status.changed() => if changed.is_err() { return Err(unavailable("shutting down")) },
                }
            }
        };
        tokio::time::timeout(self.inner.config.ready_wait, wait)
            .await
            .unwrap_or_else(|_| Err(unavailable("still starting")))
    }
}

fn describe(status: &BackendStatus) -> String {
    match status {
        BackendStatus::Idle => "not started".into(),
        BackendStatus::Failed { code, message, .. } => format!("{code}: {message}"),
        BackendStatus::Stopped => "stopped".into(),
        other => format!("{other:?}"),
    }
}

impl Inner {
    fn set_status(&self, status: BackendStatus) {
        let changed = *self.status.borrow() != status;
        if changed {
            self.status.send_replace(status.clone());
            self.sink.status(&status);
        }
    }

    fn is_stopping(&self) -> bool {
        *self.stopping.borrow()
    }

    fn stderr_tail(&self) -> Vec<String> {
        self.stderr_tail.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).iter().cloned().collect()
    }

    async fn call_on(
        &self,
        connection: &Connection,
        method: &str,
        params: Option<Value>,
        limit: Duration,
    ) -> Result<Value, RpcError> {
        let id = self.next_id.fetch_add(1, Ordering::SeqCst);
        let bytes = encode(&Request::new(id, method, params))
            .map_err(|error| RpcError::new(codes::INVALID_PARAMS, error.to_string()))?;
        let (reply, answer) = oneshot::channel();
        connection.register(id, reply)?;
        if let Err(error) = connection.send_bytes(bytes) {
            connection.forget(id);
            return Err(error);
        }
        match tokio::time::timeout(limit, answer).await {
            Ok(Ok(outcome)) => outcome,
            // The connection dropped the reply: the process went away.
            Ok(Err(_)) => Err(restarted()),
            Err(_) => {
                connection.forget(id);
                // Best effort: a request that has not started yet is dropped by the server.
                if let Ok(cancel) = encode(&Notification::new("$/cancelRequest", Some(json!({ "id": id })))) {
                    let _ = connection.send_bytes(cancel);
                }
                log::warn!(
                    "[Supervisor] Call timed out — {{ method: {method}, id: {id}, limit_s: {} }}",
                    limit.as_secs()
                );
                Err(RpcError::new(codes::TIMEOUT, format!("{method} did not answer within {} s", limit.as_secs())))
            }
        }
    }

    async fn run(self: Arc<Self>) {
        let mut crashes: VecDeque<Instant> = VecDeque::new();
        let mut attempt: u32 = 0;
        self.stderr_tail.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).clear();
        loop {
            if self.is_stopping() {
                break;
            }
            attempt += 1;
            self.set_status(BackendStatus::Starting { attempt });
            // Resolving may ask the login shell for PATH: keep it off the async workers.
            let launcher = Arc::clone(&self.launch);
            let resolved = tokio::task::spawn_blocking(move || launcher()).await.unwrap_or_else(|error| {
                Err(LaunchError { code: "INTERNAL".into(), message: format!("launch lookup panicked: {error}") })
            });
            let launch = match resolved {
                Ok(launch) => launch,
                Err(error) => {
                    log::warn!(
                        "[Supervisor] No backend to start — {{ code: {}, message: {} }}",
                        error.code,
                        error.message
                    );
                    self.set_status(BackendStatus::Failed {
                        code: error.code,
                        message: error.message,
                        crashes: crashes.len(),
                        stderr_tail: Vec::new(),
                    });
                    return;
                }
            };
            match self.run_once(launch).await {
                Outcome::Shutdown => break,
                Outcome::Fatal { code, message } => {
                    log::error!("[Supervisor] Backend refused — {{ code: {code}, message: {message} }}");
                    self.set_status(BackendStatus::Failed {
                        code: code.into(),
                        message,
                        crashes: crashes.len(),
                        stderr_tail: self.stderr_tail(),
                    });
                    return;
                }
                Outcome::Crashed(reason) => {
                    let now = Instant::now();
                    crashes.push_back(now);
                    while crashes.front().is_some_and(|at| now.duration_since(*at) > self.config.crash_window) {
                        crashes.pop_front();
                    }
                    log::warn!(
                        "[Supervisor] Backend crashed — {{ reason: {reason}, crashes_in_window: {} }}",
                        crashes.len()
                    );
                    if crashes.len() >= self.config.max_crashes {
                        self.set_status(BackendStatus::Failed {
                            code: failure::CRASH_LOOP.into(),
                            message: format!(
                                "MyNotes stopped {} times in {} s; last: {reason}",
                                crashes.len(),
                                self.config.crash_window.as_secs()
                            ),
                            crashes: crashes.len(),
                            stderr_tail: self.stderr_tail(),
                        });
                        return;
                    }
                    let delay = (self.config.backoff)(crashes.len().saturating_sub(1) as u32);
                    self.set_status(BackendStatus::Restarting { attempt, delay_ms: delay.as_millis() as u64, reason });
                    let mut stopping = self.stopping.subscribe();
                    tokio::select! {
                        _ = tokio::time::sleep(delay) => {}
                        _ = until_stopped(&mut stopping) => break,
                    }
                }
            }
        }
        self.set_status(BackendStatus::Stopped);
    }

    /// Spawn one child, handshake, serve until it exits or the app stops.
    async fn run_once(self: &Arc<Self>, launch: Launch) -> Outcome {
        let started = Instant::now();
        let mut command = Command::new(&launch.program);
        command
            .args(&launch.args)
            .envs(launch.env.iter().map(|(key, value)| (key, value)))
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true);
        if let Some(cwd) = &launch.cwd {
            command.current_dir(cwd);
        }
        process_tree::configure(&mut command);
        let mut child = match command.spawn() {
            Ok(child) => child,
            Err(error) => return Outcome::Crashed(format!("could not start {}: {error}", launch.program.display())),
        };
        let pid = child.id().unwrap_or_default();
        let tree = match process_tree::adopt(&child) {
            Ok(tree) => tree,
            Err(error) => {
                let _ = child.start_kill();
                let _ = child.wait().await;
                return Outcome::Crashed(format!("could not contain the backend process: {error}"));
            }
        };
        let generation = self.generation.fetch_add(1, Ordering::SeqCst) + 1;
        log::info!("[Supervisor] Backend spawned — {{ pid: {pid}, kind: {}, generation: {generation} }}", launch.kind);

        let (writer, queue) = mpsc::unbounded_channel();
        let connection = Arc::new(Connection {
            generation,
            pid,
            writer,
            pending: Mutex::new(HashMap::new()),
            closed: AtomicBool::new(false),
        });
        let (stdin, stdout, stderr) = (child.stdin.take(), child.stdout.take(), child.stderr.take());
        let (Some(stdin), Some(stdout), Some(stderr)) = (stdin, stdout, stderr) else {
            let _ = child.start_kill();
            let _ = child.wait().await;
            return Outcome::Crashed("the backend's standard streams were not available".into());
        };
        let (close_stdin, close_signal) = oneshot::channel::<()>();
        let writer_task = tokio::spawn(write_loop(stdin, queue, close_signal));
        let reader_task = tokio::spawn(read_loop(Arc::clone(self), Arc::clone(&connection), stdout));
        let stderr_task = tokio::spawn(stderr_loop(Arc::clone(self), stderr));

        let outcome = self.serve(&connection, &mut child, started).await;
        let stopping = matches!(outcome, Outcome::Shutdown);
        self.link.send_replace(None);
        let failed = connection.fail_all();
        if failed > 0 {
            log::warn!("[Supervisor] Pending calls failed — {{ count: {failed}, generation: {generation} }}");
        }
        // Close stdin first: the documented way to stop `mynotes serve`.
        let _ = close_stdin.send(());
        stop_child(&mut child, &tree, if stopping { self.config.shutdown_grace } else { Duration::ZERO }).await;
        let _ = writer_task.await;
        let _ = reader_task.await;
        let _ = stderr_task.await;
        log::info!(
            "[Supervisor] Backend gone — {{ pid: {pid}, generation: {generation}, lived_ms: {} }}",
            started.elapsed().as_millis()
        );
        outcome
    }

    /// Handshake, then watch the child, the pings and the stop flag.
    async fn serve(&self, connection: &Arc<Connection>, child: &mut Child, started: Instant) -> Outcome {
        let mut stopping = self.stopping.subscribe();
        let hello = tokio::select! {
            hello = self.call_on(connection, "system.hello", Some(json!({ "protocol": PROTOCOL_VERSION })), self.config.hello_timeout) => hello,
            status = child.wait() => return Outcome::Crashed(format!("exited during the handshake ({})", exit_text(status))),
            _ = until_stopped(&mut stopping) => return Outcome::Shutdown,
        };
        let hello = match hello {
            Ok(value) => value,
            Err(error) if error.code == codes::UNSUPPORTED_PROTOCOL => {
                return Outcome::Fatal { code: failure::PROTOCOL_MISMATCH, message: error.message }
            }
            // The output closed first; the exit status says more than the lost reply.
            Err(error) if error.code == codes::BACKEND_RESTARTED => {
                return match tokio::time::timeout(Duration::from_secs(2), child.wait()).await {
                    Ok(status) => Outcome::Crashed(format!("exited during the handshake ({})", exit_text(status))),
                    Err(_) => Outcome::Crashed("closed its output during the handshake".into()),
                };
            }
            Err(error) => return Outcome::Crashed(format!("handshake failed: {}", error.message)),
        };
        let parsed: HelloResult = match serde_json::from_value(hello) {
            Ok(parsed) => parsed,
            Err(error) => return Outcome::Crashed(format!("handshake answer is not system.hello: {error}")),
        };
        if let Err(error) = check_hello(&parsed) {
            return Outcome::Fatal { code: failure::PROTOCOL_MISMATCH, message: error.to_string() };
        }
        let server_pid = parsed.extra.get("pid").and_then(Value::as_u64).map(|pid| pid as u32);
        let version = parsed.extra.get("version").and_then(Value::as_str).map(str::to_owned);
        log::info!(
            "[Supervisor] Backend ready — {{ pid: {}, server_pid: {server_pid:?}, version: {version:?}, startup_ms: {} }}",
            connection.pid,
            started.elapsed().as_millis()
        );
        self.link.send_replace(Some(Arc::clone(connection)));
        self.set_status(BackendStatus::Ready {
            pid: connection.pid,
            server_pid,
            protocol: parsed.protocol,
            version,
            generation: connection.generation,
        });

        let mut ping = tokio::time::interval_at(
            tokio::time::Instant::now() + self.config.ping_interval,
            self.config.ping_interval,
        );
        ping.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Delay);
        loop {
            tokio::select! {
                status = child.wait() => {
                    return Outcome::Crashed(format!("exited ({})", exit_text(status)));
                }
                _ = until_stopped(&mut stopping) => return Outcome::Shutdown,
                _ = ping.tick() => {
                    let pinged = self.call_on(connection, "system.ping", None, self.config.ping_timeout).await;
                    match pinged {
                        Ok(_) => log::debug!("[Supervisor] Ping ok — {{ generation: {} }}", connection.generation),
                        Err(error) if error.code == codes::TIMEOUT => {
                            return Outcome::Crashed(format!("no answer to ping within {} s", self.config.ping_timeout.as_secs()));
                        }
                        // The process is going away; `child.wait()` reports how.
                        Err(_) => {}
                    }
                }
            }
        }
    }
}

/// Resolves once the stop flag is set (or its sender is gone); holds no guard across awaits.
async fn until_stopped(stopping: &mut watch::Receiver<bool>) {
    let _ = stopping.wait_for(|stop| *stop).await;
}

fn exit_text(status: std::io::Result<std::process::ExitStatus>) -> String {
    match status {
        Ok(status) => status.to_string(),
        Err(error) => format!("wait failed: {error}"),
    }
}

async fn write_loop(
    mut stdin: ChildStdin,
    mut queue: mpsc::UnboundedReceiver<Vec<u8>>,
    mut close: oneshot::Receiver<()>,
) {
    loop {
        tokio::select! {
            biased;
            _ = &mut close => break,
            message = queue.recv() => {
                let Some(bytes) = message else { break };
                if stdin.write_all(&bytes).await.is_err() || stdin.flush().await.is_err() {
                    break;
                }
            }
        }
    }
    // Dropping stdin sends end-of-file: the server finishes running requests and exits.
    let _ = stdin.shutdown().await;
}

async fn read_loop(inner: Arc<Inner>, connection: Arc<Connection>, mut stdout: ChildStdout) {
    let mut decoder = LineDecoder::default();
    let mut buffer = vec![0u8; 64 * 1024];
    let mut logged_errors = 0u32;
    loop {
        let read = match stdout.read(&mut buffer).await {
            Ok(0) | Err(_) => break,
            Ok(read) => read,
        };
        for line in decoder.push(&buffer[..read]) {
            let parsed = line.and_then(|bytes| parse_incoming(&bytes));
            match parsed {
                Ok(Incoming::Response(response)) => route_response(&connection, response),
                Ok(Incoming::Notification(notification)) => {
                    let params = notification.params.unwrap_or(Value::Null);
                    inner.sink.notification(&notification.method, &params);
                }
                Err(error) => {
                    logged_errors += 1;
                    if logged_errors <= MAX_LOGGED_PROTOCOL_ERRORS {
                        log::warn!("[Supervisor] Invalid line from backend skipped — {{ error: {error} }}");
                    }
                }
            }
        }
    }
    connection.fail_all();
}

fn route_response(connection: &Connection, response: super::protocol::Response) {
    let Some(RequestId::Number(id)) = response.id.clone() else {
        // `id: null` answers a line the server could not read; nothing waits for it.
        log::warn!("[Supervisor] Response without a usable id — {{ id: {:?} }}", response.id);
        return;
    };
    if !connection.resolve(id, response.into_result()) {
        log::debug!("[Supervisor] Late response dropped — {{ id: {id} }}");
    }
}

async fn stderr_loop(inner: Arc<Inner>, stderr: tokio::process::ChildStderr) {
    let mut lines = BufReader::new(stderr).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        let line: String = line.chars().take(STDERR_LINE_MAX_CHARS).collect();
        log::info!("[Backend] {line}");
        let mut tail = inner.stderr_tail.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if tail.len() == STDERR_TAIL_LINES {
            tail.pop_front();
        }
        tail.push_back(line);
    }
}

/// Give the child `grace` to exit on its own (stdin is closed), then terminate its whole tree.
async fn stop_child(child: &mut Child, tree: &process_tree::Tree, grace: Duration) {
    if !grace.is_zero() && tokio::time::timeout(grace, child.wait()).await.is_ok() {
        // The server exited; anything it left in its group goes too.
        tree.kill();
        return;
    }
    tree.terminate();
    if tokio::time::timeout(Duration::from_secs(2), child.wait()).await.is_err() {
        log::warn!("[Supervisor] Backend ignored termination; killing its process tree");
    }
    tree.kill();
    let _ = child.start_kill();
    let _ = child.wait().await;
}

/// Process-tree containment: a process group on Unix, a Job Object on Windows.
mod process_tree {
    use tokio::process::{Child, Command};

    #[cfg(unix)]
    pub struct Tree {
        pgid: Option<libc::pid_t>,
    }

    #[cfg(unix)]
    pub fn configure(command: &mut Command) {
        // The child leads a new group, so `killpg` reaches it and every child it starts.
        command.process_group(0);
    }

    #[cfg(unix)]
    pub fn adopt(child: &Child) -> std::io::Result<Tree> {
        let pgid = child.id().and_then(|pid| libc::pid_t::try_from(pid).ok());
        Ok(Tree { pgid })
    }

    #[cfg(unix)]
    impl Tree {
        fn signal(&self, signal: libc::c_int) {
            if let Some(pgid) = self.pgid.filter(|pgid| *pgid > 1) {
                // SAFETY: killpg only sends a signal; a negative or stale group fails with ESRCH.
                unsafe {
                    libc::killpg(pgid, signal);
                }
            }
        }

        pub fn terminate(&self) {
            self.signal(libc::SIGTERM);
        }

        pub fn kill(&self) {
            self.signal(libc::SIGKILL);
        }
    }

    #[cfg(windows)]
    pub struct Tree {
        job: windows_sys::Win32::Foundation::HANDLE,
    }

    // SAFETY: a job handle is a kernel object handle, usable from any thread.
    #[cfg(windows)]
    unsafe impl Send for Tree {}
    #[cfg(windows)]
    unsafe impl Sync for Tree {}

    #[cfg(windows)]
    pub fn configure(command: &mut Command) {
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        command.creation_flags(CREATE_NO_WINDOW);
    }

    #[cfg(windows)]
    pub fn adopt(child: &Child) -> std::io::Result<Tree> {
        use windows_sys::Win32::System::JobObjects::{
            AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
            JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
        };
        let process = child.raw_handle().ok_or_else(|| std::io::Error::other("the backend already exited"))?;
        // SAFETY: plain Win32 calls on handles owned by this function or by `child`.
        unsafe {
            let job = CreateJobObjectW(std::ptr::null(), std::ptr::null());
            if job.is_null() {
                return Err(std::io::Error::last_os_error());
            }
            let tree = Tree { job };
            let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = std::mem::zeroed();
            limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
            let size = std::mem::size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32;
            if SetInformationJobObject(job, JobObjectExtendedLimitInformation, std::ptr::addr_of!(limits).cast(), size)
                == 0
            {
                return Err(std::io::Error::last_os_error());
            }
            // The child runs for a moment before this call; it starts nothing that early.
            if AssignProcessToJobObject(job, process as _) == 0 {
                return Err(std::io::Error::last_os_error());
            }
            Ok(tree)
        }
    }

    #[cfg(windows)]
    impl Tree {
        pub fn terminate(&self) {
            self.kill();
        }

        pub fn kill(&self) {
            use windows_sys::Win32::System::JobObjects::TerminateJobObject;
            // SAFETY: the job handle stays open until `drop`.
            unsafe {
                TerminateJobObject(self.job, 1);
            }
        }
    }

    #[cfg(windows)]
    impl Drop for Tree {
        fn drop(&mut self) {
            use windows_sys::Win32::Foundation::CloseHandle;
            // SAFETY: closing our own handle; kill-on-close ends any process still in the job.
            unsafe {
                CloseHandle(self.job);
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_doubles_from_half_a_second_and_caps_at_thirty() {
        assert_eq!(restart_delay(0), Duration::from_millis(500));
        assert_eq!(restart_delay(1), Duration::from_secs(1));
        assert_eq!(restart_delay(4), Duration::from_secs(8));
        assert_eq!(restart_delay(6), Duration::from_secs(30));
        assert_eq!(restart_delay(40), Duration::from_secs(30));
    }

    #[test]
    fn statuses_serialize_with_a_state_tag_in_camel_case() {
        let status = BackendStatus::Restarting { attempt: 2, delay_ms: 500, reason: "exited".into() };
        assert_eq!(
            serde_json::to_value(&status).unwrap(),
            json!({"state": "restarting", "attempt": 2, "delayMs": 500, "reason": "exited"})
        );
        let failed =
            BackendStatus::Failed { code: "CRASH_LOOP".into(), message: "m".into(), crashes: 5, stderr_tail: vec![] };
        assert_eq!(serde_json::to_value(&failed).unwrap()["stderrTail"], json!([]));
    }
}

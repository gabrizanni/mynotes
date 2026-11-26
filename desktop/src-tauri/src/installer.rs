//! First-run installer: detect a missing runtime and run `setup_runtime.py` with live progress.
//!
//! The webview sees two commands and one event, with the shapes of `src/rpc/setup.ts`:
//! - `setup_status` → [`SetupStatus`] (`setupStatusSchema` in `src/rpc/schema.ts`, unknown fields
//!   pass through);
//! - `setup_run {request: {provider, workspace?}}` → [`SetupStatus`] once the script started;
//! - `setup://progress` → [`SetupStatus`] for every progress line the script prints.
//!
//! Where the release payload comes from:
//! - **Linux:** the Linux payload of `scripts/build_linux_payload.py` (docs/LINUX.md, "Install
//!   from the Linux payload"), extracted. `MYNOTES_DESKTOP_PAYLOAD` points to it; a bundle that
//!   ships it puts it under `<resources>/payload`. The script runs with a system Python 3.11+
//!   (`python3.12`, `python3.13`, `python3`, or `MYNOTES_DESKTOP_SETUP_PYTHON`).
//! - **macOS:** the same flow with the macOS release root under `<resources>/payload`, as the
//!   native setup app does. Not bundled by this crate yet: without it `setup_run` answers
//!   `PAYLOAD_MISSING`.
//! - **Windows:** the runtime comes from the Inno Setup installer; `setup_run` answers
//!   `EXTERNAL_INSTALLER` and the UI points the student there.
//!
//! `setup_runtime.py` prints one JSON object per line (`stage`, `message`, `completed`, `total`,
//! `phase`, `downloaded_bytes`, …) and ends with stage `ready` or `failed`; [`apply_progress`]
//! maps each line onto [`SetupStatus`]. It is resumable and rolls back on SIGTERM, so app exit
//! terminates it ([`Installer::shutdown`]) instead of leaving it running.

use std::ffi::OsString;
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tauri_plugin_log::log;

use crate::backend::locate::{self, Platform};

pub const PAYLOAD_ENV: &str = "MYNOTES_DESKTOP_PAYLOAD";
pub const SETUP_PYTHON_ENV: &str = "MYNOTES_DESKTOP_SETUP_PYTHON";
pub const PROGRESS_EVENT: &str = "setup://progress";
const SCRIPT: &str = "scripts/setup_runtime.py";
const STDERR_TAIL_LINES: usize = 20;
/// Time `setup_runtime.py` gets to roll back after SIGTERM when the app quits.
const SHUTDOWN_GRACE: Duration = Duration::from_secs(10);

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "lowercase")]
pub enum SetupState {
    Idle,
    Running,
    Paused,
    Failed,
    Complete,
}

/// `setupStatusSchema` plus pass-through details (`reason`, `phase`, `component`, …).
#[derive(Debug, Clone, PartialEq, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupStatus {
    pub state: SetupState,
    pub stage: Option<String>,
    pub title: Option<String>,
    pub bytes_done: Option<u64>,
    pub bytes_total: Option<u64>,
    pub last_data_at: Option<String>,
    pub error: Option<String>,
    /// Machine-readable cause for `idle` and `failed` (see [`SetupError::code`]).
    #[serde(skip_serializing_if = "Option::is_none")]
    pub reason: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub phase: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub component: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub completed: Option<u64>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub total: Option<u64>,
    pub platform: &'static str,
}

impl SetupStatus {
    fn new(state: SetupState) -> Self {
        Self {
            state,
            stage: None,
            title: None,
            bytes_done: None,
            bytes_total: None,
            last_data_at: None,
            error: None,
            reason: None,
            phase: None,
            component: None,
            completed: None,
            total: None,
            platform: platform_name(),
        }
    }

    fn with_reason(mut self, reason: &str, error: Option<String>) -> Self {
        self.reason = Some(reason.to_owned());
        self.error = error;
        self
    }
}

fn platform_name() -> &'static str {
    match Platform::current() {
        Platform::MacOs => "macos",
        Platform::Linux => "linux",
        Platform::Windows => "windows",
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Deserialize)]
#[serde(rename_all = "lowercase")]
pub enum Provider {
    Codex,
    Agy,
    Claude,
}

impl Provider {
    fn as_str(self) -> &'static str {
        match self {
            Self::Codex => "codex",
            Self::Agy => "agy",
            Self::Claude => "claude",
        }
    }
}

/// `setup_run` input, mirrored by `setupRunRequestSchema` in `src/rpc/setup.ts`.
#[derive(Debug, Clone, Deserialize)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub struct SetupRunRequest {
    pub provider: Provider,
    /// Absolute local folder for the private workspace; the script's default otherwise.
    #[serde(default)]
    pub workspace: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum SetupError {
    #[error("setup is already running")]
    AlreadyRunning,
    #[error("on Windows the MyNotes runtime comes from the MyNotes installer; run it, then reopen MyNotes Desktop")]
    ExternalInstaller,
    #[error("no MyNotes release payload was found (set {PAYLOAD_ENV} to an extracted payload)")]
    PayloadMissing,
    #[error("the release payload at {0} has no {SCRIPT}")]
    PayloadInvalid(PathBuf),
    #[error("setup needs Python 3.11 or newer (python3.12 recommended), or {SETUP_PYTHON_ENV}")]
    PythonMissing,
    #[error("the workspace must be an absolute local folder")]
    InvalidWorkspace,
    #[error("setup could not start: {0}")]
    Spawn(String),
}

impl SetupError {
    pub fn code(&self) -> &'static str {
        match self {
            Self::AlreadyRunning => "ALREADY_RUNNING",
            Self::ExternalInstaller => "EXTERNAL_INSTALLER",
            Self::PayloadMissing => "PAYLOAD_MISSING",
            Self::PayloadInvalid(_) => "PAYLOAD_INVALID",
            Self::PythonMissing => "PYTHON_MISSING",
            Self::InvalidWorkspace => "INVALID_WORKSPACE",
            Self::Spawn(_) => "SPAWN_FAILED",
        }
    }
}

/// `2026-11-26T09:30:00Z` without a date library (days-from-civil, Howard Hinnant).
pub fn iso8601_utc(time: SystemTime) -> String {
    let seconds = time.duration_since(UNIX_EPOCH).map(|d| d.as_secs()).unwrap_or(0);
    let (days, rest) = (seconds / 86_400, seconds % 86_400);
    let z = days as i64 + 719_468;
    let era = z.div_euclid(146_097);
    let day_of_era = z.rem_euclid(146_097);
    let year_of_era = (day_of_era - day_of_era / 1_460 + day_of_era / 36_524 - day_of_era / 146_096) / 365;
    let day_of_year = day_of_era - (365 * year_of_era + year_of_era / 4 - year_of_era / 100);
    let mp = (5 * day_of_year + 2) / 153;
    let day = day_of_year - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = year_of_era + era * 400 + i64::from(month <= 2);
    format!("{year:04}-{month:02}-{day:02}T{:02}:{:02}:{:02}Z", rest / 3_600, rest % 3_600 / 60, rest % 60)
}

fn as_u64(value: Option<&Value>) -> Option<u64> {
    value.and_then(|v| v.as_u64().or_else(|| v.as_f64().filter(|f| *f >= 0.0).map(|f| f as u64)))
}

/// Fold one stdout line of `setup_runtime.py` into `status`. Non-JSON lines are ignored.
pub fn apply_progress(status: &mut SetupStatus, line: &str, now: SystemTime) -> bool {
    let Ok(Value::Object(object)) = serde_json::from_str::<Value>(line.trim()) else {
        return false;
    };
    let Some(stage) = object.get("stage").and_then(Value::as_str) else {
        return false;
    };
    let text = |key: &str| object.get(key).and_then(Value::as_str).map(str::to_owned);
    status.stage = Some(stage.to_owned());
    status.title = text("message");
    status.phase = text("phase");
    status.component = text("component");
    status.completed = as_u64(object.get("completed"));
    status.total = as_u64(object.get("total"));
    let downloading = status.phase.as_deref() == Some("download");
    status.bytes_done = if downloading { as_u64(object.get("downloaded_bytes")) } else { None };
    status.bytes_total = if downloading { as_u64(object.get("total_bytes")) } else { None };
    let data_at = object
        .get("last_data_at")
        .and_then(Value::as_f64)
        .map(|epoch| UNIX_EPOCH + Duration::from_secs_f64(epoch.max(0.0)))
        .unwrap_or(now);
    status.last_data_at = Some(iso8601_utc(data_at));
    match stage {
        "ready" => {
            status.state = SetupState::Complete;
            status.error = None;
            status.reason = None;
        }
        "failed" => {
            status.state = SetupState::Failed;
            status.error = text("error").or_else(|| text("message"));
            status.reason = Some("SETUP_FAILED".into());
        }
        _ => {
            status.state = SetupState::Running;
            status.error = None;
            status.reason = None;
        }
    }
    true
}

/// A resolved `setup_runtime.py` invocation.
#[derive(Debug, Clone)]
pub struct SetupCommand {
    pub program: PathBuf,
    pub args: Vec<OsString>,
    pub env: Vec<(OsString, OsString)>,
}

type Notify = Arc<dyn Fn(&SetupStatus) + Send + Sync>;

#[derive(Default)]
struct Shared {
    running: bool,
    last: Option<SetupStatus>,
    child: Option<u32>,
}

/// First-run setup runner; one script at a time.
#[derive(Clone, Default)]
pub struct Installer {
    shared: Arc<Mutex<Shared>>,
}

impl Installer {
    pub fn new() -> Self {
        Self::default()
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Shared> {
        self.shared.lock().unwrap_or_else(|poisoned| poisoned.into_inner())
    }

    /// Running progress, else the runtime check: complete when Python is found, idle otherwise.
    pub fn status(&self) -> SetupStatus {
        let shared = self.lock();
        if shared.running {
            return shared.last.clone().unwrap_or_else(|| SetupStatus::new(SetupState::Running));
        }
        let last = shared.last.clone();
        drop(shared);
        let path = std::env::var_os("PATH");
        match locate::locate_from_environment(path.as_deref()) {
            Ok(_) => SetupStatus::new(SetupState::Complete),
            Err(error) => {
                if let Some(previous) = last.filter(|s| s.state == SetupState::Failed) {
                    return previous;
                }
                if let Some(previous) = read_progress_file().filter(|s| s.state == SetupState::Failed) {
                    return previous;
                }
                let reason = if error.code() == "NOT_INSTALLED" { "RUNTIME_MISSING" } else { error.code() };
                SetupStatus::new(SetupState::Idle).with_reason(reason, Some(error.to_string()))
            }
        }
    }

    /// Resolve the payload and Python for this platform, then [`Installer::run_with`].
    pub fn run<R: tauri::Runtime>(
        &self,
        app: tauri::AppHandle<R>,
        request: SetupRunRequest,
    ) -> Result<SetupStatus, SetupError> {
        use tauri::{Emitter, Manager};
        if Platform::current() == Platform::Windows {
            return Err(SetupError::ExternalInstaller);
        }
        let resources = app.path().resource_dir().ok();
        let command = resolve_command(&request, resources.as_deref())?;
        let emitter = app.clone();
        let notify: Notify = Arc::new(move |status: &SetupStatus| {
            if let Err(error) = emitter.emit(PROGRESS_EVENT, status) {
                log::warn!("[Installer] Progress event not delivered — {{ error: {error} }}");
            }
            if status.state == SetupState::Complete {
                // The runtime now exists: start the backend that was waiting for it.
                if let Some(state) = emitter.try_state::<crate::commands::AppState>() {
                    state.supervisor.restart();
                }
            }
        });
        self.run_with(command, notify)
    }

    /// Spawn `command` and stream its progress to `notify` from a background thread.
    pub fn run_with(&self, command: SetupCommand, notify: Notify) -> Result<SetupStatus, SetupError> {
        let mut shared = self.lock();
        if shared.running {
            return Err(SetupError::AlreadyRunning);
        }
        let mut process = Command::new(&command.program);
        process.args(&command.args).envs(command.env.iter().map(|(k, v)| (k, v))).stdin(Stdio::null());
        process.stdout(Stdio::piped()).stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            process.process_group(0);
        }
        let mut child = process.spawn().map_err(|error| SetupError::Spawn(error.to_string()))?;
        let mut status = SetupStatus::new(SetupState::Running);
        status.stage = Some("starting".into());
        status.last_data_at = Some(iso8601_utc(SystemTime::now()));
        shared.running = true;
        shared.child = Some(child.id());
        shared.last = Some(status.clone());
        drop(shared);
        log::info!("[Installer] Setup started — {{ pid: {} }}", child.id());
        notify(&status);
        let installer = self.clone();
        std::thread::spawn(move || installer.follow(&mut child, notify));
        Ok(status)
    }

    fn update(&self, status: &SetupStatus) {
        self.lock().last = Some(status.clone());
    }

    /// Read progress until the script exits, then settle on its final state.
    fn follow(&self, child: &mut Child, notify: Notify) {
        let stderr_tail = Arc::new(Mutex::new(Vec::<String>::new()));
        let stderr_reader = child.stderr.take().map(|stderr| {
            let tail = Arc::clone(&stderr_tail);
            std::thread::spawn(move || {
                for line in BufReader::new(stderr).lines().map_while(Result::ok) {
                    log::info!("[Installer] setup stderr — {line}");
                    let mut tail = tail.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
                    if tail.len() == STDERR_TAIL_LINES {
                        tail.remove(0);
                    }
                    tail.push(line);
                }
            })
        });
        let mut status = self.lock().last.clone().unwrap_or_else(|| SetupStatus::new(SetupState::Running));
        if let Some(stdout) = child.stdout.take() {
            for line in BufReader::new(stdout).lines().map_while(Result::ok) {
                if apply_progress(&mut status, &line, SystemTime::now()) {
                    self.update(&status);
                    notify(&status);
                }
            }
        }
        let exit = child.wait();
        if let Some(reader) = stderr_reader {
            let _ = reader.join();
        }
        let code = exit.as_ref().ok().and_then(std::process::ExitStatus::code);
        if status.state != SetupState::Complete && status.state != SetupState::Failed {
            let tail = stderr_tail.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).join("\n");
            status.state = SetupState::Failed;
            status.reason = Some("SETUP_FAILED".into());
            status.error = Some(match (&exit, tail.is_empty()) {
                (Ok(exit), true) => format!("setup stopped ({exit}) without a result"),
                (Ok(exit), false) => format!("setup stopped ({exit}): {tail}"),
                (Err(error), _) => format!("setup could not be followed: {error}"),
            });
        }
        if status.state == SetupState::Complete && code != Some(0) {
            status.state = SetupState::Failed;
            status.reason = Some("SETUP_FAILED".into());
            status.error = Some(format!("setup reported ready but exited with {code:?}"));
        }
        log::info!("[Installer] Setup finished — {{ state: {:?}, exit_code: {code:?} }}", status.state);
        {
            let mut shared = self.lock();
            shared.running = false;
            shared.child = None;
            shared.last = Some(status.clone());
        }
        notify(&status);
    }

    /// Ask a running setup to stop (it rolls back on SIGTERM) and wait for it, bounded.
    pub fn shutdown(&self) {
        let Some(pid) = self.lock().child else { return };
        log::info!("[Installer] Stopping setup on exit — {{ pid: {pid} }}");
        #[cfg(unix)]
        if let Ok(pgid) = libc::pid_t::try_from(pid) {
            // SAFETY: signals the group this installer created with `process_group(0)`.
            unsafe {
                libc::killpg(pgid, libc::SIGTERM);
            }
        }
        let deadline = std::time::Instant::now() + SHUTDOWN_GRACE;
        while self.lock().running && std::time::Instant::now() < deadline {
            std::thread::sleep(Duration::from_millis(100));
        }
        #[cfg(unix)]
        if self.lock().running {
            if let Ok(pgid) = libc::pid_t::try_from(pid) {
                // SAFETY: as above; the script ignored SIGTERM for the whole grace period.
                unsafe {
                    libc::killpg(pgid, libc::SIGKILL);
                }
            }
        }
    }
}

/// The last progress line `setup_runtime.py` saved, so a failed setup survives an app restart.
fn read_progress_file() -> Option<SetupStatus> {
    let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from)?;
    let mynotes_home = std::env::var_os("MYNOTES_HOME").map(PathBuf::from);
    let file = locate::runtime_home(Platform::current(), &home, mynotes_home.as_deref()).join("setup-progress.json");
    let text = std::fs::read_to_string(file).ok()?;
    let mut status = SetupStatus::new(SetupState::Idle);
    let modified = SystemTime::now();
    apply_progress(&mut status, &text.replace('\n', " "), modified).then_some(status)
}

/// The release root: `MYNOTES_DESKTOP_PAYLOAD`, else `<resources>/payload`.
pub fn release_root(env_payload: Option<PathBuf>, resources: Option<&Path>) -> Result<PathBuf, SetupError> {
    let root = env_payload
        .or_else(|| resources.map(|dir| dir.join("payload")).filter(|dir| dir.is_dir()))
        .ok_or(SetupError::PayloadMissing)?;
    if !root.join(SCRIPT).is_file() {
        return Err(SetupError::PayloadInvalid(root));
    }
    Ok(root)
}

/// A Python 3.11+ for the script (it needs `tomllib`).
fn setup_python(path: Option<&std::ffi::OsStr>) -> Result<PathBuf, SetupError> {
    if let Some(explicit) = std::env::var_os(SETUP_PYTHON_ENV).filter(|v| !v.is_empty()) {
        return Ok(PathBuf::from(explicit));
    }
    let path = path.ok_or(SetupError::PythonMissing)?;
    ["python3.12", "python3.13", "python3"]
        .iter()
        .filter_map(|name| locate::find_on_path(name, path))
        .find(|candidate| {
            Command::new(candidate)
                .args(["-I", "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)"])
                .stdin(Stdio::null())
                .stdout(Stdio::null())
                .stderr(Stdio::null())
                .status()
                .is_ok_and(|status| status.success())
        })
        .ok_or(SetupError::PythonMissing)
}

fn checked_workspace(workspace: Option<&str>) -> Result<Option<PathBuf>, SetupError> {
    let Some(raw) = workspace.filter(|w| !w.trim().is_empty()) else { return Ok(None) };
    crate::paths::lexical_key(raw, crate::paths::Flavor::current()).map_err(|_| SetupError::InvalidWorkspace)?;
    Ok(Some(PathBuf::from(raw)))
}

/// Build the `setup_runtime.py` command line for `request`.
pub fn resolve_command(request: &SetupRunRequest, resources: Option<&Path>) -> Result<SetupCommand, SetupError> {
    let workspace = checked_workspace(request.workspace.as_deref())?;
    let root = release_root(std::env::var_os(PAYLOAD_ENV).filter(|v| !v.is_empty()).map(PathBuf::from), resources)?;
    let path = locate::backend_path();
    let program = setup_python(path.as_deref())?;
    let mut args: Vec<OsString> = vec!["-I".into(), root.join(SCRIPT).into(), "--release-root".into(), root.into()];
    args.extend(["--provider".into(), request.provider.as_str().into()]);
    if let Some(workspace) = workspace {
        args.extend(["--workspace".into(), workspace.into()]);
    }
    let env = path.map(|path| vec![(OsString::from("PATH"), path)]).unwrap_or_default();
    Ok(SetupCommand { program, args, env })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::sync::mpsc;

    fn at(seconds: u64) -> SystemTime {
        UNIX_EPOCH + Duration::from_secs(seconds)
    }

    #[test]
    fn formats_utc_timestamps() {
        assert_eq!(iso8601_utc(at(0)), "1970-01-01T00:00:00Z");
        assert_eq!(iso8601_utc(at(951_782_400)), "2000-02-29T00:00:00Z");
        assert_eq!(iso8601_utc(at(1_795_685_400)), "2026-11-26T09:30:00Z");
    }

    #[test]
    fn download_lines_carry_bytes_and_other_lines_do_not() {
        let mut status = SetupStatus::new(SetupState::Idle);
        let line = r#"{"stage":"components","message":"Downloading speech model file 1/1: 10/1550 MiB","completed":10485760,"total":1624555275,"component":"whisper-model","phase":"download","downloaded_bytes":10485760,"total_bytes":1624555275,"last_data_at":1795685400.5}"#;
        assert!(apply_progress(&mut status, line, at(1)));
        assert_eq!(status.state, SetupState::Running);
        assert_eq!(status.stage.as_deref(), Some("components"));
        assert_eq!((status.bytes_done, status.bytes_total), (Some(10_485_760), Some(1_624_555_275)));
        assert_eq!(status.last_data_at.as_deref(), Some("2026-11-26T09:30:00Z"));
        assert_eq!(status.component.as_deref(), Some("whisper-model"));

        let line = r#"{"stage":"verification","message":"Testing local transcription","completed":0,"total":1}"#;
        assert!(apply_progress(&mut status, line, at(60)));
        assert_eq!((status.bytes_done, status.bytes_total), (None, None));
        assert_eq!(status.last_data_at.as_deref(), Some("1970-01-01T00:01:00Z"));
    }

    #[test]
    fn ready_completes_failed_fails_and_noise_is_ignored() {
        let mut status = SetupStatus::new(SetupState::Running);
        assert!(!apply_progress(&mut status, "Collecting wheels…", at(0)));
        assert!(!apply_progress(&mut status, r#"{"message":"no stage"}"#, at(0)));
        assert!(apply_progress(
            &mut status,
            r#"{"stage":"failed","message":"Setup paused","error":"LibreOffice missing"}"#,
            at(0)
        ));
        assert_eq!(status.state, SetupState::Failed);
        assert_eq!(status.error.as_deref(), Some("LibreOffice missing"));
        assert!(apply_progress(
            &mut status,
            r#"{"stage":"ready","message":"Components installed.","completed":1,"total":1}"#,
            at(0)
        ));
        assert_eq!(status.state, SetupState::Complete);
        assert_eq!(status.error, None);
    }

    #[test]
    fn statuses_match_the_webview_schema_names() {
        let value =
            serde_json::to_value(SetupStatus::new(SetupState::Idle).with_reason("RUNTIME_MISSING", None)).unwrap();
        for key in ["state", "stage", "title", "bytesDone", "bytesTotal", "lastDataAt", "error", "reason", "platform"] {
            assert!(value.get(key).is_some(), "missing {key}");
        }
        assert_eq!(value["state"], "idle");
    }

    #[test]
    fn requests_are_strict_and_workspaces_must_be_absolute() {
        let ok: SetupRunRequest = serde_json::from_value(serde_json::json!({"provider": "claude"})).unwrap();
        assert_eq!(ok.provider, Provider::Claude);
        assert!(serde_json::from_value::<SetupRunRequest>(serde_json::json!({"provider": "gpt"})).is_err());
        assert!(serde_json::from_value::<SetupRunRequest>(serde_json::json!({"provider": "agy", "x": 1})).is_err());
        assert_eq!(checked_workspace(Some("relative/ws")), Err(SetupError::InvalidWorkspace));
        assert_eq!(checked_workspace(Some("   ")), Ok(None));
        if cfg!(unix) {
            assert_eq!(checked_workspace(Some("/srv/ws/../etc")), Err(SetupError::InvalidWorkspace));
            assert_eq!(checked_workspace(Some("/srv/ws")), Ok(Some(PathBuf::from("/srv/ws"))));
        }
    }

    #[test]
    fn a_payload_needs_the_setup_script() {
        assert_eq!(release_root(None, None), Err(SetupError::PayloadMissing));
        let empty = std::env::temp_dir().join(format!("mynotes-desktop-empty-payload-{}", std::process::id()));
        std::fs::create_dir_all(&empty).unwrap();
        assert_eq!(release_root(Some(empty.clone()), None), Err(SetupError::PayloadInvalid(empty.clone())));
        std::fs::remove_dir_all(&empty).unwrap();
    }

    /// A fake release root whose `setup_runtime.py` prints the real progress format.
    fn fake_payload(name: &str, body: &str) -> PathBuf {
        let root = std::env::temp_dir().join(format!("mynotes-desktop-{name}-{}", std::process::id()));
        std::fs::create_dir_all(root.join("scripts")).unwrap();
        std::fs::write(root.join(SCRIPT), body).unwrap();
        root
    }

    fn run_fake(root: &Path) -> Vec<SetupStatus> {
        let python = locate::find_on_path("python3", &std::env::var_os("PATH").unwrap()).expect("python3 on PATH");
        let command = SetupCommand {
            program: python,
            args: vec!["-I".into(), root.join(SCRIPT).into(), "--release-root".into(), root.into()],
            env: Vec::new(),
        };
        let (sender, receiver) = mpsc::channel();
        let installer = Installer::new();
        let first = installer
            .run_with(command.clone(), Arc::new(move |status: &SetupStatus| sender.send(status.clone()).unwrap()))
            .unwrap();
        assert_eq!(first.state, SetupState::Running);
        assert_eq!(
            installer.run_with(command, Arc::new(|_: &SetupStatus| {})).unwrap_err(),
            SetupError::AlreadyRunning
        );
        let mut seen = Vec::new();
        while let Ok(status) = receiver.recv_timeout(Duration::from_secs(20)) {
            let done = !installer.lock().running && matches!(status.state, SetupState::Complete | SetupState::Failed);
            seen.push(status);
            if done {
                break;
            }
        }
        std::fs::remove_dir_all(root).unwrap();
        seen
    }

    #[cfg(unix)]
    #[test]
    fn runs_the_script_and_streams_its_progress_to_completion() {
        let root = fake_payload(
            "ok",
            r#"import json, sys
for line in [
    {"stage": "installation", "message": "Checking MyNotes installation", "completed": 0, "total": 1},
    {"stage": "components", "message": "Downloading model", "completed": 5, "total": 10, "phase": "download", "downloaded_bytes": 5, "total_bytes": 10},
    {"stage": "ready", "message": "Components installed.", "completed": 1, "total": 1},
]:
    print(json.dumps(line), flush=True)
print("pip noise on stderr", file=sys.stderr)
"#,
        );
        let seen = run_fake(&root);
        let stages: Vec<_> = seen.iter().filter_map(|s| s.stage.clone()).collect();
        assert_eq!(stages, ["starting", "installation", "components", "ready", "ready"]);
        assert_eq!(seen[2].bytes_done, Some(5));
        assert_eq!(seen.last().unwrap().state, SetupState::Complete);
    }

    #[cfg(unix)]
    #[test]
    fn a_crash_without_a_result_is_a_failure_with_stderr() {
        let root = fake_payload(
            "crash",
            r#"import json, sys
print(json.dumps({"stage": "installation", "message": "Checking", "completed": 0, "total": 1}), flush=True)
print("Traceback: disk full", file=sys.stderr)
raise SystemExit(3)
"#,
        );
        let last = run_fake(&root).pop().unwrap();
        assert_eq!(last.state, SetupState::Failed);
        assert!(last.error.as_deref().unwrap().contains("disk full"), "{:?}", last.error);
    }
}

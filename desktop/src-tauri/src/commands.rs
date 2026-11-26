//! Tauri commands exposed to the webviews.
//!
//! `rpc_call` is the only door to the backend: the method must be in the protocol-1 table
//! ([`crate::backend::methods::METHODS`]) and not reserved for Rust, and the small `capsule`
//! window may only call the three capture methods it renders. Notifications travel the other
//! way as the `rpc://notification` event (see `events.rs`).
//!
//! `open_path` and `reveal_path` run the checks of [`crate::paths`] and then call
//! `tauri-plugin-opener` from Rust. The plugin is used as a library and never registered, so the
//! webview has no opener command to call, whatever a capability file says.
//!
//! Every command is also listed in `build.rs` (app manifest) and granted per window in
//! `capabilities/*.json`.

use std::sync::{Arc, Mutex};

use serde::Serialize;
use serde_json::Value;
use tauri::{AppHandle, State, WebviewWindow};
use tauri_plugin_log::log;

use crate::backend::locate::Platform;
use crate::backend::methods::{lane_of, RUST_ONLY_METHODS};
use crate::backend::protocol::{codes, RpcError};
use crate::backend::supervisor::{BackendStatus, Supervisor};
use crate::external;
use crate::installer::{Installer, SetupRunRequest, SetupStatus};
use crate::paths;

/// Label of the floating recording capsule window.
pub const CAPSULE_LABEL: &str = "capsule";
/// The only methods the capsule window may call.
pub const CAPSULE_METHODS: &[&str] = &["capture.dashboard", "capture.status", "capture.stop"];

pub struct AppState {
    pub supervisor: Supervisor,
    /// Folders `open_path` and `reveal_path` may touch; course folders come from `config`.
    pub roots: Arc<paths::AllowedRoots>,
    pub installer: Installer,
    /// `--action` given to the first launch, collected once by the webview.
    pub pending_action: Mutex<Option<String>>,
    /// The window session the webview asked for, replayed when a restarted backend is ready.
    pub session: SessionIntent,
}

#[cfg(test)]
impl AppState {
    /// State with a supervisor that is never started, for tests of the native surfaces.
    pub fn for_tests() -> Self {
        use crate::backend::supervisor::{EventSink, LaunchError, SupervisorConfig};
        struct Quiet;
        impl EventSink for Quiet {
            fn notification(&self, _method: &str, _params: &Value) {}
            fn status(&self, _status: &BackendStatus) {}
        }
        let launch = Arc::new(|| Err(LaunchError { code: "NOT_INSTALLED".into(), message: "test".into() }));
        Self {
            supervisor: Supervisor::new(SupervisorConfig::default(), launch, Arc::new(Quiet)),
            roots: Arc::new(paths::AllowedRoots::new(&[])),
            installer: Installer::new(),
            pending_action: Mutex::new(None),
            session: SessionIntent::default(),
        }
    }
}

/// Last `session.open` / `session.setVisible` / `session.close` seen from the webview.
///
/// A restarted server knows no window session, so queue jobs would stop starting (the lease
/// lapses); `events.rs` sends `session.open` again with this visibility on every `ready`.
#[derive(Debug, Default)]
pub struct SessionIntent(Mutex<Option<bool>>);

impl SessionIntent {
    pub fn observe(&self, method: &str, params: Option<&Value>) {
        let visible = params.and_then(|p| p.get("visible")).and_then(Value::as_bool).unwrap_or(true);
        let mut intent = self.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        match method {
            "session.open" => *intent = Some(visible),
            "session.setVisible" if intent.is_some() => *intent = Some(visible),
            "session.close" => *intent = None,
            _ => {}
        }
    }

    /// Params for `session.open`, when the webview had one open.
    pub fn replay(&self) -> Option<Value> {
        let intent = *self.0.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        intent.map(|visible| serde_json::json!({ "visible": visible }))
    }
}

/// May the webview call `method`? Known to protocol 1, well formed, not reserved for Rust.
pub fn is_allowed(method: &str) -> bool {
    !RUST_ONLY_METHODS.contains(&method) && lane_of(method).is_some()
}

/// Per-window rule on top of [`is_allowed`]: the capsule only stops and reads capture.
pub fn is_allowed_for_window(label: &str, method: &str) -> bool {
    is_allowed(method) && (label != CAPSULE_LABEL || CAPSULE_METHODS.contains(&method))
}

/// Error shape every command returns; mirrors JSON-RPC errors (`src/rpc/errors.ts`).
#[derive(Debug, Serialize)]
pub struct CommandError {
    pub code: i64,
    pub message: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub data: Option<Value>,
}

impl From<RpcError> for CommandError {
    fn from(error: RpcError) -> Self {
        Self { code: error.code, message: error.message, data: error.data }
    }
}

impl CommandError {
    fn new(code: i64, message: impl Into<String>) -> Self {
        Self { code, message: message.into(), data: None }
    }
}

#[tauri::command]
pub async fn rpc_call(
    window: WebviewWindow,
    state: State<'_, AppState>,
    method: String,
    params: Option<Value>,
) -> Result<Value, CommandError> {
    if !is_allowed_for_window(window.label(), &method) {
        log::warn!("[Commands] Method refused — {{ method: {method:?}, window: {} }}", window.label());
        return Err(CommandError::new(codes::METHOD_NOT_ALLOWED, format!("method not allowed: {method}")));
    }
    if params.as_ref().is_some_and(|p| !p.is_object()) {
        return Err(CommandError::new(codes::INVALID_PARAMS, "params must be an object"));
    }
    state.session.observe(&method, params.as_ref());
    state.supervisor.call(&method, params).await.map_err(CommandError::from)
}

#[tauri::command]
pub fn backend_status(state: State<'_, AppState>) -> BackendStatus {
    state.supervisor.status()
}

/// Start the backend again after the failure screen (or after setup installed the runtime).
#[tauri::command]
pub fn backend_restart(state: State<'_, AppState>) -> BackendStatus {
    state.supervisor.restart();
    state.supervisor.status()
}

fn path_error(error: paths::PathError) -> CommandError {
    CommandError::new(codes::INVALID_PARAMS, error.to_string())
}

/// Open a document with the OS default application, after [`paths::check_openable`].
#[tauri::command]
pub fn open_path(state: State<'_, AppState>, path: String) -> Result<(), CommandError> {
    let checked = paths::check_openable(&path, &state.roots).map_err(path_error)?;
    log::info!("[Commands] Opening file — {{ extension: {:?} }}", checked.extension());
    tauri_plugin_opener::open_path(&checked, None::<&str>)
        .map_err(|error| CommandError::new(codes::INTERNAL_ERROR, format!("could not open the file: {error}")))
}

/// Show a file or folder in the system file manager, after [`paths::check_revealable`].
#[tauri::command]
pub fn reveal_path(state: State<'_, AppState>, path: String) -> Result<(), CommandError> {
    let checked = paths::check_revealable(&path, &state.roots).map_err(path_error)?;
    log::info!("[Commands] Revealing path — {{ is_dir: {} }}", checked.is_dir());
    tauri_plugin_opener::reveal_item_in_dir(&checked)
        .map_err(|error| CommandError::new(codes::INTERNAL_ERROR, format!("could not show the file: {error}")))
}

/// Let the webview play one recording through the asset protocol. The path must pass
/// [`paths::check_playable`] (an audio file inside a recording folder the backend reports);
/// then only that file joins the asset scope, which starts empty (`tauri.conf.json`).
/// Returns the canonical path, the form the scope matches, for `convertFileSrc`.
#[tauri::command]
pub fn audio_source(app: AppHandle, state: State<'_, AppState>, path: String) -> Result<String, CommandError> {
    use tauri::Manager;
    let checked = paths::check_playable(&path, &state.roots).map_err(path_error)?;
    app.asset_protocol_scope()
        .allow_file(&checked)
        .map_err(|error| CommandError::new(codes::INTERNAL_ERROR, format!("could not allow the recording: {error}")))?;
    log::info!("[Commands] Recording allowed for playback — {{ extension: {:?} }}", checked.extension());
    checked
        .to_str()
        .map(str::to_owned)
        .ok_or_else(|| CommandError::new(codes::INVALID_PARAMS, "the recording path is not valid UTF-8"))
}

/// First-run state, the same shape as `setupStatusSchema` in `src/rpc/schema.ts`.
#[tauri::command]
pub fn setup_status(state: State<'_, AppState>) -> SetupStatus {
    state.installer.status()
}

/// Run `setup_runtime.py`; progress arrives as `setup://progress` events. Async, and the
/// launch (Python lookup, payload checks, spawn) runs on a blocking worker: a sync command runs
/// on the main thread, which also draws the window.
#[tauri::command]
pub async fn setup_run(app: AppHandle, request: SetupRunRequest) -> Result<SetupStatus, CommandError> {
    use tauri::Manager;
    let worker = app.clone();
    let launched = tauri::async_runtime::spawn_blocking(move || {
        let state = worker.state::<AppState>();
        state.installer.run(worker.clone(), request)
    })
    .await
    .map_err(|error| CommandError::new(codes::INTERNAL_ERROR, format!("setup could not start: {error}")))?;
    launched.map_err(|error| CommandError {
        code: codes::INVALID_PARAMS,
        message: error.to_string(),
        data: Some(serde_json::json!({ "reason": error.code() })),
    })
}

/// Open an install guide in the default browser, after [`external::check_link`].
#[tauri::command]
pub fn open_external(url: String) -> Result<(), CommandError> {
    let checked =
        external::check_link(&url).map_err(|error| CommandError::new(codes::INVALID_PARAMS, error.to_string()))?;
    log::info!("[Commands] Opening link — {{ host: {:?} }}", checked.host_str());
    tauri_plugin_opener::open_url(checked.as_str(), None::<&str>)
        .map_err(|error| CommandError::new(codes::INTERNAL_ERROR, format!("could not open the link: {error}")))
}

/// Open the system's microphone or sound settings ([`external::settings_target`]).
#[tauri::command]
pub fn open_system_settings(pane: String) -> Result<(), CommandError> {
    let refused = |error: external::ExternalError| CommandError::new(codes::INVALID_PARAMS, error.to_string());
    let pane = external::SettingsPane::parse(&pane).map_err(refused)?;
    let path = crate::backend::locate::cached_backend_path();
    let target = external::settings_target(Platform::current(), pane, path.as_deref()).map_err(refused)?;
    log::info!("[Commands] Opening system settings — {{ pane: {pane:?} }}");
    match target {
        external::SettingsTarget::Url(url) => tauri_plugin_opener::open_url(url, None::<&str>)
            .map_err(|error| CommandError::new(codes::INTERNAL_ERROR, format!("could not open the settings: {error}"))),
        external::SettingsTarget::Program { program, args } => {
            let mut child = std::process::Command::new(program)
                .args(args)
                .stdin(std::process::Stdio::null())
                .stdout(std::process::Stdio::null())
                .stderr(std::process::Stdio::null())
                .spawn()
                .map_err(|error| {
                    CommandError::new(codes::INTERNAL_ERROR, format!("could not open the settings: {error}"))
                })?;
            // Reap it when the user closes it, so no zombie stays behind.
            std::thread::spawn(move || {
                let _ = child.wait();
            });
            Ok(())
        }
    }
}

/// The `--action` the app was launched with, once (`record-dialog`, `show`).
#[tauri::command]
pub fn take_pending_action(state: State<'_, AppState>) -> Option<String> {
    state.pending_action.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).take()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn allowlist_accepts_every_protocol_method_but_shutdown() {
        for method in [
            "system.hello",
            "notes.run",
            "notes.publish",
            "capture.dashboard",
            "capture.connect",
            "queue.status",
            "library.importAudio",
            "library.transcript",
            "lesson.process",
            "job.wait",
        ] {
            assert!(is_allowed(method), "{method} should be allowed");
        }
        assert!(!is_allowed("system.shutdown"), "only Rust stops the backend");
    }

    #[test]
    fn allowlist_rejects_unknown_and_malformed_methods() {
        for method in [
            "",
            "shell.exec",
            "capture.",
            "capture.dash board",
            "notes.run;rm",
            "capture.run",
            "queue.heartbeat",
            "setup.run",
        ] {
            assert!(!is_allowed(method), "{method:?} should be rejected");
        }
    }

    #[test]
    fn the_capsule_window_only_reads_and_stops_capture() {
        assert!(is_allowed_for_window(CAPSULE_LABEL, "capture.stop"));
        assert!(is_allowed_for_window(CAPSULE_LABEL, "capture.dashboard"));
        assert!(!is_allowed_for_window(CAPSULE_LABEL, "notes.run"));
        assert!(!is_allowed_for_window(CAPSULE_LABEL, "system.shutdown"));
        assert!(is_allowed_for_window("main", "notes.run"));
    }

    #[test]
    fn the_session_intent_follows_open_visibility_and_close() {
        let intent = SessionIntent::default();
        intent.observe("session.setVisible", Some(&serde_json::json!({"visible": false})));
        assert_eq!(intent.replay(), None, "no session was opened");
        intent.observe("session.open", Some(&serde_json::json!({"visible": true})));
        intent.observe("capture.dashboard", None);
        assert_eq!(intent.replay(), Some(serde_json::json!({"visible": true})));
        intent.observe("session.setVisible", Some(&serde_json::json!({"visible": false})));
        assert_eq!(intent.replay(), Some(serde_json::json!({"visible": false})));
        intent.observe("session.close", None);
        assert_eq!(intent.replay(), None);
    }

    /// Audio playback needs the asset protocol, but nothing may be served from it until
    /// `audio_source` has checked one file: no static scope, and only media may load from it.
    #[test]
    fn the_asset_protocol_serves_only_what_audio_source_allows() {
        let config: Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let security = &config["app"]["security"];
        assert_eq!(security["assetProtocol"], serde_json::json!({"enable": true, "scope": []}), "no $HOME/** scope");
        for key in ["csp", "devCsp"] {
            let csp = security[key].as_str().unwrap();
            let directive = |name: &str| {
                csp.split(';').map(str::trim).find(|d| d.split_whitespace().next() == Some(name)).unwrap().to_owned()
            };
            assert_eq!(directive("media-src"), "media-src 'self' asset: http://asset.localhost", "{key}");
            assert_eq!(directive("default-src"), "default-src 'self'", "{key}");
            assert_eq!(directive("script-src"), "script-src 'self'", "{key}");
            assert!(!directive("connect-src").contains("asset"), "{key}");
        }
        let cargo = include_str!("../Cargo.toml");
        assert!(cargo.lines().any(|line| line.starts_with("tauri = ") && line.contains("\"protocol-asset\"")));
        let main: Value = serde_json::from_str(include_str!("../capabilities/default.json")).unwrap();
        let capsule: Value = serde_json::from_str(include_str!("../capabilities/capsule.json")).unwrap();
        let grants = |capability: &Value| {
            capability["permissions"].as_array().unwrap().contains(&serde_json::json!("allow-audio-source"))
        };
        assert!(grants(&main) && !grants(&capsule), "only the main window plays recordings");
    }

    #[test]
    fn every_capsule_method_exists_in_protocol_one() {
        for method in CAPSULE_METHODS {
            assert!(is_allowed(method), "{method} is not a protocol-1 method");
        }
    }
}

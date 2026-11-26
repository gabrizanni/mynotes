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

use crate::backend::methods::{lane_of, RUST_ONLY_METHODS};
use crate::backend::protocol::{codes, RpcError};
use crate::backend::supervisor::{BackendStatus, Supervisor};
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

/// First-run state, the same shape as `setupStatusSchema` in `src/rpc/schema.ts`.
#[tauri::command]
pub fn setup_status(state: State<'_, AppState>) -> SetupStatus {
    state.installer.status()
}

/// Run `setup_runtime.py`; progress arrives as `setup://progress` events.
#[tauri::command]
pub fn setup_run(
    app: AppHandle,
    state: State<'_, AppState>,
    request: SetupRunRequest,
) -> Result<SetupStatus, CommandError> {
    state.installer.run(app, request).map_err(|error| CommandError {
        code: codes::INVALID_PARAMS,
        message: error.to_string(),
        data: Some(serde_json::json!({ "reason": error.code() })),
    })
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

    #[test]
    fn every_capsule_method_exists_in_protocol_one() {
        for method in CAPSULE_METHODS {
            assert!(is_allowed(method), "{method} is not a protocol-1 method");
        }
    }
}

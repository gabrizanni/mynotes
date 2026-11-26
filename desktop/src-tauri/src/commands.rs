//! Tauri commands exposed to the webview.
//!
//! `rpc_call` is the only door to the backend: it checks the method against an allowlist before
//! anything reaches the Python process. Notifications travel the other way as the
//! `rpc://notification` event (see `backend/supervisor.rs`).

use serde::Serialize;
use serde_json::Value;
use tauri::State;

use crate::backend::protocol::{codes, RpcError};
use crate::backend::supervisor::Supervisor;
use crate::paths;

pub struct AppState {
    pub supervisor: Supervisor,
}

/// Exact methods the webview may call (ARCHITECTURE.md section A, protocol 1).
const ALLOWED_METHODS: &[&str] = &[
    "system.hello",
    "system.ping",
    "system.stats",
    "session.open",
    "session.close",
    "session.setVisible",
    "watch.subscribe",
    "watch.unsubscribe",
    "doctor.get",
    "provider.status",
    "provider.login",
    "config.get",
    "profile.save",
    "course.add",
    "course.edit",
    "course.link",
    "course.pending",
    "course.deferContext",
    "memory.show",
    "memory.update",
    "style.show",
    "style.preview",
    "preferences.get",
    "preferences.saveModels",
    "models.catalog",
    "transcription.status",
    "transcription.run",
    "notes.plan",
    "notes.run",
    "notes.batchStatus",
    "notes.history",
    "notes.undo",
    "notes.restore",
    "lesson.process",
    "job.list",
    "job.get",
    "job.cancel",
    "setup.status",
    "setup.run",
    "speech.installModel",
    "speech.verify",
    "word.verifyRenderer",
];

/// Families with one method per CLI subcommand (`capture.dashboard`, `queue.enqueue`, …).
const ALLOWED_PREFIXES: &[&str] = &["capture.", "queue.", "library."];

/// Never callable from the webview, even inside an allowed family.
const DENIED_METHODS: &[&str] =
    &["system.shutdown", "capture.run", "queue.run", "queue.worker", "queue.heartbeat", "queue.close"];

pub fn is_allowed(method: &str) -> bool {
    if DENIED_METHODS.contains(&method) {
        return false;
    }
    let well_formed = !method.is_empty() && method.chars().all(|c| c.is_ascii_alphanumeric() || c == '.' || c == '_');
    well_formed
        && (ALLOWED_METHODS.contains(&method)
            || ALLOWED_PREFIXES.iter().any(|prefix| method.len() > prefix.len() && method.starts_with(prefix)))
}

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

#[tauri::command]
pub async fn rpc_call(
    state: State<'_, AppState>,
    method: String,
    params: Option<Value>,
) -> Result<Value, CommandError> {
    if !is_allowed(&method) {
        return Err(RpcError::new(codes::METHOD_NOT_ALLOWED, format!("method not allowed: {method}")).into());
    }
    state.supervisor.call(&method, params).await.map_err(CommandError::from)
}

/// Open a document with the OS default application, after [`paths::check_openable`].
///
/// TODO(opener): hand the checked path to `tauri-plugin-opener` from Rust once it is added.
#[tauri::command]
pub fn open_path(path: String) -> Result<(), CommandError> {
    let checked = paths::check_openable(&path).map_err(|error| CommandError {
        code: codes::INVALID_PARAMS,
        message: error.to_string(),
        data: None,
    })?;
    Err(CommandError {
        code: codes::INTERNAL_ERROR,
        message: format!("opening is not wired yet: {}", checked.display()),
        data: None,
    })
}

/// Show a file or folder in the system file manager, after [`paths::check_revealable`].
///
/// TODO(opener): `tauri-plugin-opener`'s `reveal_item_in_dir` from Rust.
#[tauri::command]
pub fn reveal_path(path: String) -> Result<(), CommandError> {
    let checked = paths::check_revealable(&path).map_err(|error| CommandError {
        code: codes::INVALID_PARAMS,
        message: error.to_string(),
        data: None,
    })?;
    Err(CommandError {
        code: codes::INTERNAL_ERROR,
        message: format!("revealing is not wired yet: {}", checked.display()),
        data: None,
    })
}

#[cfg(test)]
mod tests {
    use super::is_allowed;

    #[test]
    fn allowlist_accepts_known_methods_and_families() {
        for method in ["system.hello", "notes.run", "capture.dashboard", "queue.status", "library.importAudio"] {
            assert!(is_allowed(method), "{method} should be allowed");
        }
    }

    #[test]
    fn allowlist_rejects_unknown_denied_and_malformed_methods() {
        for method in [
            "",
            "system.shutdown",
            "capture.run",
            "queue.heartbeat",
            "shell.exec",
            "capture.",
            "capture.dash board",
            "notes.run;rm",
        ] {
            assert!(!is_allowed(method), "{method:?} should be rejected");
        }
    }
}

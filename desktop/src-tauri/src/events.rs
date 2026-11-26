//! What the supervisor reports, turned into Tauri events and native side effects.
//!
//! Events (names shared with `src/rpc/transports/tauri.ts`):
//! - `rpc://notification` `{method, params}`: every server notification, unchanged;
//! - `backend://status`: [`BackendStatus`] at every lifecycle change;
//! - `app://action` `{action}`: a tray item, the global shortcut or a second launch asked for
//!   an action (see `actions.rs`).
//!
//! Side effects, all driven by notifications so they work with the window closed:
//! - `config.changed` → course folders allowed for `open_path` / `reveal_path`;
//! - `capture.changed` → tray recording state and the recording capsule window;
//! - `job.finished` of a notes job → the "notes ready" system notification.
//!
//! On every `ready` (first start and each restart) the Rust side reopens the webview's window
//! session if it had one and subscribes to all four watch topics, so tray, capsule, path roots
//! and the webview's notifications survive a backend restart.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

use serde_json::{json, Value};
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tauri_plugin_log::log;

use crate::backend::supervisor::{BackendStatus, EventSink};
use crate::commands::AppState;
use crate::paths::AllowedRoots;

pub const NOTIFICATION_EVENT: &str = "rpc://notification";
pub const STATUS_EVENT: &str = "backend://status";
pub const ACTION_EVENT: &str = "app://action";
/// Job methods whose success means "your notes are ready".
const NOTES_METHODS: &[&str] = &["notes.run", "lesson.process"];
/// Started jobs remembered to name their `job.finished` (bounded).
const MAX_TRACKED_JOBS: usize = 256;

/// The `data` of a watch snapshot (`{topic, hash, data}`), unwrapping a `{exitCode, data}`
/// envelope too; a bare object passes through.
pub fn snapshot_data(params: &Value) -> Option<&Value> {
    let data = if params.get("topic").is_some() && params.get("hash").is_some() { params.get("data")? } else { params };
    let data = match (data.get("exitCode"), data.get("data")) {
        (Some(_), Some(inner)) => inner,
        _ => data,
    };
    (!data.is_null()).then_some(data)
}

/// Recording state the tray and the capsule show.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct Recording {
    pub active: bool,
    pub exporting: bool,
    pub title: Option<String>,
}

/// Read `runtime.recording`, `runtime.exporting` and the current lesson title of a dashboard.
pub fn recording_state(dashboard: &Value) -> Recording {
    let runtime = &dashboard["runtime"];
    let title = runtime["currentSession"]["event"]["title"]
        .as_str()
        .or_else(|| runtime["title"].as_str())
        .map(str::to_owned)
        .filter(|title| !title.is_empty());
    Recording {
        active: runtime["recording"].as_bool().unwrap_or(false),
        exporting: runtime["exporting"].as_bool().unwrap_or(false),
        title,
    }
}

/// Every local folder the configuration names: course folders and the configured paths.
pub fn course_folders(config: &Value) -> Vec<PathBuf> {
    fn collect(value: &Value, out: &mut Vec<PathBuf>) {
        match value {
            Value::String(text) if !text.is_empty() => out.push(PathBuf::from(text)),
            Value::Array(items) => items.iter().for_each(|item| collect(item, out)),
            _ => {}
        }
    }
    let mut folders = Vec::new();
    for course in config["courses"].as_array().into_iter().flatten() {
        for folder in course["folders"].as_object().into_iter().flat_map(|map| map.values()) {
            collect(folder, &mut folders);
        }
    }
    for value in config["paths"].as_object().into_iter().flat_map(|map| map.values()) {
        collect(value, &mut folders);
    }
    folders.sort();
    folders.dedup();
    folders
}

/// A finished notes job worth a notification: completed, exit code 0.
pub fn notes_ready(method: Option<&str>, finished: &Value) -> bool {
    method.is_some_and(|method| NOTES_METHODS.contains(&method))
        && finished["state"].as_str() == Some("COMPLETED")
        && finished["exitCode"].as_i64().unwrap_or(0) == 0
}

/// [`EventSink`] backed by a Tauri app.
pub struct TauriSink<R: Runtime> {
    app: AppHandle<R>,
    roots: Arc<AllowedRoots>,
    jobs: Mutex<HashMap<String, String>>,
}

impl<R: Runtime> TauriSink<R> {
    pub fn new(app: AppHandle<R>, roots: Arc<AllowedRoots>) -> Self {
        Self { app, roots, jobs: Mutex::new(HashMap::new()) }
    }

    fn apply_config(&self, config: &Value) {
        let folders = course_folders(config);
        log::debug!("[Events] Course folders updated — {{ count: {} }}", folders.len());
        self.roots.set_course_folders(&folders);
    }

    fn track_job(&self, params: &Value) {
        let (Some(id), Some(method)) = (params["jobId"].as_str(), params["method"].as_str()) else { return };
        let mut jobs = self.jobs.lock().unwrap_or_else(|poisoned| poisoned.into_inner());
        if jobs.len() >= MAX_TRACKED_JOBS {
            jobs.clear();
        }
        jobs.insert(id.to_owned(), method.to_owned());
    }

    fn finish_job(&self, params: &Value) {
        let Some(id) = params["jobId"].as_str() else { return };
        let method = self.jobs.lock().unwrap_or_else(|poisoned| poisoned.into_inner()).remove(id);
        if notes_ready(method.as_deref(), params) {
            notify_notes_ready(&self.app);
        }
    }
}

impl<R: Runtime> EventSink for TauriSink<R> {
    fn notification(&self, method: &str, params: &Value) {
        if let Err(error) = self.app.emit(NOTIFICATION_EVENT, json!({ "method": method, "params": params })) {
            log::warn!("[Events] Notification not delivered — {{ method: {method}, error: {error} }}");
        }
        match method {
            "config.changed" => {
                if let Some(config) = snapshot_data(params) {
                    self.apply_config(config);
                }
            }
            "capture.changed" => {
                if let Some(dashboard) = snapshot_data(params) {
                    apply_recording(&self.app, &recording_state(dashboard));
                }
            }
            "job.started" => self.track_job(params),
            "job.finished" => self.finish_job(params),
            _ => {}
        }
    }

    fn status(&self, status: &BackendStatus) {
        log::info!("[Events] Backend status — {{ status: {status:?} }}");
        if let Err(error) = self.app.emit(STATUS_EVENT, status) {
            log::warn!("[Events] Status not delivered — {{ error: {error} }}");
        }
        if matches!(status, BackendStatus::Ready { .. }) {
            subscribe_native_topics(self.app.clone());
        }
    }
}

fn apply_recording<R: Runtime>(app: &AppHandle<R>, recording: &Recording) {
    crate::tray::set_recording(app, recording);
    crate::capsule::sync(app, recording);
}

/// Subscribe to `capture` and `config` for the native side and apply the first snapshots.
fn subscribe_native_topics<R: Runtime>(app: AppHandle<R>) {
    tauri::async_runtime::spawn(async move {
        let Some(state) = app.try_state::<AppState>() else { return };
        let supervisor = state.supervisor.clone();
        let roots = Arc::clone(&state.roots);
        if let Some(params) = state.session.replay() {
            if let Err(error) = supervisor.call("session.open", Some(params)).await {
                log::warn!("[Events] Session not reopened — {{ code: {}, message: {} }}", error.code, error.message);
            }
        }
        // All four topics: after a restart the webview's own subscription is gone with the old
        // server, and `<topic>.changed` keeps flowing to it this way.
        let topics = json!({ "topics": ["capture", "queue", "library", "config"] });
        let answer = supervisor.call("watch.subscribe", Some(topics)).await;
        let snapshots = match answer {
            Ok(value) => value,
            Err(error) => {
                log::warn!(
                    "[Events] Native subscription failed — {{ code: {}, message: {} }}",
                    error.code,
                    error.message
                );
                return;
            }
        };
        if let Some(config) = snapshot_data(&snapshots["snapshots"]["config"]) {
            roots.set_course_folders(&course_folders(config));
        }
        if let Some(dashboard) = snapshot_data(&snapshots["snapshots"]["capture"]) {
            apply_recording(&app, &recording_state(dashboard));
        }
    });
}

fn notify_notes_ready<R: Runtime>(app: &AppHandle<R>) {
    use tauri_plugin_notification::NotificationExt;
    let focused = app.get_webview_window("main").and_then(|window| window.is_focused().ok()).unwrap_or(false);
    if focused {
        return;
    }
    let shown = app.notification().builder().title("Notes ready").body("Your handout is up to date in MyNotes.").show();
    if let Err(error) = shown {
        log::warn!("[Events] Notes-ready notification failed — {{ error: {error} }}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn snapshots_and_envelopes_unwrap_to_their_data() {
        let snapshot = json!({"topic": "capture", "hash": "h", "data": {"schemaVersion": 1}});
        assert_eq!(snapshot_data(&snapshot), Some(&json!({"schemaVersion": 1})));
        let envelope = json!({"topic": "config", "hash": "h", "data": {"exitCode": 0, "data": {"courses": []}}});
        assert_eq!(snapshot_data(&envelope), Some(&json!({"courses": []})));
        let failed = json!({"topic": "capture", "hash": "h", "data": null, "error": {"code": -32001}});
        assert_eq!(snapshot_data(&failed), None);
        assert_eq!(snapshot_data(&json!({"runtime": {}})), Some(&json!({"runtime": {}})));
    }

    #[test]
    fn recording_state_reads_the_runtime_block() {
        let dashboard = json!({"runtime": {"recording": true, "exporting": false,
            "currentSession": {"event": {"title": "Lesson 5"}}}});
        assert_eq!(
            recording_state(&dashboard),
            Recording { active: true, exporting: false, title: Some("Lesson 5".into()) }
        );
        assert_eq!(recording_state(&json!({})), Recording::default());
    }

    #[test]
    fn course_folders_collects_course_and_configured_paths() {
        let config = json!({
            "courses": [{"folders": {"materials": "/srv/ws/DEMO/Materials", "syllabus": "", "recordings": ["/srv/rec"]}}],
            "paths": {"recording_dirs": ["/srv/rec", "/srv/more"], "workspace": "/srv/ws"}
        });
        let folders: Vec<PathBuf> =
            ["/srv/more", "/srv/rec", "/srv/ws", "/srv/ws/DEMO/Materials"].iter().map(PathBuf::from).collect();
        assert_eq!(course_folders(&config), folders);
        assert!(course_folders(&json!({})).is_empty());
    }

    #[test]
    fn only_completed_notes_jobs_notify() {
        assert!(notes_ready(Some("notes.run"), &json!({"state": "COMPLETED", "exitCode": 0})));
        assert!(notes_ready(Some("lesson.process"), &json!({"state": "COMPLETED"})));
        assert!(!notes_ready(Some("notes.run"), &json!({"state": "FAILED", "exitCode": 1})));
        assert!(!notes_ready(Some("notes.run"), &json!({"state": "COMPLETED", "exitCode": 2})));
        assert!(!notes_ready(Some("transcription.run"), &json!({"state": "COMPLETED"})));
        assert!(!notes_ready(None, &json!({"state": "COMPLETED"})));
    }
}

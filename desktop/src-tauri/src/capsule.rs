//! The recording capsule: a small, transparent, always-on-top, undecorated window
//! (`capsule.html`, label `capsule`) shown only while a lesson records or is being saved.
//!
//! It is created on the first recording, not at startup (one webview less while idle), and
//! hidden, not destroyed, when recording stops. Its page renders `RecordingCapsule` from its own
//! `capture.changed` events and may call only the capture methods listed in
//! [`crate::commands::CAPSULE_METHODS`]. Wayland compositors place it themselves; elsewhere it
//! sits at the top centre of the current monitor. macOS gets no transparency (it would need
//! Tauri's private-API flag), so the page paints its own pill background.

use tauri::{AppHandle, Manager, PhysicalPosition, Runtime, WebviewUrl, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_log::log;

use crate::commands::CAPSULE_LABEL;
use crate::events::Recording;

const WIDTH: f64 = 380.0;
const HEIGHT: f64 = 64.0;
const TOP_MARGIN: i32 = 24;

fn create<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<WebviewWindow<R>> {
    let builder = WebviewWindowBuilder::new(app, CAPSULE_LABEL, WebviewUrl::App("capsule.html".into()))
        .title("MyNotes recording")
        .inner_size(WIDTH, HEIGHT)
        .resizable(false)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .visible_on_all_workspaces(true)
        .focused(false)
        .shadow(false)
        .visible(false);
    #[cfg(not(target_os = "macos"))]
    let builder = builder.transparent(true);
    let window = builder.build()?;
    if let Ok(Some(monitor)) = window.current_monitor() {
        let scale = monitor.scale_factor();
        let x = monitor.position().x + (monitor.size().width as i32 - (WIDTH * scale) as i32) / 2;
        let _ = window.set_position(PhysicalPosition::new(x, monitor.position().y + TOP_MARGIN));
    }
    Ok(window)
}

/// Show the capsule while recording or saving, hide it otherwise.
pub fn sync<R: Runtime>(app: &AppHandle<R>, recording: &Recording) {
    let wanted = recording.active || recording.exporting;
    let existing = app.get_webview_window(CAPSULE_LABEL);
    if !wanted {
        if let Some(window) = existing {
            let _ = window.hide();
        }
        return;
    }
    let window = match existing {
        Some(window) => window,
        None => match create(app) {
            Ok(window) => window,
            Err(error) => {
                log::warn!("[Capsule] Window not created — {{ error: {error} }}");
                return;
            }
        },
    };
    if !window.is_visible().unwrap_or(false) {
        log::info!("[Capsule] Showing — {{ recording: {}, exporting: {} }}", recording.active, recording.exporting);
        let _ = window.show();
    }
}

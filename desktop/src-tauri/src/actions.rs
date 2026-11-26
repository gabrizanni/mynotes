//! App actions requested from outside the webview: a second launch (`--action record-dialog`,
//! which is how Hyprland and other Wayland key binds reach the app), the tray, or the global
//! shortcut. Each one shows the main window and emits `app://action {action}` to it.
//!
//! Window routing: closing the main window destroys it, but the app may live on, because the
//! recording capsule is another window. So [`show_main`] recreates the main window from the
//! app configuration when it is gone, and [`main_window_destroyed`] quits the app when no tray
//! is there to reopen it: no state leaves a running app that nothing can show or quit.

use std::sync::atomic::{AtomicBool, Ordering};

use serde_json::json;
use tauri::{AppHandle, Emitter, Manager, Runtime, WebviewWindow, WebviewWindowBuilder};
use tauri_plugin_log::log;

use crate::events::ACTION_EVENT;

pub const MAIN_LABEL: &str = "main";

/// Set once the app is exiting, so a second exit request is not sent.
static EXITING: AtomicBool = AtomicBool::new(false);

/// What closing the main window leads to.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AfterMainClosed {
    /// The tray can reopen the window or quit; recording state and notifications go on.
    KeepRunning,
    /// Nothing could bring the window back or quit the app (the capsule only stops a recording).
    Quit,
}

pub fn after_main_closed(tray_available: bool) -> AfterMainClosed {
    if tray_available {
        AfterMainClosed::KeepRunning
    } else {
        AfterMainClosed::Quit
    }
}

/// Exit once; the tray's "Quit MyNotes" and a closed main window without a tray both end here.
pub fn quit<R: Runtime>(app: &AppHandle<R>) {
    if !EXITING.swap(true, Ordering::SeqCst) {
        log::info!("[Actions] Quitting");
        app.exit(0);
    }
}

/// Called when the main window is destroyed.
pub fn main_window_destroyed<R: Runtime>(app: &AppHandle<R>) {
    let tray = app.tray_by_id(crate::tray::TRAY_ID).is_some();
    // TODO: [TECH-DEBT] the webview opens a queue session (`session.open`) and nothing sends
    // `session.close` when its window goes away (docs/SERVE.md asks for it, except with
    // background transcription); the lease keeps renewing until the app quits.
    match after_main_closed(tray) {
        AfterMainClosed::KeepRunning => log::info!("[Actions] Main window closed — {{ tray: true }}"),
        AfterMainClosed::Quit => quit(app),
    }
}

/// Actions a launch argument may request.
pub const ACTIONS: &[&str] = &["record-dialog", "show"];

/// The allowlisted value of `--action NAME` or `--action=NAME`, if any.
pub fn parse_action<S: AsRef<str>>(args: &[S]) -> Option<String> {
    let mut iter = args.iter().map(AsRef::as_ref);
    while let Some(arg) = iter.next() {
        let value = match arg.strip_prefix("--action=") {
            Some(value) => Some(value),
            None if arg == "--action" => iter.next(),
            None => None,
        };
        if let Some(value) = value {
            return ACTIONS.contains(&value).then(|| value.to_owned());
        }
    }
    None
}

/// Show the main window, creating it again from the app configuration if it was closed.
pub fn show_main<R: Runtime>(app: &AppHandle<R>) {
    show_main_window(app);
}

/// [`show_main`]; `Some(true)` when the window had to be created, `None` when that failed.
fn show_main_window<R: Runtime>(app: &AppHandle<R>) -> Option<bool> {
    let (window, created) = match app.get_webview_window(MAIN_LABEL) {
        Some(window) => (window, false),
        None => match create_main(app) {
            Ok(window) => (window, true),
            Err(error) => {
                log::error!("[Actions] Main window not recreated — {{ error: {error} }}");
                return None;
            }
        },
    };
    let _ = window.unminimize();
    let _ = window.show();
    let _ = window.set_focus();
    Some(created)
}

fn create_main<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<WebviewWindow<R>> {
    let config = app
        .config()
        .app
        .windows
        .iter()
        .find(|window| window.label == MAIN_LABEL)
        .cloned()
        .ok_or(tauri::Error::WindowNotFound)?;
    log::info!("[Actions] Recreating the main window");
    WebviewWindowBuilder::from_config(app, &config)?.build()
}

/// Bring the window forward and tell the webview what was asked.
pub fn dispatch<R: Runtime>(app: &AppHandle<R>, action: &str) {
    log::info!("[Actions] Dispatching — {{ action: {action} }}");
    let created = show_main_window(app);
    if action == "show" {
        return;
    }
    if created == Some(true) {
        // A new webview is not listening yet: it reads the action with `take_pending_action`.
        if let Some(state) = app.try_state::<crate::commands::AppState>() {
            *state.pending_action.lock().unwrap_or_else(|poisoned| poisoned.into_inner()) = Some(action.to_owned());
        }
        return;
    }
    if let Err(error) = app.emit_to(MAIN_LABEL, ACTION_EVENT, json!({ "action": action })) {
        log::warn!("[Actions] Action not delivered — {{ action: {action}, error: {error} }}");
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use tauri::utils::config::WindowConfig;

    /// The app's own main window, as `tauri.conf.json` declares it.
    fn main_window_config() -> WindowConfig {
        let config: serde_json::Value = serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        let main = config["app"]["windows"].as_array().unwrap().iter().find(|w| w["label"] == MAIN_LABEL).unwrap();
        serde_json::from_value(main.clone()).unwrap()
    }

    fn mock_app() -> tauri::App<tauri::test::MockRuntime> {
        let mut context = tauri::test::mock_context(tauri::test::noop_assets());
        context.config_mut().app.windows = vec![main_window_config()];
        tauri::test::mock_builder().build(context).unwrap()
    }

    /// The state the bug left behind: the main window was closed (destroyed) while the recording
    /// capsule exists, so the app keeps running with no main window. (The mock runtime only
    /// forgets a destroyed window inside `run`, so the test starts from that state directly.)
    #[test]
    fn reopening_brings_back_the_main_window_when_only_the_capsule_is_left() {
        let app = mock_app();
        let handle = app.handle();
        let capsule = WebviewWindowBuilder::new(handle, crate::commands::CAPSULE_LABEL, tauri::WebviewUrl::default())
            .build()
            .unwrap();
        assert!(handle.get_webview_window(MAIN_LABEL).is_none());
        show_main(handle); // Tray "Open MyNotes", a second launch, or any action.
        assert!(handle.get_webview_window(MAIN_LABEL).is_some(), "show_main built the configured main window");
        show_main(handle); // Already there: shown, not duplicated.
        assert_eq!(handle.webview_windows().len(), 2);
        capsule.destroy().unwrap();
    }

    #[test]
    fn an_action_for_a_recreated_window_waits_for_its_webview() {
        let app = mock_app();
        let handle = app.handle();
        handle.manage(crate::commands::AppState::for_tests());
        dispatch(handle, "record-dialog");
        let state = handle.state::<crate::commands::AppState>();
        assert_eq!(
            state.pending_action.lock().unwrap().as_deref(),
            Some("record-dialog"),
            "read by take_pending_action"
        );
    }

    #[test]
    fn closing_the_main_window_quits_only_when_no_tray_can_reopen_it() {
        assert_eq!(after_main_closed(true), AfterMainClosed::KeepRunning);
        assert_eq!(after_main_closed(false), AfterMainClosed::Quit);
    }

    #[test]
    fn reads_both_argument_forms_and_only_known_actions() {
        assert_eq!(parse_action(&["mynotes-desktop", "--action", "record-dialog"]), Some("record-dialog".into()));
        assert_eq!(parse_action(&["mynotes-desktop", "--action=show"]), Some("show".into()));
        assert_eq!(parse_action(&["mynotes-desktop", "--action", "rm -rf"]), None);
        assert_eq!(parse_action(&["mynotes-desktop", "--action"]), None);
        assert_eq!(parse_action::<&str>(&[]), None);
    }
}

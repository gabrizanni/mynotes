//! App actions requested from outside the webview: a second launch (`--action record-dialog`,
//! which is how Hyprland and other Wayland key binds reach the app), the tray, or the global
//! shortcut. Each one shows the main window and emits `app://action {action}` to it.

use serde_json::json;
use tauri::{AppHandle, Emitter, Manager, Runtime};
use tauri_plugin_log::log;

use crate::events::ACTION_EVENT;

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

pub fn show_main<R: Runtime>(app: &AppHandle<R>) {
    if let Some(window) = app.get_webview_window("main") {
        let _ = window.unminimize();
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Bring the window forward and tell the webview what was asked.
pub fn dispatch<R: Runtime>(app: &AppHandle<R>, action: &str) {
    log::info!("[Actions] Dispatching — {{ action: {action} }}");
    show_main(app);
    if action != "show" {
        if let Err(error) = app.emit_to("main", ACTION_EVENT, json!({ "action": action })) {
            log::warn!("[Actions] Action not delivered — {{ action: {action}, error: {error} }}");
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_both_argument_forms_and_only_known_actions() {
        assert_eq!(parse_action(&["mynotes-desktop", "--action", "record-dialog"]), Some("record-dialog".into()));
        assert_eq!(parse_action(&["mynotes-desktop", "--action=show"]), Some("show".into()));
        assert_eq!(parse_action(&["mynotes-desktop", "--action", "rm -rf"]), None);
        assert_eq!(parse_action(&["mynotes-desktop", "--action"]), None);
        assert_eq!(parse_action::<&str>(&[]), None);
    }
}

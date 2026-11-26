//! Tray icon: recording state and quick actions.
//!
//! Status: minimal. The menu holds "Open MyNotes" and "Quit"; recording items arrive with the
//! supervisor. On by default on Linux and Windows, off on macOS where Capture already owns a
//! menu-bar icon.
//!
//! TODO(tray): status line, Record now, Stop & save, Next lesson, Automatic recording toggle,
//! a red-dot variant of the icon while recording, localized labels from the webview language.

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime};

pub fn enabled_by_default() -> bool {
    !cfg!(target_os = "macos")
}

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let open = MenuItem::with_id(app, "open", "Open MyNotes", true, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&open, &quit])?;
    let mut builder = TrayIconBuilder::with_id("main").tooltip("MyNotes").menu(&menu).on_menu_event(|app, event| {
        match event.id().as_ref() {
            "open" => {
                if let Some(window) = app.get_webview_window("main") {
                    let _ = window.show();
                    let _ = window.set_focus();
                }
            }
            "quit" => app.exit(0),
            _ => {}
        }
    });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

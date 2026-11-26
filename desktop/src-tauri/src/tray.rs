//! Tray icon: recording state and quick actions.
//!
//! On by default on Linux and Windows; off on macOS, where Capture already owns a menu-bar icon
//! (`MYNOTES_DESKTOP_TRAY=1` turns it on there, `0` off anywhere). On Linux the tray needs a
//! StatusNotifierItem host (waybar's tray module on Hyprland); without one the app runs without.
//!
//! While a lesson records, the icon gets a red dot, the tooltip and the first menu line name the
//! lesson, and "Stop recording" is enabled. Labels are English for now.

use tauri::image::Image;
use tauri::menu::{Menu, MenuItem, PredefinedMenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Manager, Runtime};
use tauri_plugin_log::log;

use crate::events::Recording;

const TRAY_ID: &str = "main";

pub fn enabled_by_default() -> bool {
    match std::env::var("MYNOTES_DESKTOP_TRAY").as_deref() {
        Ok("1") => true,
        Ok("0") => false,
        _ => !cfg!(target_os = "macos"),
    }
}

/// Menu items updated with the recording state.
pub struct TrayHandles<R: Runtime> {
    status: MenuItem<R>,
    stop: MenuItem<R>,
    idle_icon: Option<Image<'static>>,
    recording_icon: Option<Image<'static>>,
}

/// A copy of `icon` with a red dot in its lower-right quarter (RGBA).
pub fn with_recording_dot(width: u32, height: u32, rgba: &[u8]) -> Vec<u8> {
    let mut out = rgba.to_vec();
    let radius = (width.min(height) as f32 * 0.22).max(2.0);
    let (cx, cy) = (width as f32 - radius - 1.0, height as f32 - radius - 1.0);
    for y in 0..height {
        for x in 0..width {
            let (dx, dy) = (x as f32 + 0.5 - cx, y as f32 + 0.5 - cy);
            if dx * dx + dy * dy <= radius * radius {
                let index = ((y * width + x) * 4) as usize;
                if let Some(pixel) = out.get_mut(index..index + 4) {
                    pixel.copy_from_slice(&[0xD9, 0x2D, 0x20, 0xFF]);
                }
            }
        }
    }
    out
}

pub fn build<R: Runtime>(app: &AppHandle<R>) -> tauri::Result<()> {
    let status = MenuItem::with_id(app, "status", "Not recording", false, None::<&str>)?;
    let open = MenuItem::with_id(app, "open", "Open MyNotes", true, None::<&str>)?;
    let record = MenuItem::with_id(app, "record", "Record a lesson…", true, None::<&str>)?;
    let stop = MenuItem::with_id(app, "stop", "Stop recording", false, None::<&str>)?;
    let quit = MenuItem::with_id(app, "quit", "Quit MyNotes", true, None::<&str>)?;
    let separator = PredefinedMenuItem::separator(app)?;
    let separator_end = PredefinedMenuItem::separator(app)?;
    let menu = Menu::with_items(app, &[&status, &separator, &open, &record, &stop, &separator_end, &quit])?;

    let idle_icon = app.default_window_icon().map(|icon| icon.clone().to_owned());
    let recording_icon = idle_icon.as_ref().map(|icon| {
        Image::new_owned(with_recording_dot(icon.width(), icon.height(), icon.rgba()), icon.width(), icon.height())
    });
    let mut builder = TrayIconBuilder::with_id(TRAY_ID).tooltip("MyNotes").menu(&menu).on_menu_event(|app, event| {
        match event.id().as_ref() {
            "open" => crate::actions::show_main(app),
            "record" => crate::actions::dispatch(app, "record-dialog"),
            "stop" => stop_recording(app.clone()),
            "quit" => app.exit(0),
            _ => {}
        }
    });
    if let Some(icon) = idle_icon.clone() {
        builder = builder.icon(icon);
    }
    builder.build(app)?;
    app.manage(TrayHandles { status, stop, idle_icon, recording_icon });
    Ok(())
}

fn stop_recording<R: Runtime>(app: AppHandle<R>) {
    tauri::async_runtime::spawn(async move {
        let Some(state) = app.try_state::<crate::commands::AppState>() else { return };
        let supervisor = state.supervisor.clone();
        if let Err(error) = supervisor.call("capture.stop", None).await {
            log::warn!("[Tray] Stop recording failed — {{ code: {}, message: {} }}", error.code, error.message);
        }
    });
}

/// Reflect the recording state in the tray (no-op when the tray is off).
pub fn set_recording<R: Runtime>(app: &AppHandle<R>, recording: &Recording) {
    let (Some(handles), Some(tray)) = (app.try_state::<TrayHandles<R>>(), app.tray_by_id(TRAY_ID)) else { return };
    let line = match (recording.active, recording.exporting, &recording.title) {
        (true, _, Some(title)) => format!("Recording: {title}"),
        (true, _, None) => "Recording".to_owned(),
        (false, true, _) => "Saving the recording…".to_owned(),
        (false, false, _) => "Not recording".to_owned(),
    };
    let _ = handles.status.set_text(&line);
    let _ = handles.stop.set_enabled(recording.active);
    let _ = tray.set_tooltip(Some(if recording.active { format!("MyNotes · {line}") } else { "MyNotes".to_owned() }));
    let icon = if recording.active { handles.recording_icon.clone() } else { handles.idle_icon.clone() };
    if icon.is_some() {
        let _ = tray.set_icon(icon);
    }
}

#[cfg(test)]
mod tests {
    use super::with_recording_dot;

    #[test]
    fn the_dot_is_red_in_the_corner_and_leaves_the_rest() {
        let (w, h) = (32u32, 32u32);
        let blank = vec![0u8; (w * h * 4) as usize];
        let dotted = with_recording_dot(w, h, &blank);
        let pixel = |x: u32, y: u32| &dotted[((y * w + x) * 4) as usize..((y * w + x) * 4 + 4) as usize];
        assert_eq!(pixel(w - 6, h - 6), [0xD9, 0x2D, 0x20, 0xFF]);
        assert_eq!(pixel(2, 2), [0, 0, 0, 0]);
        assert_eq!(dotted.len(), blank.len());
    }
}

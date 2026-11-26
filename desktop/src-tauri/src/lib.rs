//! MyNotes Desktop: the Tauri shell around the local MyNotes backend.
//!
//! Module map:
//! - `backend::protocol`: JSON-RPC line framing and message types (pure, tested);
//! - `backend::methods`: the protocol-1 method table and lanes, checked against the server;
//! - `backend::locate`: which Python runs `mynotes serve`, and the login-shell `PATH`;
//! - `backend::supervisor`: the child process (spawn, handshake, ping, restart, shutdown);
//! - `commands`: what the webviews may call (`rpc_call` allowlist, paths, setup, status);
//! - `events`: supervisor reports → Tauri events, tray, capsule, course folders, notifications;
//! - `paths`: open/reveal validation: local-only prefixes, allowed roots, file types (tested);
//! - `installer`: first-run `setup_runtime.py` with progress;
//! - `tray`, `capsule`, `actions`: native surfaces; `selfcheck`: the headless proof.

pub mod actions;
pub mod backend;
pub mod capsule;
pub mod commands;
pub mod events;
pub mod installer;
pub mod paths;
pub mod selfcheck;
pub mod tray;

use std::sync::{Arc, Mutex};

use tauri::{Manager, RunEvent};
use tauri_plugin_log::log;

use backend::supervisor::{Supervisor, SupervisorConfig};
use commands::{AppState, CAPSULE_LABEL};

/// Global shortcut for "record a lesson" where the platform allows one (not on Wayland).
#[cfg(desktop)]
const RECORD_SHORTCUT: &str = "CommandOrControl+Alt+Shift+R";

/// Wayland sessions get no global shortcuts from the plugin (X11 only on Linux); a compositor
/// bind to `mynotes-desktop --action record-dialog` replaces it (see the README).
#[cfg(desktop)]
fn global_shortcuts_supported() -> bool {
    if !cfg!(target_os = "linux") {
        return true;
    }
    std::env::var_os("WAYLAND_DISPLAY").is_none() && std::env::var("XDG_SESSION_TYPE").as_deref() != Ok("wayland")
}

#[cfg(desktop)]
fn register_global_shortcut(app: &tauri::AppHandle) {
    use tauri_plugin_global_shortcut::{GlobalShortcutExt, ShortcutState};
    if !global_shortcuts_supported() {
        log::info!(
            "[App] Global shortcut skipped on Wayland — {{ hint: bind `mynotes-desktop --action record-dialog` }}"
        );
        return;
    }
    let registered = app.global_shortcut().on_shortcut(RECORD_SHORTCUT, |app, _shortcut, event| {
        if event.state == ShortcutState::Pressed {
            actions::dispatch(app, "record-dialog");
        }
    });
    if let Err(error) = registered {
        log::warn!("[App] Global shortcut unavailable — {{ shortcut: {RECORD_SHORTCUT}, error: {error} }}");
    }
}

fn log_plugin() -> tauri::plugin::TauriPlugin<tauri::Wry> {
    use tauri_plugin_log::{Target, TargetKind};
    tauri_plugin_log::Builder::new()
        .targets([
            Target::new(TargetKind::Stdout),
            Target::new(TargetKind::LogDir { file_name: Some("mynotes-desktop".into()) }),
        ])
        .level(log::LevelFilter::Info)
        .max_file_size(2 * 1024 * 1024)
        .build()
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let args: Vec<String> = std::env::args().collect();
    if selfcheck::requested(&args, std::env::var(selfcheck::ENV).ok().as_deref()) {
        std::process::exit(selfcheck::run());
    }

    let builder = tauri::Builder::default()
        // First: a second launch forwards its `--action` here and exits.
        .plugin(tauri_plugin_single_instance::init(|app, argv, _cwd| match actions::parse_action(&argv) {
            Some(action) => actions::dispatch(app, &action),
            None => actions::show_main(app),
        }))
        .plugin(log_plugin())
        .plugin(
            tauri_plugin_window_state::Builder::default()
                // Wayland cannot restore positions; size is what students notice.
                .with_state_flags(tauri_plugin_window_state::StateFlags::SIZE)
                .with_denylist(&[CAPSULE_LABEL])
                .build(),
        )
        .plugin(tauri_plugin_dialog::init())
        .plugin(tauri_plugin_notification::init());
    #[cfg(desktop)]
    let builder = builder.plugin(tauri_plugin_global_shortcut::Builder::new().build());

    let app = builder
        .setup(move |app| {
            let roots = Arc::new(paths::AllowedRoots::from_environment());
            let sink = Arc::new(events::TauriSink::new(app.handle().clone(), Arc::clone(&roots)));
            let supervisor =
                Supervisor::new(SupervisorConfig::default(), Arc::new(backend::locate::launch_from_environment), sink);
            app.manage(AppState {
                supervisor: supervisor.clone(),
                roots,
                installer: installer::Installer::new(),
                pending_action: Mutex::new(actions::parse_action(&args)),
                session: commands::SessionIntent::default(),
            });
            tauri::async_runtime::spawn(async move { supervisor.start() });
            if tray::enabled_by_default() {
                // A missing StatusNotifier host (some Linux desktops) must not stop the app.
                if let Err(error) = tray::build(app.handle()) {
                    log::warn!("[App] Tray unavailable — {{ error: {error} }}");
                }
            }
            #[cfg(desktop)]
            register_global_shortcut(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::rpc_call,
            commands::backend_status,
            commands::backend_restart,
            commands::open_path,
            commands::reveal_path,
            commands::setup_status,
            commands::setup_run,
            commands::take_pending_action,
        ])
        .build(tauri::generate_context!())
        .expect("error while building MyNotes Desktop");

    app.run(|app, event| {
        if let RunEvent::Exit = event {
            // Leave no backend or setup process behind.
            if let Some(state) = app.try_state::<AppState>() {
                let supervisor = state.supervisor.clone();
                tauri::async_runtime::block_on(supervisor.shutdown());
                state.installer.shutdown();
            }
        }
    });
}

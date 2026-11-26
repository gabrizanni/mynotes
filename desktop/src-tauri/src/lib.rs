//! MyNotes Desktop: the Tauri shell around the local MyNotes backend.
//!
//! Module map:
//! - `backend::protocol`: JSON-RPC line framing and message types (pure, tested);
//! - `backend::locate`: which Python runs `mynotes serve` (pure, tested);
//! - `backend::supervisor`: the child process (stub, see its TODO list);
//! - `commands`: what the webview may call (`rpc_call` allowlist, path checks);
//! - `paths`: open/reveal validation (tested);
//! - `tray`, `installer`: platform features (minimal / stub).

pub mod backend;
pub mod commands;
pub mod installer;
pub mod paths;
pub mod tray;

use commands::AppState;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .manage(AppState { supervisor: backend::supervisor::Supervisor::new() })
        .setup(|app| {
            if tray::enabled_by_default() {
                // A missing StatusNotifier host (some Linux desktops) must not stop the app.
                if let Err(error) = tray::build(app.handle()) {
                    eprintln!("mynotes-desktop: tray unavailable: {error}");
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![commands::rpc_call, commands::open_path, commands::reveal_path])
        .run(tauri::generate_context!())
        .expect("error while running MyNotes Desktop");
}

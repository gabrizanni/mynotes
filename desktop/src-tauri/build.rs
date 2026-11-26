fn main() {
    // App commands get `allow-<command>` permissions; capabilities/*.json grant them per window,
    // so the capsule window cannot reach setup or file opening.
    let manifest = tauri_build::AppManifest::new().commands(&[
        "rpc_call",
        "backend_status",
        "backend_restart",
        "open_path",
        "reveal_path",
        "setup_status",
        "setup_run",
        "take_pending_action",
    ]);
    tauri_build::try_build(tauri_build::Attributes::new().app_manifest(manifest)).expect("failed to run tauri-build");
}

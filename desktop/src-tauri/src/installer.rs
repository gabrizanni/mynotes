//! First-run installer.
//!
//! Status: **stub**.
//!
//! TODO(installer):
//! - macOS: bundle a pinned `uv` plus the release payload and run `setup_runtime.py
//!   --provider X --workspace W --progress-file F` exactly as the native app does, reading
//!   its progress file every 0.5 s and emitting `setup://progress` events;
//! - Linux: same flow with the Linux payload (ARCHITECTURE.md section B);
//! - Windows: point the student to the Inno Setup runtime installer.
//!
//! The webview already renders the progress pill from `setup.status` (see `src/rpc`).

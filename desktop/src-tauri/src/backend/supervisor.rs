//! Supervising the `mynotes serve --stdio` child process.
//!
//! Status: **stub**. The public surface is fixed so `commands.rs` and the webview can be wired
//! now; the process management lands with the `serve` server (PR A).
//!
//! TODO(supervisor), in this order:
//! - build PATH from the login shell, like the macOS app does, so provider CLIs are found;
//! - spawn the command from [`super::locate::locate_python`] with `serve --stdio`, in its own
//!   process group on Unix (`libc::setpgid`, killed with `killpg`) and a Job Object on Windows;
//! - frame stdin/stdout with [`super::protocol::encode`] and [`super::protocol::LineDecoder`];
//!   stderr goes to the log file;
//! - handshake with `system.hello` and refuse a protocol mismatch
//!   ([`super::protocol::check_hello`]);
//! - ping every 15 s; restart with exponential backoff from 0.5 s to 30 s; after 5 crashes in
//!   2 minutes stop and surface a failure screen with the logs;
//! - on restart fail every pending call with [`super::protocol::codes::BACKEND_RESTARTED`];
//! - forward notifications to the webview as the `rpc://notification` Tauri event;
//! - on `config.changed`, pass the linked course folders to
//!   [`crate::paths::AllowedRoots::set_course_folders`] so `open_path` can reach them.

use std::time::Duration;

use serde_json::Value;

use super::protocol::{codes, RpcError};

/// Backoff schedule used once restarts are implemented (kept here so it is unit-tested now).
pub fn restart_delay(consecutive_crashes: u32) -> Duration {
    let millis = 500u64.saturating_mul(1u64 << consecutive_crashes.min(6));
    Duration::from_millis(millis.min(30_000))
}

/// Crashes tolerated inside [`CRASH_WINDOW`] before the app stops restarting.
pub const MAX_CRASHES: usize = 5;
pub const CRASH_WINDOW: Duration = Duration::from_secs(120);
pub const PING_INTERVAL: Duration = Duration::from_secs(15);

#[derive(Debug, Default)]
pub struct Supervisor {
    _private: (),
}

impl Supervisor {
    pub fn new() -> Self {
        Self::default()
    }

    /// Send one request and wait for its response.
    ///
    /// TODO(supervisor): route through the live child process. Until then every call fails
    /// with `BACKEND_UNAVAILABLE`, which the webview shows as "MyNotes is starting".
    pub async fn call(&self, method: &str, _params: Option<Value>) -> Result<Value, RpcError> {
        Err(RpcError::new(codes::BACKEND_UNAVAILABLE, format!("backend not connected yet ({method})")))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn backoff_doubles_from_half_a_second_and_caps_at_thirty() {
        assert_eq!(restart_delay(0), Duration::from_millis(500));
        assert_eq!(restart_delay(1), Duration::from_secs(1));
        assert_eq!(restart_delay(4), Duration::from_secs(8));
        assert_eq!(restart_delay(6), Duration::from_secs(30));
        assert_eq!(restart_delay(40), Duration::from_secs(30));
    }
}

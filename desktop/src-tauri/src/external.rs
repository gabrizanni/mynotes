//! What the app opens outside itself: web pages in the default browser and the system settings
//! panes for the microphone. The webview has no opener permission; it asks Rust, which checks.
//!
//! - **Links.** Only `https` URLs on [`ALLOWED_HOSTS`] (the agents' official install guides),
//!   with no user name, password or port. Anything else is refused before the browser starts.
//! - **Settings.** macOS and Windows open a settings URL (`x-apple.systempreferences:`,
//!   `ms-settings:`); Linux runs the first sound settings program found on `PATH` (GNOME, KDE,
//!   then PulseAudio Volume Control). Both are pure here and unit-tested on every platform.

use std::ffi::OsStr;
use std::path::PathBuf;

use crate::backend::locate::{find_on_path, Platform};

/// Hosts the onboarding links to (`GUIDES` in `src/features/onboarding/platform.ts`).
pub const ALLOWED_HOSTS: &[&str] = &["docs.claude.com", "github.com", "antigravity.google"];

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum ExternalError {
    #[error("only https links to the install guides are opened")]
    LinkRefused,
    #[error("unknown settings pane: {0}")]
    UnknownPane(String),
    #[error("no sound settings program was found (GNOME Settings, KDE System Settings or pavucontrol)")]
    NoSettingsProgram,
}

/// The checked URL, ready for the browser.
pub fn check_link(raw: &str) -> Result<tauri::Url, ExternalError> {
    let url = tauri::Url::parse(raw).map_err(|_| ExternalError::LinkRefused)?;
    let host_allowed = url.host_str().is_some_and(|host| ALLOWED_HOSTS.contains(&host));
    let plain = url.username().is_empty() && url.password().is_none() && url.port().is_none();
    if url.scheme() == "https" && host_allowed && plain {
        Ok(url)
    } else {
        Err(ExternalError::LinkRefused)
    }
}

/// A settings pane the onboarding may open (`SettingsPane` in `platform.ts`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum SettingsPane {
    Microphone,
    Privacy,
    Sound,
}

impl SettingsPane {
    pub fn parse(raw: &str) -> Result<Self, ExternalError> {
        match raw {
            "microphone" => Ok(Self::Microphone),
            "privacy" => Ok(Self::Privacy),
            "sound" => Ok(Self::Sound),
            other => Err(ExternalError::UnknownPane(other.chars().take(32).collect())),
        }
    }
}

/// How a pane is opened on one platform.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SettingsTarget {
    Url(&'static str),
    Program { program: PathBuf, args: Vec<&'static str> },
}

/// Linux sound settings, in order of preference: GNOME, KDE Plasma, PulseAudio Volume Control.
/// Linux has no microphone permission; every pane opens the input devices.
pub const LINUX_SETTINGS: &[(&str, &[&str])] =
    &[("gnome-control-center", &["sound"]), ("systemsettings", &["kcm_pulseaudio"]), ("pavucontrol", &["--tab=4"])];

pub fn settings_target(
    platform: Platform,
    pane: SettingsPane,
    path: Option<&OsStr>,
) -> Result<SettingsTarget, ExternalError> {
    let url = match (platform, pane) {
        (Platform::MacOs, SettingsPane::Microphone | SettingsPane::Privacy) => {
            "x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"
        }
        (Platform::MacOs, SettingsPane::Sound) => "x-apple.systempreferences:com.apple.preference.sound",
        (Platform::Windows, SettingsPane::Microphone) => "ms-settings:privacy-microphone",
        (Platform::Windows, SettingsPane::Privacy) => "ms-settings:privacy",
        (Platform::Windows, SettingsPane::Sound) => "ms-settings:sound",
        (Platform::Linux, _) => {
            let path = path.ok_or(ExternalError::NoSettingsProgram)?;
            return LINUX_SETTINGS
                .iter()
                .find_map(|(name, args)| {
                    find_on_path(name, path).map(|program| SettingsTarget::Program { program, args: args.to_vec() })
                })
                .ok_or(ExternalError::NoSettingsProgram);
        }
    };
    Ok(SettingsTarget::Url(url))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn only_https_links_to_the_install_guides_open() {
        for allowed in [
            "https://docs.claude.com/en/docs/claude-code/setup",
            "https://github.com/openai/codex",
            "https://antigravity.google",
        ] {
            assert!(check_link(allowed).is_ok(), "{allowed}");
        }
        for refused in [
            "http://github.com/openai/codex",
            "https://github.com.evil.example/openai/codex",
            "https://evil.example/?https://github.com",
            "https://user:secret@github.com/openai/codex",
            "https://github.com:8443/openai/codex",
            "javascript:alert(1)",
            "file:///etc/passwd",
            "ms-settings:privacy",
            "github.com/openai/codex",
            "",
        ] {
            assert_eq!(check_link(refused), Err(ExternalError::LinkRefused), "{refused}");
        }
    }

    #[test]
    fn panes_open_the_platform_settings() {
        let pane = |raw| SettingsPane::parse(raw).unwrap();
        assert_eq!(
            settings_target(Platform::MacOs, pane("microphone"), None),
            Ok(SettingsTarget::Url("x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone"))
        );
        assert_eq!(
            settings_target(Platform::Windows, pane("microphone"), None),
            Ok(SettingsTarget::Url("ms-settings:privacy-microphone"))
        );
        assert_eq!(
            settings_target(Platform::Windows, pane("sound"), None),
            Ok(SettingsTarget::Url("ms-settings:sound"))
        );
        assert!(matches!(SettingsPane::parse("../../bin/sh"), Err(ExternalError::UnknownPane(_))));
    }

    #[cfg(unix)]
    #[test]
    fn linux_runs_the_first_sound_settings_program_on_path() {
        use std::os::unix::fs::PermissionsExt;
        let dir = std::env::temp_dir().join(format!("mynotes-desktop-settings-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        std::fs::create_dir_all(&dir).unwrap();
        let path = dir.as_os_str();
        assert_eq!(
            settings_target(Platform::Linux, SettingsPane::Microphone, Some(path)),
            Err(ExternalError::NoSettingsProgram)
        );
        for name in ["pavucontrol", "systemsettings"] {
            let program = dir.join(name);
            std::fs::write(&program, "#!/bin/sh\n").unwrap();
            std::fs::set_permissions(&program, std::fs::Permissions::from_mode(0o755)).unwrap();
        }
        assert_eq!(
            settings_target(Platform::Linux, SettingsPane::Microphone, Some(path)),
            Ok(SettingsTarget::Program { program: dir.join("systemsettings"), args: vec!["kcm_pulseaudio"] }),
            "KDE before pavucontrol when GNOME is absent"
        );
        std::fs::remove_dir_all(dir).unwrap();
    }
}

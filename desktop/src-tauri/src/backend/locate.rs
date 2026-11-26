//! Finding the Python that runs `mynotes serve --stdio`.
//!
//! Lookup order (ARCHITECTURE.md section C, "Finding Python"):
//! 1. `MYNOTES_DESKTOP_PYTHON`, an explicit interpreter path;
//! 2. in development builds, `uv run --project <repo>` from the source checkout;
//! 3. the installed runtime: macOS and Linux `<runtime home>/app/current/venv/bin/python`,
//!    Windows `<install dir>\runtime\python.exe` (Inno Setup layout).
//!
//! The function is pure: the environment and the file system are injected, so the order is
//! unit-tested on every platform.

use std::path::{Path, PathBuf};

pub const PYTHON_ENV: &str = "MYNOTES_DESKTOP_PYTHON";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Platform {
    MacOs,
    Linux,
    Windows,
}

impl Platform {
    pub fn current() -> Self {
        if cfg!(target_os = "macos") {
            Self::MacOs
        } else if cfg!(target_os = "windows") {
            Self::Windows
        } else {
            Self::Linux
        }
    }
}

/// How to start the backend; `serve_args` are appended by the supervisor.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum PythonCommand {
    /// Run `program -I -X utf8 -m mynotes …`.
    Interpreter(PathBuf),
    /// Run `uv run --project <project> mynotes …` (development only).
    UvProject { uv: PathBuf, project: PathBuf },
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum LocateError {
    #[error("{PYTHON_ENV} points to {0}, which does not exist")]
    EnvPathMissing(PathBuf),
    #[error("MyNotes is not installed yet")]
    NotInstalled,
}

/// Everything the lookup needs from the outside world.
pub struct LocateInput<'a> {
    pub platform: Platform,
    pub env_python: Option<PathBuf>,
    /// Source checkout, only set in development builds.
    pub dev_repo: Option<PathBuf>,
    /// `uv` found on PATH, only consulted together with `dev_repo`.
    pub uv: Option<PathBuf>,
    pub home: Option<PathBuf>,
    /// `MYNOTES_HOME`, which overrides the runtime home like the Python side does.
    pub mynotes_home: Option<PathBuf>,
    /// Directory of the running executable (Windows install layout).
    pub exe_dir: Option<PathBuf>,
    pub exists: &'a dyn Fn(&Path) -> bool,
}

/// The runtime home used by `setup_runtime.py` on each platform.
pub fn runtime_home(platform: Platform, home: &Path, mynotes_home: Option<&Path>) -> PathBuf {
    if let Some(explicit) = mynotes_home {
        return explicit.to_path_buf();
    }
    match platform {
        Platform::MacOs => home.join("Library/Application Support/MyNotes"),
        // Mirrors workspace.py and the Linux plan in ARCHITECTURE.md section B.
        Platform::Linux => home.join(".local/share/mynotes"),
        Platform::Windows => home.join("AppData/Local/MyNotes"),
    }
}

pub fn locate_python(input: &LocateInput<'_>) -> Result<PythonCommand, LocateError> {
    if let Some(explicit) = &input.env_python {
        return if (input.exists)(explicit) {
            Ok(PythonCommand::Interpreter(explicit.clone()))
        } else {
            Err(LocateError::EnvPathMissing(explicit.clone()))
        };
    }
    if let (Some(repo), Some(uv)) = (&input.dev_repo, &input.uv) {
        if (input.exists)(&repo.join("pyproject.toml")) {
            return Ok(PythonCommand::UvProject { uv: uv.clone(), project: repo.clone() });
        }
    }
    for candidate in installed_candidates(input) {
        if (input.exists)(&candidate) {
            return Ok(PythonCommand::Interpreter(candidate));
        }
    }
    Err(LocateError::NotInstalled)
}

fn installed_candidates(input: &LocateInput<'_>) -> Vec<PathBuf> {
    match input.platform {
        Platform::Windows => input
            .exe_dir
            .iter()
            .flat_map(|dir| [dir.join("..").join("runtime").join("python.exe"), dir.join("runtime").join("python.exe")])
            .collect(),
        Platform::MacOs | Platform::Linux => input
            .home
            .iter()
            .map(|home| {
                runtime_home(input.platform, home, input.mynotes_home.as_deref()).join("app/current/venv/bin/python")
            })
            .collect(),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::collections::HashSet;

    fn input<'a>(platform: Platform, exists: &'a dyn Fn(&Path) -> bool) -> LocateInput<'a> {
        LocateInput {
            platform,
            env_python: None,
            dev_repo: None,
            uv: None,
            home: Some(PathBuf::from("/srv/student")),
            mynotes_home: None,
            exe_dir: None,
            exists,
        }
    }

    fn files(paths: &[&str]) -> impl Fn(&Path) -> bool {
        let set: HashSet<PathBuf> = paths.iter().map(PathBuf::from).collect();
        move |path: &Path| set.contains(path)
    }

    #[test]
    fn explicit_environment_path_wins() {
        let exists = files(&["/opt/py/bin/python", "/srv/student/.local/share/mynotes/app/current/venv/bin/python"]);
        let mut lookup = input(Platform::Linux, &exists);
        lookup.env_python = Some(PathBuf::from("/opt/py/bin/python"));
        assert_eq!(locate_python(&lookup), Ok(PythonCommand::Interpreter("/opt/py/bin/python".into())));
    }

    #[test]
    fn a_missing_explicit_path_is_an_error_not_a_fallback() {
        let exists = files(&["/srv/student/.local/share/mynotes/app/current/venv/bin/python"]);
        let mut lookup = input(Platform::Linux, &exists);
        lookup.env_python = Some(PathBuf::from("/nowhere/python"));
        assert_eq!(locate_python(&lookup), Err(LocateError::EnvPathMissing("/nowhere/python".into())));
    }

    #[test]
    fn development_builds_use_uv_on_the_checkout() {
        let exists = files(&["/src/mynotes/pyproject.toml"]);
        let mut lookup = input(Platform::Linux, &exists);
        lookup.dev_repo = Some(PathBuf::from("/src/mynotes"));
        lookup.uv = Some(PathBuf::from("/usr/bin/uv"));
        assert_eq!(
            locate_python(&lookup),
            Ok(PythonCommand::UvProject { uv: "/usr/bin/uv".into(), project: "/src/mynotes".into() })
        );
    }

    #[test]
    fn installed_runtime_per_platform() {
        let mac = files(&["/srv/student/Library/Application Support/MyNotes/app/current/venv/bin/python"]);
        assert!(matches!(locate_python(&input(Platform::MacOs, &mac)), Ok(PythonCommand::Interpreter(_))));

        let linux = files(&["/srv/student/.local/share/mynotes/app/current/venv/bin/python"]);
        assert!(matches!(locate_python(&input(Platform::Linux, &linux)), Ok(PythonCommand::Interpreter(_))));

        let windows = files(&["C:/Program Files/MyNotes/app/runtime/python.exe"]);
        let mut lookup = input(Platform::Windows, &windows);
        lookup.exe_dir = Some(PathBuf::from("C:/Program Files/MyNotes/app"));
        assert_eq!(
            locate_python(&lookup),
            Ok(PythonCommand::Interpreter("C:/Program Files/MyNotes/app/runtime/python.exe".into()))
        );
    }

    #[test]
    fn mynotes_home_overrides_the_runtime_home() {
        let exists = files(&["/data/mynotes/app/current/venv/bin/python"]);
        let mut lookup = input(Platform::Linux, &exists);
        lookup.mynotes_home = Some(PathBuf::from("/data/mynotes"));
        assert_eq!(
            locate_python(&lookup),
            Ok(PythonCommand::Interpreter("/data/mynotes/app/current/venv/bin/python".into()))
        );
    }

    #[test]
    fn nothing_found_means_not_installed() {
        let exists = files(&[]);
        assert_eq!(locate_python(&input(Platform::Linux, &exists)), Err(LocateError::NotInstalled));
    }
}

//! Finding the Python that runs `mynotes serve --stdio`.
//!
//! Lookup order (ARCHITECTURE.md section C, "Finding Python"):
//! 1. `MYNOTES_DESKTOP_PYTHON`, an explicit interpreter path;
//! 2. `MYNOTES_DESKTOP_REPO`, a source checkout run with `uv run --project <repo>` (development);
//! 3. the installed runtime: macOS and Linux `<runtime home>/app/current/venv/bin/python`,
//!    Windows `<install dir>\runtime\python.exe` (Inno Setup layout).
//!
//! An explicit variable that points nowhere is an error, never a silent fallback to another
//! Python. [`locate_python`] is pure: the environment and the file system are injected, so the
//! order is unit-tested on every platform. [`locate_from_environment`] feeds it the real ones.
//!
//! GUI apps on macOS (and some Linux launchers) start with a minimal `PATH`, so provider CLIs and
//! `uv` would not be found. [`login_shell_path`] asks the login shell for its `PATH`, as the macOS
//! setup app does (`zsh -lic`), and [`merge_path`] puts it in front of the inherited one.

use std::ffi::{OsStr, OsString};
use std::path::{Path, PathBuf};
use std::sync::OnceLock;
use std::time::{Duration, Instant};

use super::supervisor::{Launch, LaunchError};

pub const PYTHON_ENV: &str = "MYNOTES_DESKTOP_PYTHON";
pub const REPO_ENV: &str = "MYNOTES_DESKTOP_REPO";
/// How long the login shell may take to print its `PATH`.
pub const LOGIN_SHELL_TIMEOUT: Duration = Duration::from_secs(5);
const PATH_MARKER: &str = "__MYNOTES_DESKTOP_PATH__=";

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

impl PythonCommand {
    /// Program and arguments that start `mynotes serve --stdio`.
    pub fn serve_invocation(&self) -> (PathBuf, Vec<OsString>) {
        match self {
            Self::Interpreter(python) => {
                (python.clone(), ["-I", "-X", "utf8", "-m", "mynotes", "serve", "--stdio"].map(OsString::from).to_vec())
            }
            Self::UvProject { uv, project } => {
                let mut args =
                    vec![OsString::from("run"), OsString::from("--project"), project.clone().into_os_string()];
                args.extend(["mynotes", "serve", "--stdio"].map(OsString::from));
                (uv.clone(), args)
            }
        }
    }

    /// Short label for logs and the self-check (no user paths beyond the program name).
    pub fn kind(&self) -> &'static str {
        match self {
            Self::Interpreter(_) => "interpreter",
            Self::UvProject { .. } => "uv-project",
        }
    }
}

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum LocateError {
    #[error("{PYTHON_ENV} points to {0}, which does not exist")]
    EnvPathMissing(PathBuf),
    #[error("{REPO_ENV} points to {0}, which has no pyproject.toml")]
    RepoMissing(PathBuf),
    #[error("{REPO_ENV} is set but `uv` is not on PATH")]
    UvMissing,
    #[error("MyNotes is not installed yet")]
    NotInstalled,
}

impl LocateError {
    /// Stable code for the webview (`NOT_INSTALLED` sends the student to setup).
    pub fn code(&self) -> &'static str {
        match self {
            Self::NotInstalled => "NOT_INSTALLED",
            Self::EnvPathMissing(_) | Self::RepoMissing(_) | Self::UvMissing => "MISCONFIGURED",
        }
    }
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
    if let Some(repo) = &input.dev_repo {
        if !(input.exists)(&repo.join("pyproject.toml")) {
            return Err(LocateError::RepoMissing(repo.clone()));
        }
        let uv = input.uv.clone().ok_or(LocateError::UvMissing)?;
        return Ok(PythonCommand::UvProject { uv, project: repo.clone() });
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

/// First executable called `name` in a `PATH` value (`name.exe` too on Windows).
pub fn find_on_path(name: &str, path: &OsStr) -> Option<PathBuf> {
    let names: Vec<String> =
        if cfg!(windows) { vec![format!("{name}.exe"), name.to_owned()] } else { vec![name.to_owned()] };
    std::env::split_paths(path)
        .filter(|dir| dir.is_absolute())
        .flat_map(|dir| names.iter().map(move |candidate| dir.join(candidate)))
        .find(|candidate| is_executable(candidate))
}

#[cfg(unix)]
fn is_executable(path: &Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    path.metadata().map(|meta| meta.is_file() && meta.permissions().mode() & 0o111 != 0).unwrap_or(false)
}

#[cfg(not(unix))]
fn is_executable(path: &Path) -> bool {
    path.is_file()
}

/// `login` first, then the entries of `inherited` it lacks; duplicates and relative entries go.
pub fn merge_path(login: Option<&OsStr>, inherited: Option<&OsStr>) -> Option<OsString> {
    let mut seen = Vec::<PathBuf>::new();
    for value in [login, inherited].into_iter().flatten() {
        for dir in std::env::split_paths(value) {
            if dir.is_absolute() && !seen.contains(&dir) {
                seen.push(dir);
            }
        }
    }
    if seen.is_empty() {
        return None;
    }
    std::env::join_paths(seen).ok()
}

/// Extract the `PATH` the login shell printed after [`PATH_MARKER`], ignoring banners.
pub fn parse_login_output(stdout: &str) -> Option<String> {
    stdout
        .lines()
        .rev()
        .find_map(|line| line.trim_end_matches('\r').strip_prefix(PATH_MARKER))
        .map(str::to_owned)
        .filter(|path| !path.trim().is_empty())
}

/// The login shell's `PATH` (`$SHELL -l -i -c`), or `None` after [`LOGIN_SHELL_TIMEOUT`].
///
/// Blocking: call it from a blocking thread. The shell gets no stdin and its stderr is dropped,
/// so an interactive profile cannot hang on a prompt longer than the timeout.
#[cfg(unix)]
pub fn login_shell_path(timeout: Duration) -> Option<String> {
    use std::io::Read;
    use std::process::{Command, Stdio};

    let shell = std::env::var_os("SHELL").filter(|s| !s.is_empty()).unwrap_or_else(|| OsString::from("/bin/sh"));
    let script = format!("printf '\\n{PATH_MARKER}%s\\n' \"$PATH\"");
    let mut child = Command::new(&shell)
        .args(["-l", "-i", "-c", &script])
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::null())
        .spawn()
        .ok()?;
    let started = Instant::now();
    loop {
        match child.try_wait() {
            Ok(Some(_)) => break,
            Ok(None) if started.elapsed() < timeout => std::thread::sleep(Duration::from_millis(25)),
            _ => {
                let _ = child.kill();
                let _ = child.wait();
                return None;
            }
        }
    }
    let mut stdout = String::new();
    child.stdout.take()?.read_to_string(&mut stdout).ok()?;
    parse_login_output(&stdout)
}

#[cfg(not(unix))]
pub fn login_shell_path(_timeout: Duration) -> Option<String> {
    None
}

/// The process `PATH` to give the backend: login shell first (macOS and Linux), then inherited.
pub fn backend_path() -> Option<OsString> {
    let inherited = std::env::var_os("PATH");
    let login = login_shell_path(LOGIN_SHELL_TIMEOUT).map(OsString::from);
    merge_path(login.as_deref(), inherited.as_deref())
}

fn non_empty_env(name: &str) -> Option<PathBuf> {
    std::env::var_os(name).filter(|value| !value.is_empty()).map(PathBuf::from)
}

/// [`locate_python`] with the real environment; `path` is the `PATH` the backend will get.
pub fn locate_from_environment(path: Option<&OsStr>) -> Result<PythonCommand, LocateError> {
    let home = non_empty_env(if cfg!(windows) { "USERPROFILE" } else { "HOME" });
    let exe_dir = std::env::current_exe().ok().and_then(|exe| exe.parent().map(Path::to_path_buf));
    let exists = |candidate: &Path| candidate.exists();
    locate_python(&LocateInput {
        platform: Platform::current(),
        env_python: non_empty_env(PYTHON_ENV),
        dev_repo: non_empty_env(REPO_ENV),
        uv: path.and_then(|value| find_on_path("uv", value)),
        home,
        mynotes_home: non_empty_env("MYNOTES_HOME"),
        exe_dir,
        exists: &exists,
    })
}

static BACKEND_PATH: OnceLock<Option<OsString>> = OnceLock::new();

/// [`backend_path`], computed once per app run (the login shell is slow to start).
pub fn cached_backend_path() -> Option<OsString> {
    BACKEND_PATH.get_or_init(backend_path).clone()
}

/// The supervisor's launch function: the located Python, `serve --stdio`, the merged `PATH`.
pub fn launch_from_environment() -> Result<Launch, LaunchError> {
    let path = cached_backend_path();
    let command = locate_from_environment(path.as_deref())
        .map_err(|error| LaunchError { code: error.code().to_owned(), message: error.to_string() })?;
    let (program, args) = command.serve_invocation();
    let env = path.map(|path| vec![(OsString::from("PATH"), path)]).unwrap_or_default();
    Ok(Launch { program, args, env, cwd: None, kind: command.kind().to_owned() })
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
        assert_eq!(LocateError::NotInstalled.code(), "NOT_INSTALLED");
    }

    #[test]
    fn an_explicit_repo_without_uv_or_pyproject_is_an_error() {
        let exists =
            files(&["/src/mynotes/pyproject.toml", "/srv/student/.local/share/mynotes/app/current/venv/bin/python"]);
        let mut lookup = input(Platform::Linux, &exists);
        lookup.dev_repo = Some(PathBuf::from("/src/mynotes"));
        assert_eq!(locate_python(&lookup), Err(LocateError::UvMissing));
        lookup.dev_repo = Some(PathBuf::from("/src/elsewhere"));
        lookup.uv = Some(PathBuf::from("/usr/bin/uv"));
        assert_eq!(locate_python(&lookup), Err(LocateError::RepoMissing("/src/elsewhere".into())));
    }

    #[test]
    fn serve_invocations_isolate_python_or_use_the_uv_project() {
        let (program, args) = PythonCommand::Interpreter("/opt/py/bin/python".into()).serve_invocation();
        assert_eq!(program, PathBuf::from("/opt/py/bin/python"));
        assert_eq!(args, ["-I", "-X", "utf8", "-m", "mynotes", "serve", "--stdio"].map(OsString::from).to_vec());
        let (program, args) =
            PythonCommand::UvProject { uv: "/usr/bin/uv".into(), project: "/src/mynotes".into() }.serve_invocation();
        assert_eq!(program, PathBuf::from("/usr/bin/uv"));
        assert_eq!(
            args,
            ["run", "--project", "/src/mynotes", "mynotes", "serve", "--stdio"].map(OsString::from).to_vec()
        );
    }

    #[test]
    fn login_output_is_read_after_the_marker_only() {
        let stdout = format!("Welcome banner\nlast login\n\n{PATH_MARKER}/opt/bin:/usr/bin\r\n");
        assert_eq!(parse_login_output(&stdout).as_deref(), Some("/opt/bin:/usr/bin"));
        assert_eq!(parse_login_output("no marker here"), None);
        assert_eq!(parse_login_output(&format!("{PATH_MARKER}  ")), None);
    }

    #[cfg(unix)]
    #[test]
    fn merged_path_puts_the_login_shell_first_without_duplicates() {
        let merged =
            merge_path(Some(OsStr::new("/opt/homebrew/bin:/usr/bin")), Some(OsStr::new("/usr/bin:relative:/bin")));
        assert_eq!(merged, Some(OsString::from("/opt/homebrew/bin:/usr/bin:/bin")));
        assert_eq!(merge_path(None, None), None);
    }

    #[cfg(unix)]
    #[test]
    fn finds_executables_on_a_path_value() {
        let sh = find_on_path("sh", OsStr::new("/nonexistent:/bin:/usr/bin")).expect("sh is on every Unix");
        assert!(sh.ends_with("sh"));
        assert_eq!(find_on_path("definitely-not-a-real-binary-name", OsStr::new("/bin:/usr/bin")), None);
    }

    #[cfg(unix)]
    #[test]
    fn the_login_shell_answers_with_a_path() {
        // Real shell, real profile: proves the marker survives whatever the profile prints.
        let path = login_shell_path(LOGIN_SHELL_TIMEOUT).expect("the login shell printed a PATH");
        assert!(path.split(':').any(|dir| dir.starts_with('/')), "unexpected PATH: {path}");
    }
}

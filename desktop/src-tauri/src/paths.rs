//! Path checks for `open_path` and `reveal_path`.
//!
//! The webview has no opener permission. It asks Rust to open or reveal a path, and Rust agrees
//! only after three gates, in this order:
//!
//! 1. **Lexical gate, before any filesystem call.** The raw string must be a plain local absolute
//!    path. On Windows only drive-letter prefixes (`C:\`, `\\?\C:\`) pass: UNC shares
//!    (`\\host\share`), verbatim UNC (`\\?\UNC\…`), device namespaces (`\\.\…`) and other
//!    verbatim prefixes are refused, because touching them makes Windows authenticate to the
//!    remote host and leak the user's NTLM hash. `..` segments and NUL bytes are refused. The
//!    path must also sit, lexically, under an allowed root.
//! 2. **Canonicalization.** Only then is the path resolved (symlinks, junctions, case).
//! 3. **Root and type gate.** The canonical path must still sit under an allowed root (a
//!    symlink cannot escape) and, for files, carry a document or media extension.
//!
//! Allowed roots are the user's home, the MyNotes runtime home and workspace, plus the course
//! folders the backend reports ([`AllowedRoots::set_course_folders`], called by the supervisor).
//! Residual risk: a junction inside an allowed local root that points to a network share is
//! followed by step 2; roots themselves are never network paths.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::RwLock;

/// Extensions the app may hand to the OS default application or reveal.
pub const OPENABLE_EXTENSIONS: &[&str] =
    &["docx", "pdf", "txt", "md", "m4a", "mp3", "wav", "pptx", "xlsx", "png", "jpg", "jpeg"];

/// Audio the webview may play through the asset protocol (`allow_audio`): the recording types
/// MyNotes imports (`course_links.AUDIO_TYPES` and the ingestion defaults).
pub const PLAYABLE_EXTENSIONS: &[&str] = &["m4a", "mp3", "wav", "aac", "aiff", "flac", "ogg", "caf"];

#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum PathError {
    #[error("the path must be absolute")]
    NotAbsolute,
    #[error("network and device paths are not opened from MyNotes")]
    NotLocal,
    #[error("the path must not contain '..'")]
    Traversal,
    #[error("the path is outside your MyNotes folders")]
    OutsideRoots,
    #[error("the file no longer exists")]
    Missing,
    #[error("this kind of file can't be opened from MyNotes")]
    NotAllowed,
}

/// Which path grammar to apply. The runtime uses [`Flavor::current`]; tests use both on any OS.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Flavor {
    Unix,
    Windows,
}

impl Flavor {
    pub fn current() -> Self {
        if cfg!(windows) {
            Self::Windows
        } else {
            Self::Unix
        }
    }
}

/// Windows path prefixes, mirroring `std::path::Prefix` so the rule is testable on any OS.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum WindowsPrefix {
    /// `C:` (absolute only when followed by a separator).
    Disk,
    /// `\\?\C:`
    VerbatimDisk,
    /// `\\server\share`
    Unc,
    /// `\\?\UNC\server\share`
    VerbatimUnc,
    /// `\\.\device`
    DeviceNs,
    /// `\\?\anything-else`
    Verbatim,
    /// No prefix: relative, or rooted without a drive (`\notes`).
    None,
}

/// Only local drive letters may reach the filesystem.
pub fn windows_prefix_allowed(prefix: WindowsPrefix) -> bool {
    matches!(prefix, WindowsPrefix::Disk | WindowsPrefix::VerbatimDisk)
}

fn is_sep(c: char) -> bool {
    c == '\\' || c == '/'
}

fn starts_with_drive(s: &str) -> bool {
    let mut chars = s.chars();
    matches!((chars.next(), chars.next()), (Some(letter), Some(':')) if letter.is_ascii_alphabetic())
}

/// Classify the prefix of a raw Windows path string, the way `std::path` does on Windows.
pub fn windows_prefix_of(raw: &str) -> WindowsPrefix {
    if let Some(rest) = raw.strip_prefix(r"\\?\") {
        if rest.get(..4).is_some_and(|head| head.eq_ignore_ascii_case(r"UNC\")) {
            return WindowsPrefix::VerbatimUnc;
        }
        return if starts_with_drive(rest) { WindowsPrefix::VerbatimDisk } else { WindowsPrefix::Verbatim };
    }
    if raw.starts_with(r"\\.\") {
        return WindowsPrefix::DeviceNs;
    }
    let mut chars = raw.chars();
    if matches!((chars.next(), chars.next()), (Some(a), Some(b)) if is_sep(a) && is_sep(b)) {
        return WindowsPrefix::Unc;
    }
    if starts_with_drive(raw) {
        WindowsPrefix::Disk
    } else {
        WindowsPrefix::None
    }
}

/// A normalized, comparable path: lower-cased drive and segments on Windows, exact on Unix.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PathKey(Vec<String>);

impl PathKey {
    pub fn is_within(&self, root: &PathKey) -> bool {
        self.0.len() >= root.0.len() && self.0[..root.0.len()] == root.0[..]
    }
}

fn segments(rest: &str, separators: fn(char) -> bool) -> Result<Vec<&str>, PathError> {
    let mut out = Vec::new();
    for segment in rest.split(separators) {
        match segment {
            "" | "." => {}
            ".." => return Err(PathError::Traversal),
            other => out.push(other),
        }
    }
    Ok(out)
}

/// Gate 1 without the root check: the raw string is a plain, local, absolute path.
/// Never touches the filesystem.
pub fn lexical_key(raw: &str, flavor: Flavor) -> Result<PathKey, PathError> {
    if raw.contains('\0') {
        return Err(PathError::NotAllowed);
    }
    match flavor {
        Flavor::Unix => {
            let rest = raw.strip_prefix('/').ok_or(PathError::NotAbsolute)?;
            Ok(PathKey(segments(rest, |c| c == '/')?.into_iter().map(str::to_owned).collect()))
        }
        Flavor::Windows => {
            let prefix = windows_prefix_of(raw);
            if prefix == WindowsPrefix::None {
                return Err(PathError::NotAbsolute);
            }
            if !windows_prefix_allowed(prefix) {
                return Err(PathError::NotLocal);
            }
            let drive_path = if prefix == WindowsPrefix::VerbatimDisk { &raw[4..] } else { raw };
            let (drive, rest) = drive_path.split_at(2);
            // `C:notes.docx` is relative to the drive's current directory.
            if !rest.starts_with(|c: char| if prefix == WindowsPrefix::VerbatimDisk { c == '\\' } else { is_sep(c) }) {
                return Err(PathError::NotAbsolute);
            }
            let mut key = vec![drive.to_ascii_lowercase()];
            key.extend(segments(rest, is_sep)?.into_iter().map(str::to_lowercase));
            Ok(PathKey(key))
        }
    }
}

/// Gate 1 complete: plain local absolute path, lexically under one of `roots`.
pub fn lexical_check(raw: &str, flavor: Flavor, roots: &[PathKey]) -> Result<PathKey, PathError> {
    let key = lexical_key(raw, flavor)?;
    if roots.iter().any(|root| key.is_within(root)) {
        Ok(key)
    } else {
        Err(PathError::OutsideRoots)
    }
}

/// On Windows, confirm the lexical verdict with `std::path` itself before touching the disk.
#[cfg(windows)]
fn std_prefix_is_local(raw: &str) -> bool {
    use std::path::{Component, Prefix};
    match Path::new(raw).components().next() {
        Some(Component::Prefix(prefix)) => matches!(prefix.kind(), Prefix::Disk(_) | Prefix::VerbatimDisk(_)),
        _ => false,
    }
}

#[cfg(not(windows))]
fn std_prefix_is_local(raw: &str) -> bool {
    Path::new(raw).is_absolute()
}

/// Folders the student's files may be opened from. Shared by commands; updated by the supervisor.
#[derive(Debug, Default)]
pub struct AllowedRoots {
    base: RwLock<Vec<PathKey>>,
    courses: RwLock<Vec<PathKey>>,
    /// Folders the backend reports recordings in, per reporting topic; audio plays only from them.
    audio: RwLock<BTreeMap<&'static str, Vec<PathKey>>>,
}

/// Register a root: refused unless it passes gate 1 and exists; stored in canonical form.
fn root_key(path: &Path, flavor: Flavor) -> Option<PathKey> {
    let raw = path.to_str()?;
    lexical_key(raw, flavor).ok()?;
    if !std_prefix_is_local(raw) {
        return None;
    }
    let canonical = path.canonicalize().ok()?;
    lexical_key(canonical.to_str()?, flavor).ok()
}

impl AllowedRoots {
    pub fn new(base: &[PathBuf]) -> Self {
        let flavor = Flavor::current();
        let keys = base.iter().filter_map(|path| root_key(path, flavor)).collect();
        Self { base: RwLock::new(keys), courses: RwLock::new(Vec::new()), audio: RwLock::new(BTreeMap::new()) }
    }

    /// Home, the MyNotes runtime home and the workspace (`MYNOTES_WORKSPACE`, or
    /// `~/Documents/MyNotes`, the onboarding default).
    pub fn from_environment() -> Self {
        let home = std::env::var_os(if cfg!(windows) { "USERPROFILE" } else { "HOME" }).map(PathBuf::from);
        let mut roots = Vec::new();
        if let Some(home) = &home {
            roots.push(home.clone());
            roots.push(home.join("Documents").join("MyNotes"));
            let mynotes_home = std::env::var_os("MYNOTES_HOME").map(PathBuf::from);
            roots.push(crate::backend::locate::runtime_home(
                crate::backend::locate::Platform::current(),
                home,
                mynotes_home.as_deref(),
            ));
        }
        if let Some(workspace) = std::env::var_os("MYNOTES_WORKSPACE") {
            roots.push(PathBuf::from(workspace));
        }
        Self::new(&roots)
    }

    /// Replace the course folders (linked material folders reported by the backend).
    /// Network or missing folders are dropped, not trusted.
    pub fn set_course_folders(&self, folders: &[PathBuf]) {
        let flavor = Flavor::current();
        let keys = folders.iter().filter_map(|path| root_key(path, flavor)).collect();
        if let Ok(mut courses) = self.courses.write() {
            *courses = keys;
        }
    }

    /// Replace the folders of one audio source (`"config"`, `"capture"`, `"library"`): each
    /// topic reports its own folders, and the playable set is their union.
    pub fn set_audio_folders(&self, source: &'static str, folders: &[PathBuf]) {
        let flavor = Flavor::current();
        let keys = folders.iter().filter_map(|path| root_key(path, flavor)).collect();
        if let Ok(mut audio) = self.audio.write() {
            audio.insert(source, keys);
        }
    }

    pub fn audio_keys(&self) -> Vec<PathKey> {
        self.audio.read().map(|audio| audio.values().flatten().cloned().collect()).unwrap_or_default()
    }

    pub fn keys(&self) -> Vec<PathKey> {
        let mut keys = self.base.read().map(|base| base.clone()).unwrap_or_default();
        keys.extend(self.courses.read().map(|courses| courses.clone()).unwrap_or_default());
        keys
    }
}

/// Gates 1 to 3, minus the type rule.
fn resolve(raw: &str, roots: &[PathKey]) -> Result<PathBuf, PathError> {
    let flavor = Flavor::current();
    lexical_check(raw, flavor, roots)?;
    if !std_prefix_is_local(raw) {
        return Err(PathError::NotLocal);
    }
    let canonical = Path::new(raw).canonicalize().map_err(|_| PathError::Missing)?;
    let key = canonical.to_str().ok_or(PathError::NotAllowed).and_then(|text| lexical_key(text, flavor))?;
    if roots.iter().any(|root| key.is_within(root)) {
        Ok(canonical)
    } else {
        Err(PathError::OutsideRoots)
    }
}

fn has_openable_extension(path: &Path) -> bool {
    path.extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| OPENABLE_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str()))
        .unwrap_or(false)
}

/// A document or media file the student may open with the default application.
pub fn check_openable(raw: &str, roots: &AllowedRoots) -> Result<PathBuf, PathError> {
    let path = resolve(raw, &roots.keys())?;
    if path.is_file() && has_openable_extension(&path) {
        Ok(path)
    } else {
        Err(PathError::NotAllowed)
    }
}

/// A recording the webview may play: gates 1 to 3 against the audio folders only (never the
/// home folder), then an audio extension. The canonical path is what the asset scope allows.
pub fn check_playable(raw: &str, roots: &AllowedRoots) -> Result<PathBuf, PathError> {
    let path = resolve(raw, &roots.audio_keys())?;
    let audio = path
        .extension()
        .and_then(|ext| ext.to_str())
        .is_some_and(|ext| PLAYABLE_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str()));
    if path.is_file() && audio {
        Ok(path)
    } else {
        Err(PathError::NotAllowed)
    }
}

/// A folder, or a document or media file, to show in the system file manager.
pub fn check_revealable(raw: &str, roots: &AllowedRoots) -> Result<PathBuf, PathError> {
    let path = resolve(raw, &roots.keys())?;
    if path.is_dir() || (path.is_file() && has_openable_extension(&path)) {
        Ok(path)
    } else {
        Err(PathError::NotAllowed)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn win(raw: &str) -> Result<PathKey, PathError> {
        lexical_key(raw, Flavor::Windows)
    }

    #[test]
    fn classifies_windows_prefixes_like_std() {
        assert_eq!(windows_prefix_of(r"C:\Data\student"), WindowsPrefix::Disk);
        assert_eq!(windows_prefix_of(r"\\?\C:\Data\student"), WindowsPrefix::VerbatimDisk);
        assert_eq!(windows_prefix_of(r"\\fileserver\share\notes.docx"), WindowsPrefix::Unc);
        assert_eq!(windows_prefix_of("//fileserver/share/notes.docx"), WindowsPrefix::Unc);
        assert_eq!(windows_prefix_of(r"\\?\UNC\fileserver\share\notes.docx"), WindowsPrefix::VerbatimUnc);
        assert_eq!(windows_prefix_of(r"\\?\unc\fileserver\share"), WindowsPrefix::VerbatimUnc);
        assert_eq!(windows_prefix_of(r"\\.\PhysicalDrive0"), WindowsPrefix::DeviceNs);
        assert_eq!(windows_prefix_of(r"\\?\GLOBALROOT\Device\HarddiskVolume1"), WindowsPrefix::Verbatim);
        assert_eq!(windows_prefix_of(r"\notes.docx"), WindowsPrefix::None);
        assert_eq!(windows_prefix_of("notes.docx"), WindowsPrefix::None);
    }

    #[test]
    fn refuses_network_and_device_paths_before_any_filesystem_call() {
        for raw in [
            r"\\fileserver\share\notes.docx",
            "//fileserver/share/notes.docx",
            r"\\?\UNC\fileserver\share\notes.docx",
            r"\\.\PhysicalDrive0",
            r"\\.\pipe\mynotes",
            r"\\?\GLOBALROOT\Device\HarddiskVolume1\notes.docx",
        ] {
            assert_eq!(win(raw), Err(PathError::NotLocal), "{raw}");
        }
    }

    #[test]
    fn refuses_relative_windows_paths() {
        assert_eq!(win("notes.docx"), Err(PathError::NotAbsolute));
        assert_eq!(win(r"\notes.docx"), Err(PathError::NotAbsolute));
        assert_eq!(win("C:notes.docx"), Err(PathError::NotAbsolute));
    }

    #[test]
    fn accepts_local_drives_case_insensitively() {
        let root = win(r"C:\Data\Student").unwrap();
        for raw in [
            r"C:\Data\Student\Notes\STAT.docx",
            "c:/data/student/notes/stat.docx",
            r"\\?\C:\Data\Student\Notes\STAT.docx",
        ] {
            let key = win(raw).unwrap();
            assert!(key.is_within(&root), "{raw}");
        }
        assert_eq!(lexical_check(r"D:\Other\STAT.docx", Flavor::Windows, &[root]), Err(PathError::OutsideRoots));
    }

    #[test]
    fn refuses_parent_segments_and_nul_bytes() {
        assert_eq!(win(r"C:\Data\Student\..\Other\notes.docx"), Err(PathError::Traversal));
        assert_eq!(lexical_key("/srv/student/../other/notes.docx", Flavor::Unix), Err(PathError::Traversal));
        assert_eq!(lexical_key("/srv/student/notes\0.docx", Flavor::Unix), Err(PathError::NotAllowed));
    }

    #[test]
    fn unix_paths_must_be_absolute_and_under_a_root() {
        let root = lexical_key("/srv/student", Flavor::Unix).unwrap();
        assert_eq!(lexical_key("notes.docx", Flavor::Unix), Err(PathError::NotAbsolute));
        assert!(lexical_check("/srv/student/./MyNotes//notes.docx", Flavor::Unix, std::slice::from_ref(&root)).is_ok());
        assert_eq!(
            lexical_check("/srv/studentx/notes.docx", Flavor::Unix, std::slice::from_ref(&root)),
            Err(PathError::OutsideRoots)
        );
        assert_eq!(lexical_check("/etc/passwd", Flavor::Unix, &[root]), Err(PathError::OutsideRoots));
    }

    /// Filesystem cases run where symlinks need no privilege.
    #[cfg(unix)]
    mod fs_cases {
        use super::super::*;
        use std::fs;

        fn scratch(name: &str) -> PathBuf {
            let dir = std::env::temp_dir().join(format!("mynotes-desktop-paths-{}-{name}", std::process::id()));
            let _ = fs::remove_dir_all(&dir);
            fs::create_dir_all(&dir).unwrap();
            dir
        }

        #[test]
        fn opens_documents_inside_roots_only() {
            let dir = scratch("open");
            let root = dir.join("MyNotes");
            fs::create_dir_all(&root).unwrap();
            let notes = root.join("STAT_COURSE_NOTES.docx");
            let script = root.join("run.sh");
            fs::write(&notes, b"docx").unwrap();
            fs::write(&script, b"#!/bin/sh").unwrap();
            let roots = AllowedRoots::new(std::slice::from_ref(&root));
            assert_eq!(check_openable(notes.to_str().unwrap(), &roots), Ok(notes.canonicalize().unwrap()));
            assert_eq!(check_openable(script.to_str().unwrap(), &roots), Err(PathError::NotAllowed));
            assert_eq!(check_openable(root.to_str().unwrap(), &roots), Err(PathError::NotAllowed));
            assert_eq!(check_revealable(root.to_str().unwrap(), &roots), Ok(root.canonicalize().unwrap()));
            assert_eq!(check_revealable(script.to_str().unwrap(), &roots), Err(PathError::NotAllowed));
            fs::remove_dir_all(dir).unwrap();
        }

        #[test]
        fn refuses_symlinks_that_escape_the_roots() {
            let dir = scratch("escape");
            let root = dir.join("MyNotes");
            let outside = dir.join("elsewhere");
            fs::create_dir_all(&root).unwrap();
            fs::create_dir_all(&outside).unwrap();
            let secret = outside.join("secret.docx");
            fs::write(&secret, b"docx").unwrap();
            let link = root.join("looks-local.docx");
            std::os::unix::fs::symlink(&secret, &link).unwrap();
            let roots = AllowedRoots::new(std::slice::from_ref(&root));
            assert_eq!(check_openable(link.to_str().unwrap(), &roots), Err(PathError::OutsideRoots));
            assert_eq!(check_openable(secret.to_str().unwrap(), &roots), Err(PathError::OutsideRoots));
            fs::remove_dir_all(dir).unwrap();
        }

        #[test]
        fn course_folders_extend_the_roots_and_missing_files_fail_cleanly() {
            let dir = scratch("courses");
            let course = dir.join("Statistics material");
            fs::create_dir_all(&course).unwrap();
            let slides = course.join("Lecture 3.PDF");
            fs::write(&slides, b"%PDF").unwrap();
            let roots = AllowedRoots::new(&[]);
            assert_eq!(check_openable(slides.to_str().unwrap(), &roots), Err(PathError::OutsideRoots));
            roots.set_course_folders(std::slice::from_ref(&course));
            assert!(check_openable(slides.to_str().unwrap(), &roots).is_ok());
            assert_eq!(check_openable(course.join("gone.docx").to_str().unwrap(), &roots), Err(PathError::Missing));
            assert_eq!(check_openable("relative/notes.docx", &roots), Err(PathError::NotAbsolute));
            fs::remove_dir_all(dir).unwrap();
        }

        #[test]
        fn audio_plays_only_from_the_reported_recording_folders() {
            let dir = scratch("audio");
            let recordings = dir.join("recordings");
            let notes = dir.join("notes");
            fs::create_dir_all(&recordings).unwrap();
            fs::create_dir_all(&notes).unwrap();
            let lesson = recordings.join("STAT-L01.M4A");
            fs::write(&lesson, b"audio").unwrap();
            let elsewhere = notes.join("memo.m4a");
            fs::write(&elsewhere, b"audio").unwrap();
            let handout = recordings.join("handout.docx");
            fs::write(&handout, b"docx").unwrap();
            // The home folder is an open/reveal root, never an audio one.
            let roots = AllowedRoots::new(std::slice::from_ref(&dir));
            assert_eq!(check_playable(lesson.to_str().unwrap(), &roots), Err(PathError::OutsideRoots));
            roots.set_audio_folders("config", std::slice::from_ref(&recordings));
            assert_eq!(check_playable(lesson.to_str().unwrap(), &roots), Ok(lesson.canonicalize().unwrap()));
            assert_eq!(check_playable(elsewhere.to_str().unwrap(), &roots), Err(PathError::OutsideRoots));
            assert_eq!(check_playable(handout.to_str().unwrap(), &roots), Err(PathError::NotAllowed));
            assert_eq!(check_playable(recordings.to_str().unwrap(), &roots), Err(PathError::NotAllowed));
            let traversal = format!("{}/../notes/memo.m4a", recordings.display());
            assert_eq!(check_playable(&traversal, &roots), Err(PathError::Traversal));
            // Each topic keeps its own folders; replacing one leaves the others.
            roots.set_audio_folders("capture", std::slice::from_ref(&notes));
            assert!(check_playable(elsewhere.to_str().unwrap(), &roots).is_ok());
            assert!(check_playable(lesson.to_str().unwrap(), &roots).is_ok());
            roots.set_audio_folders("config", &[]);
            assert_eq!(check_playable(lesson.to_str().unwrap(), &roots), Err(PathError::OutsideRoots));
            fs::remove_dir_all(dir).unwrap();
        }
    }

    /// On Windows the runtime also asks `std::path`; both verdicts must agree.
    #[cfg(windows)]
    #[test]
    fn std_agrees_with_the_portable_classifier() {
        for raw in [r"C:\Data\Student\notes.docx", r"\\?\C:\Data\Student\notes.docx"] {
            assert!(std_prefix_is_local(raw), "{raw}");
            assert!(windows_prefix_allowed(windows_prefix_of(raw)), "{raw}");
        }
        for raw in [
            r"\\fileserver\share\notes.docx",
            r"\\?\UNC\fileserver\share\notes.docx",
            r"\\.\PhysicalDrive0",
            r"\\?\GLOBALROOT\x",
        ] {
            assert!(!std_prefix_is_local(raw), "{raw}");
            assert!(!windows_prefix_allowed(windows_prefix_of(raw)), "{raw}");
        }
    }
}

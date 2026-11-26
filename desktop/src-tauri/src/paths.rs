//! Path checks for `open_path` and `reveal_path`.
//!
//! The webview has no opener permission. It asks Rust to open a file, and Rust only agrees when
//! the path is absolute, exists after resolving symlinks, and (for opening) has a document or
//! media extension. Executables, scripts and bundles are never opened.

use std::path::{Path, PathBuf};

/// Extensions the app may hand to the OS default application.
pub const OPENABLE_EXTENSIONS: &[&str] =
    &["docx", "pdf", "txt", "md", "m4a", "mp3", "wav", "pptx", "xlsx", "png", "jpg", "jpeg"];

#[derive(Debug, thiserror::Error, PartialEq, Eq)]
pub enum PathError {
    #[error("the path must be absolute")]
    NotAbsolute,
    #[error("the file no longer exists")]
    Missing,
    #[error("this kind of file can't be opened from MyNotes")]
    NotAllowed,
}

/// Resolve a path the student may open with the default application.
pub fn check_openable(raw: &str) -> Result<PathBuf, PathError> {
    let path = resolve(raw)?;
    if !path.is_file() {
        return Err(PathError::NotAllowed);
    }
    let allowed = path
        .extension()
        .and_then(|ext| ext.to_str())
        .map(|ext| OPENABLE_EXTENSIONS.contains(&ext.to_ascii_lowercase().as_str()))
        .unwrap_or(false);
    if allowed {
        Ok(path)
    } else {
        Err(PathError::NotAllowed)
    }
}

/// Resolve a file or folder to show in the system file manager.
pub fn check_revealable(raw: &str) -> Result<PathBuf, PathError> {
    resolve(raw)
}

fn resolve(raw: &str) -> Result<PathBuf, PathError> {
    let path = Path::new(raw);
    if !path.is_absolute() {
        return Err(PathError::NotAbsolute);
    }
    path.canonicalize().map_err(|_| PathError::Missing)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    fn scratch(name: &str) -> PathBuf {
        let dir = std::env::temp_dir().join(format!("mynotes-desktop-paths-{}-{name}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        dir
    }

    #[test]
    fn opens_documents_only() {
        let dir = scratch("open");
        let notes = dir.join("STAT_COURSE_NOTES.docx");
        let script = dir.join("run.sh");
        fs::write(&notes, b"docx").unwrap();
        fs::write(&script, b"#!/bin/sh").unwrap();
        assert_eq!(check_openable(notes.to_str().unwrap()), Ok(notes.canonicalize().unwrap()));
        assert_eq!(check_openable(script.to_str().unwrap()), Err(PathError::NotAllowed));
        assert_eq!(check_openable(dir.to_str().unwrap()), Err(PathError::NotAllowed));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn rejects_relative_and_missing_paths() {
        assert_eq!(check_openable("notes.docx"), Err(PathError::NotAbsolute));
        let dir = scratch("missing");
        let gone = dir.join("gone.docx");
        assert_eq!(check_openable(gone.to_str().unwrap()), Err(PathError::Missing));
        assert_eq!(check_revealable(dir.to_str().unwrap()), Ok(dir.canonicalize().unwrap()));
        fs::remove_dir_all(dir).unwrap();
    }

    #[test]
    fn extension_check_ignores_case() {
        let dir = scratch("case");
        let slides = dir.join("Lecture 3.PDF");
        fs::write(&slides, b"%PDF").unwrap();
        assert!(check_openable(slides.to_str().unwrap()).is_ok());
        fs::remove_dir_all(dir).unwrap();
    }
}

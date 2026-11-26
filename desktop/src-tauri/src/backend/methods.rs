//! The protocol-1 method table of `mynotes serve`, with each method's lane.
//!
//! Generated from `docs/SERVE.md` ("Methods") and `mynotes serve --list-methods --json`; the test
//! `method_table_matches_the_server` re-runs that command against a serve checkout and fails on
//! any difference, so a new server method must be added here (and reviewed) before the webview
//! can call it.
//!
//! The lane decides how long the app waits for an answer: the server answers its own lane at
//! once, fast methods read files, slow methods may copy or hash audio, and job methods return a
//! job id immediately.

use std::time::Duration;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Lane {
    /// `system.*`, `session.*`, `watch.*`, `job.*`, answered by the server itself.
    Server,
    /// Read-only commands computed in the server.
    Fast,
    /// Commands that write local files, probe agent CLIs, copy or hash audio.
    Slow,
    /// Long work in a separate process; the call itself only returns `{jobId, job}`.
    Job,
}

impl Lane {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Server => "server",
            Self::Fast => "fast",
            Self::Slow => "slow",
            Self::Job => "job",
        }
    }
}

/// Methods only the Rust shell may send: the supervisor owns the backend's lifecycle.
pub const RUST_ONLY_METHODS: &[&str] = &["system.shutdown"];

/// Every method of protocol 1, sorted by name.
pub const METHODS: &[(&str, Lane)] = &[
    // The assistant panel's chat turns: `start` and `send` answer `{sessionId, turnId}` at once,
    // then stream `assistant.delta|tool|done|error`.
    ("assistant.cancel", Lane::Fast),
    ("assistant.get", Lane::Fast),
    ("assistant.list", Lane::Fast),
    ("assistant.send", Lane::Job),
    ("assistant.start", Lane::Job),
    ("capture.addLesson", Lane::Slow),
    ("capture.assign", Lane::Slow),
    ("capture.configure", Lane::Slow),
    ("capture.confirmTest", Lane::Slow),
    ("capture.connect", Lane::Job),
    ("capture.dashboard", Lane::Fast),
    ("capture.disable", Lane::Job),
    ("capture.editLesson", Lane::Slow),
    ("capture.enable", Lane::Job),
    ("capture.import", Lane::Slow),
    ("capture.pause", Lane::Slow),
    ("capture.pending", Lane::Fast),
    ("capture.recover", Lane::Slow),
    ("capture.resetLesson", Lane::Slow),
    ("capture.resume", Lane::Slow),
    ("capture.schedule", Lane::Fast),
    ("capture.setImportMode", Lane::Slow),
    ("capture.source", Lane::Fast),
    ("capture.start", Lane::Slow),
    ("capture.status", Lane::Fast),
    ("capture.stop", Lane::Slow),
    ("capture.subscribe", Lane::Slow),
    ("capture.sync", Lane::Job),
    ("capture.test", Lane::Slow),
    ("capture.unsubscribe", Lane::Slow),
    ("config.get", Lane::Fast),
    ("course.add", Lane::Slow),
    ("course.deferContext", Lane::Slow),
    ("course.edit", Lane::Slow),
    ("course.link", Lane::Slow),
    ("course.pending", Lane::Fast),
    ("doctor.get", Lane::Slow),
    ("job.cancel", Lane::Server),
    ("job.get", Lane::Server),
    ("job.list", Lane::Server),
    ("job.wait", Lane::Server),
    ("lesson.process", Lane::Job),
    ("library.associate", Lane::Slow),
    ("library.associateAudio", Lane::Slow),
    ("library.courseProfile", Lane::Fast),
    ("library.download", Lane::Job),
    ("library.editLesson", Lane::Slow),
    ("library.excludeAudio", Lane::Slow),
    ("library.finish", Lane::Slow),
    ("library.folder", Lane::Slow),
    ("library.importAudio", Lane::Slow),
    ("library.inspectAudio", Lane::Slow),
    ("library.lessons", Lane::Fast),
    ("library.list", Lane::Fast),
    ("library.preferAudio", Lane::Slow),
    ("library.prepareTrash", Lane::Slow),
    ("library.relink", Lane::Slow),
    ("library.rename", Lane::Slow),
    ("library.renameTranscript", Lane::Slow),
    ("library.restoreAudio", Lane::Slow),
    ("library.transcripts", Lane::Fast),
    ("library.verify", Lane::Slow),
    ("memory.reset", Lane::Slow),
    ("memory.show", Lane::Fast),
    ("memory.update", Lane::Slow),
    ("models.catalog", Lane::Fast),
    ("models.refresh", Lane::Job),
    ("notes.batchStatus", Lane::Fast),
    ("notes.history", Lane::Fast),
    ("notes.plan", Lane::Slow),
    ("notes.restore", Lane::Slow),
    ("notes.run", Lane::Job),
    ("notes.undo", Lane::Slow),
    ("preferences.get", Lane::Fast),
    ("preferences.saveModels", Lane::Slow),
    ("profile.save", Lane::Slow),
    ("provider.login", Lane::Job),
    ("provider.status", Lane::Slow),
    ("queue.clear", Lane::Slow),
    ("queue.configure", Lane::Slow),
    ("queue.enqueue", Lane::Slow),
    ("queue.job", Lane::Slow),
    ("queue.pause", Lane::Slow),
    ("queue.resume", Lane::Slow),
    ("queue.status", Lane::Fast),
    ("session.close", Lane::Server),
    ("session.open", Lane::Server),
    ("session.setVisible", Lane::Server),
    ("speech.installModel", Lane::Job),
    ("speech.verify", Lane::Job),
    ("style.edit", Lane::Slow),
    ("style.preview", Lane::Slow),
    ("style.show", Lane::Fast),
    ("system.hello", Lane::Server),
    ("system.ping", Lane::Server),
    ("system.shutdown", Lane::Server),
    ("system.stats", Lane::Server),
    ("transcription.run", Lane::Job),
    ("transcription.status", Lane::Fast),
    ("usage.get", Lane::Fast),
    ("watch.snapshot", Lane::Server),
    ("watch.subscribe", Lane::Server),
    ("watch.unsubscribe", Lane::Server),
    ("word.verifyRenderer", Lane::Job),
];

/// Lane of a protocol-1 method, `None` when the server does not know it.
pub fn lane_of(method: &str) -> Option<Lane> {
    METHODS.binary_search_by(|(name, _)| (*name).cmp(method)).ok().map(|index| METHODS[index].1)
}

/// How long one call may wait for its answer before the app gives up on it.
///
/// `job.wait` blocks on purpose for `timeoutSeconds`, so it gets that plus a margin.
pub fn call_timeout(method: &str, params: Option<&serde_json::Value>) -> Duration {
    if method == "job.wait" {
        let requested =
            params.and_then(|p| p.get("timeoutSeconds")).and_then(serde_json::Value::as_f64).unwrap_or(30.0);
        return Duration::from_secs_f64(requested.clamp(0.0, 3600.0)) + Duration::from_secs(15);
    }
    match lane_of(method) {
        Some(Lane::Server) | Some(Lane::Job) => Duration::from_secs(30),
        Some(Lane::Fast) => Duration::from_secs(60),
        // Importing or hashing a long recording, or a cold doctor probe, can take minutes.
        Some(Lane::Slow) | None => Duration::from_secs(600),
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;
    use std::path::{Path, PathBuf};
    use std::process::Command;

    #[test]
    fn table_is_sorted_and_unique_so_lookups_work() {
        for pair in METHODS.windows(2) {
            assert!(pair[0].0 < pair[1].0, "{} must sort before {}", pair[0].0, pair[1].0);
        }
        assert_eq!(lane_of("capture.dashboard"), Some(Lane::Fast));
        assert_eq!(lane_of("notes.run"), Some(Lane::Job));
        assert_eq!(lane_of("system.hello"), Some(Lane::Server));
        assert_eq!(lane_of("shell.exec"), None);
    }

    #[test]
    fn timeouts_follow_the_lane_and_job_wait_asks_for_its_own() {
        assert_eq!(call_timeout("system.ping", None), Duration::from_secs(30));
        assert_eq!(call_timeout("capture.dashboard", None), Duration::from_secs(60));
        assert_eq!(call_timeout("library.importAudio", None), Duration::from_secs(600));
        assert_eq!(call_timeout("job.wait", Some(&json!({"timeoutSeconds": 120}))), Duration::from_secs(135));
        assert_eq!(call_timeout("job.wait", Some(&json!({"timeoutSeconds": -5}))), Duration::from_secs(15));
    }

    /// The serve checkout: `MYNOTES_SERVE_REPO`, else this repository once the server is in it.
    fn serve_checkout() -> Option<PathBuf> {
        if let Some(explicit) = std::env::var_os("MYNOTES_SERVE_REPO") {
            return Some(PathBuf::from(explicit));
        }
        let repo = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        repo.join("src/mynotes/serve.py").is_file().then_some(repo)
    }

    #[test]
    fn method_table_matches_the_server() {
        let Some(repo) = serve_checkout() else {
            eprintln!("SKIPPED method_table_matches_the_server: no serve checkout (set MYNOTES_SERVE_REPO)");
            return;
        };
        let output = Command::new("uv")
            .args(["run", "--project"])
            .arg(&repo)
            .args(["mynotes", "serve", "--list-methods", "--json"])
            .output()
            .expect("`uv` must be on PATH to compare the method table with the server");
        assert!(output.status.success(), "list-methods failed: {}", String::from_utf8_lossy(&output.stderr));
        let listed: serde_json::Value = serde_json::from_slice(&output.stdout).expect("list-methods prints JSON");
        assert_eq!(listed["protocol"], 1, "the server speaks another protocol");
        let mut server: Vec<(String, String)> = listed["methods"]
            .as_array()
            .expect("methods array")
            .iter()
            .map(|m| {
                (m["name"].as_str().unwrap_or_default().to_owned(), m["lane"].as_str().unwrap_or_default().to_owned())
            })
            .collect();
        server.sort();
        let ours: Vec<(String, String)> =
            METHODS.iter().map(|(name, lane)| ((*name).to_owned(), lane.as_str().to_owned())).collect();
        assert_eq!(ours, server, "update METHODS from `mynotes serve --list-methods --json`");
    }
}

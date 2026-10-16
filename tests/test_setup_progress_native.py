"""Compile the pure native presentation helper; never launch/install the app."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin" or shutil.which("swiftc") is None, reason="macOS Swift compiler required")


@pytest.fixture(scope="module")
def presentation(tmp_path_factory):
    root = tmp_path_factory.mktemp("native-progress")
    main = root / "main.swift"
    main.write_text('''import Foundation
let input = try JSONSerialization.jsonObject(with: FileHandle.standardInput.readDataToEndOfFile()) as! [String: Any]
let state = input["state"] as! [String: Any]
func date(_ name: String) -> Date { Date(timeIntervalSince1970: (input[name] as! NSNumber).doubleValue) }
if let view = SetupProgressPresentation.read(state, modifiedAt: date("modified"), startedAt: date("started"), now: date("now")) {
    let result: [String: Any] = ["title": view.title, "detail": view.detail, "fraction": view.fraction as Any? ?? NSNull(),
                               "numbers": view.downloadNumbers as Any? ?? NSNull(), "activity": view.activity, "waiting": view.waitingForNetwork]
    FileHandle.standardOutput.write(try JSONSerialization.data(withJSONObject: result))
} else { print("null") }
''')
    helper = Path(__file__).parents[1] / "setup/Sources/MyNotesSetup/SetupProgress.swift"
    binary = root / "progress-test"
    compiled = subprocess.run([shutil.which("swiftc"), str(helper), str(main), "-o", str(binary)], capture_output=True, text=True)
    assert compiled.returncode == 0, compiled.stderr
    def read(state, *, now=1_700_000_100, modified=1_700_000_100, started=1_700_000_000):
        result = subprocess.run([str(binary)], input=json.dumps({"state": state, "now": now, "modified": modified, "started": started}), capture_output=True, text=True)
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    return read


def test_real_numeric_byte_progress_and_payload_detail_are_visible(presentation):
    state = {"stage": "components", "phase": "download", "message": "Downloading libreoffice", "downloaded_bytes": 6 * 1048576,
             "total_bytes": 30 * 1048576, "last_data_at": 1_700_000_083}
    view = presentation(state)
    assert view["fraction"] == pytest.approx(.2)
    assert "6.0 / 30.0 MiB" in view["numbers"] and "20.0%" in view["numbers"]
    assert view["detail"] == state["message"]
    assert "Word" in view["title"] and "17 s fa" in view["activity"]
    assert not view["waiting"]


@pytest.mark.parametrize("phase,message", [("verify", "Checking model file 6/30"), ("prepare", "Preparing libreoffice")])
def test_warm_preparation_is_indeterminate_and_not_mislabeled_download(presentation, phase, message):
    view = presentation({"stage": "components", "phase": phase, "message": message, "completed": 6, "total": 30}, now=1_700_000_200)
    assert view["fraction"] is None and view["numbers"] is None
    assert "Scarico" not in view["title"]
    assert not view["waiting"] and view["detail"] == message


def test_saved_partial_bytes_do_not_claim_new_network_data(presentation):
    view = presentation({"stage": "components", "phase": "download", "message": "Downloading libreoffice", "downloaded_bytes": 6 * 1048576,
                         "total_bytes": 30 * 1048576, "started_at": 1_700_000_020, "last_data_at": None})
    assert "6.0 / 30.0 MiB" in view["numbers"]
    assert "Attendo i primi dati" in view["activity"] and "80 s" in view["activity"]
    assert view["waiting"]


def test_stalled_state_uses_last_data_not_heartbeat_and_clears_on_new_data(presentation):
    state = {"stage": "components", "phase": "download", "message": "Downloading libreoffice", "downloaded_bytes": 6 * 1048576,
             "total_bytes": 30 * 1048576, "last_data_at": 1_700_000_020}
    waiting = presentation(state)
    assert waiting["waiting"] and "1 min 20 s fa" in waiting["activity"]
    state["last_data_at"] = 1_700_000_099
    assert not presentation(state)["waiting"]


def test_old_attempt_progress_does_not_replace_current_preparation(presentation):
    assert presentation({"stage": "ready", "completed": 1, "total": 1}, modified=1_699_999_999) is None


def test_legacy_download_counts_are_supported_without_treating_file_counts_as_bytes(presentation):
    legacy = presentation({"stage": "components", "message": "Downloading libreoffice: 6/30 MiB", "completed": 6 * 1048576, "total": 30 * 1048576})
    assert legacy["fraction"] == pytest.approx(.2)
    checking = presentation({"stage": "components", "message": "Checking model file 6/30", "completed": 6, "total": 30})
    assert checking["fraction"] is None and checking["numbers"] is None

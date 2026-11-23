"""Run a spoken fixture through the installed Windows queue and TXT exporter."""
from __future__ import annotations

import argparse
import json
import shutil
import time
import uuid
from pathlib import Path

from mynotes import transcription_queue as queue
from mynotes.onboarding import add_course
from mynotes.recording_library import set_folder
from mynotes.workspace import Workspace


def smoke(workspace: Workspace, audio: Path, collection: Path) -> None:
    workspace.initialize()
    course = add_course(workspace, "Synthetic audio fixture")["course"]
    settings = workspace.configuration_store().load()
    saved = next(item for item in settings["courses"] if item["code"] == course["code"])
    saved["transcription"].update(primary_model="tiny", language="en")
    settings["ingestion"]["stable_seconds"] = 0
    workspace.configuration_store().save(settings)
    set_folder(workspace, collection)
    copied = collection / f"{course['code']}-L01.wav"
    shutil.copy2(audio, copied)
    candidates = [item for item in queue.library(workspace) if Path(item["path"]) == copied]
    if len(candidates) != 1:
        raise RuntimeError("The installed library did not find the audio fixture")
    added = queue.enqueue(workspace, recording_id=candidates[0]["id"])
    if added["added"] != 1:
        raise RuntimeError(f"The installed queue declined the audio fixture: {added}")

    session = str(uuid.uuid4())
    queue.ui_lease(workspace, session)
    runner = queue.launch_runner(workspace)
    try:
        deadline = time.monotonic() + 90
        while time.monotonic() < deadline:
            job, = queue.status(workspace)["jobs"]
            if job["status"] == "COMPLETED":
                transcript = Path(job["export_text"]).read_text(encoding="utf-8")
                if "country" not in transcript.casefold():
                    raise RuntimeError("The installed queue exported unexpected TXT content")
                report = json.loads(Path(job["outputs"]["transcript_json"]).read_text(encoding="utf-8"))
                print(json.dumps({"status": "COMPLETED", "segments": len(report["segments"])}))
                return
            if job["status"] not in {"QUEUED", "RUNNING"}:
                raise RuntimeError(f"The installed queue stopped: {job['status']} — {job.get('error')}")
            queue.ui_lease(workspace, session)
            time.sleep(1)
        raise TimeoutError("The installed queue did not finish within 90 seconds")
    finally:
        queue.ui_lease(workspace, session, opened=False)
        runner.wait(timeout=10)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", type=Path, required=True)
    parser.add_argument("--audio", type=Path, required=True)
    parser.add_argument("--collection", type=Path, required=True)
    arguments = parser.parse_args()
    smoke(Workspace.from_value(arguments.workspace), arguments.audio, arguments.collection)

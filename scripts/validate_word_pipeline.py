#!/usr/bin/env python3
"""Create, render and publish an isolated MyNotes Word smoke test."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw

from mynotes.configuration import course_template
from mynotes.publication import build_word_candidate, publish_word_candidate, publication_history
from mynotes.utils import atomic_json_write, file_sha256
from mynotes.workspace import Workspace


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("workspace")
    root = Path(parser.parse_args().workspace).expanduser().resolve()
    workspace = Workspace.from_value(root)
    workspace.initialize()
    configuration = workspace.configuration_store().load()
    course = course_template()
    course.update({"code": "DEMO1", "name": "Quantitative Demo", "kind": "quantitative"})
    course["folders"]["handout"] = str(root / "output")
    configuration["courses"] = [course]
    workspace.configuration_store().save(configuration)
    run = workspace.runs_dir / "DEMO1-L01"
    run.mkdir(parents=True, exist_ok=True)
    image = run / "tradeoff.png"
    canvas = Image.new("RGB", (1000, 480), "white")
    draw = ImageDraw.Draw(canvas)
    draw.line((80, 400, 900, 80), fill="#0B4F6C", width=12)
    draw.text((100, 40), "Cost-service trade-off", fill="black")
    canvas.save(image)
    pack = {
        "schema_version": 1,
        "lecture_id": "DEMO1-L01",
        "course": {"code": "DEMO1", "name": "Quantitative Demo", "lesson_number": 1},
        "availability": {"materials": True},
        "evidence": [{"id": "S001", "kind": "slide", "content": "Cost-service trade-off", "source_file": "demo.pdf", "page": 1}],
        "visuals": [{"evidence_id": "S001", "image_path": str(image), "source_file": "demo.pdf", "page": 1}],
        "directives": [],
        "sources": [],
        "glossary": [],
    }
    source = run / "source_pack.json"
    atomic_json_write(source, pack)
    notes = {
        "lecture_id": "DEMO1-L01",
        "title": "Service and cost",
        "summary": "A compact quantitative lesson.",
        "bullets": [],
        "table": [["Driver", "Effect"], ["Inventory", "Higher availability"]],
        "formula": r"Q = \frac{D}{\sqrt{2K}}",
        "sections": [{"heading": "Core mechanism", "level": 3, "body": "Capacity and inventory trade cost for responsiveness.", "bullets": ["Use capacity for flexibility.", "Use inventory for immediate availability."], "evidence_ids": ["S001"]}],
        "evidence_coverage": [{"evidence_id": "S001", "disposition": "used", "reason": "core"}],
        "visual_decisions": [{"evidence_id": "S001", "decision": "include", "reason": "Essential trade-off diagram"}],
        "model_knowledge": [],
    }
    candidate_json = run / "lecture_notes.candidate.json"
    atomic_json_write(candidate_json, {"notes": notes, "source_pack": str(source), "source_pack_hash": file_sha256(source), "published_to_word": False})
    built = build_word_candidate(workspace, "DEMO1-L01")
    published = publish_word_candidate(workspace, "DEMO1-L01", Path(built["candidate"]))
    print(json.dumps({"built": built, "published": published, "history": publication_history(workspace)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

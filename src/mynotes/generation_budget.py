"""Durable, independent generation phase allowances.

Receipt directories must already be bound to the requested lessons and generation
by the workflow. This module accounts local attempt clocks; it cannot certify
academic content or grant a retry of a failed managed correction.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from .utils import atomic_json_write, file_sha256

MODEL_PHASE_SECONDS = 1200
LOCAL_CHECK_SECONDS = 120
CLI_PHASE_TIMEOUT = "21m"  # The application watchdog, not CLI rounding, owns 20m.


class GenerationBudgetError(RuntimeError):
    """Uncertain historical consumption must not grant a fresh paid allowance."""


def _seconds(value, label):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0:
        raise GenerationBudgetError(f"Invalid {label} in generation budget")
    return float(value)


def phase_limit(phase: str) -> int:
    return LOCAL_CHECK_SECONDS if phase == "local_checks" else MODEL_PHASE_SECONDS


def invocation_timeout(timeout_seconds: float | None) -> float:
    return min(MODEL_PHASE_SECONDS, MODEL_PHASE_SECONDS if timeout_seconds is None
               else _seconds(timeout_seconds, "remaining phase seconds"))


def _epoch(value):
    try:
        return datetime.strptime(value, "%Y%m%dT%H%M%S%fZ").replace(tzinfo=timezone.utc).timestamp()
    except (TypeError, ValueError) as error:
        raise GenerationBudgetError("Historical attempt lacks a reliable start timestamp") from error


def receipt_consumption(directories, *, paths=()):
    """Read caller-bound receipts, merging nested/copy records without double use.

    Streaming transport saves a cumulative outer record as well as per-failure
    records. Their active intervals overlap and must not be added twice. Unknown
    invocation state, missing durations and modified symlink records fail closed.
    """
    intervals = {}; receipts = []; seen = set()
    groups = [(Path(directory), sorted(Path(directory).rglob("provider-attempts/*.json"))) for directory in directories]
    groups += [(Path(path).parent, [Path(path)]) for path in paths]
    for directory, attempt_paths in groups:
        for path in attempt_paths:
            if path.is_symlink() or not path.resolve().is_relative_to(directory.resolve()):
                raise GenerationBudgetError("Historical attempt is outside its bound checkpoint")
            try:
                record = json.loads(path.read_text())
            except (ValueError, OSError) as error:
                raise GenerationBudgetError("Unreadable historical attempt") from error
            if not isinstance(record, dict):
                raise GenerationBudgetError("Historical attempt record is not an object")
            identity = record.get("attempt_id")
            digest = file_sha256(path)
            if identity in seen:
                # Duplicate files may be copies of one immutable attempt only.
                if not any(r["attempt_id"] == identity and r["sha256"] == digest for r in receipts):
                    raise GenerationBudgetError("Historical attempt identity has conflicting contents")
                continue
            if not isinstance(identity, str) or not identity:
                raise GenerationBudgetError("Historical attempt has no immutable identity")
            seen.add(identity)
            invoked = record.get("invocation_started")
            if invoked is False:
                continue
            if invoked is not True:
                raise GenerationBudgetError("Historical provider invocation state is uncertain")
            phase = record.get("phase")
            if not isinstance(phase, str) or not phase:
                raise GenerationBudgetError("Historical provider phase is uncertain")
            if record.get("start_time_source") == "estimated_legacy_caller":
                raise GenerationBudgetError("Historical attempt start time was estimated; phase consumption requires local review")
            active = _seconds(record.get("active_seconds", record.get("duration_seconds")), "historical phase duration")
            start = _epoch(record.get("started_at"))
            # Active clocks exclude sleep on macOS; never reconstruct them from
            # wall duration or infer completion from a response status.
            intervals.setdefault(phase, []).append((start, start + active))
            receipts.append({"attempt_id": identity, "path": str(path), "sha256": digest, "phase": phase})
    totals = {}
    for phase, values in intervals.items():
        end = None; total = 0.
        for start, stop in sorted(values):
            total += max(0., stop - max(start, end if end is not None else start))
            end = max(stop, end if end is not None else stop)
        totals[phase] = total
    return totals, receipts


class PhaseBudget:
    """Persist consumption before retries, resumes and organization revisions.

    `previous_paths` are ledgers for overlapping lessons of the SAME batch.
    Max merge prevents copied ledger state from being counted repeatedly. New
    work is added to the inherited consumption, never starts a new allowance.
    A crash with an open marker requires explicit local recovery; no fresh call.
    """
    def __init__(self, path: Path, lessons, *, previous_paths=(), receipt_directories=(), receipt_paths=(), legacy_used_seconds=0., permit_local_recovery=False):
        self.path = Path(path); self.lessons = list(lessons); self.used = {}; self.receipts = []; self.model_block_reasons = []
        for prior_path in dict.fromkeys([self.path, *(Path(p) for p in previous_paths)]):
            if not prior_path.is_file():
                continue
            try:
                saved = json.loads(prior_path.read_text())
            except (ValueError, OSError) as error:
                raise GenerationBudgetError("Unreadable phase budget ledger") from error
            if not isinstance(saved, dict) or not isinstance(saved.get("lessons"), list):
                raise GenerationBudgetError("Invalid lesson identity in phase budget ledger")
            if not set(saved.get("lessons", [])) & set(self.lessons):
                continue
            if saved.get("active_phase") is not None:
                raise GenerationBudgetError("Interrupted generation has uncertain phase consumption; review its saved attempts before another provider call")
            if "phases" not in saved:
                legacy_used_seconds = max(legacy_used_seconds, _seconds(saved.get("used_seconds"), "legacy active duration"))
                continue
            if saved.get("schema_version") != 2 or not isinstance(saved["phases"], dict):
                raise GenerationBudgetError("Unknown generation phase budget format")
            reasons = saved.get("model_block_reasons", [])
            if not isinstance(reasons, list) or any(not isinstance(reason, str) or not reason for reason in reasons):
                raise GenerationBudgetError("Invalid blocked-model state in phase budget ledger")
            for reason in reasons:
                if reason not in self.model_block_reasons:
                    self.model_block_reasons.append(reason)
            for phase, value in saved["phases"].items():
                self.used[phase] = max(self.used.get(phase, 0.), _seconds(value, "saved phase duration"))
        try:
            history, self.receipts = receipt_consumption(receipt_directories, paths=receipt_paths)
        except GenerationBudgetError as error:
            if not permit_local_recovery:
                raise
            # No inferred phase usage and no new model allowance. Only local
            # validation of an already retained response remains available.
            history = {}
            reason = str(error)
            if reason not in self.model_block_reasons:
                self.model_block_reasons.append(reason)
        legacy = _seconds(legacy_used_seconds, "legacy active duration")
        if legacy > sum(history.values()) + 5:
            reason = "Legacy active budget cannot be reliably separated into writing and correction; saved work requires local review"
            if not permit_local_recovery:
                raise GenerationBudgetError(reason)
            if reason not in self.model_block_reasons:
                self.model_block_reasons.append(reason)
        for phase, value in history.items():
            self.used[phase] = max(self.used.get(phase, 0.), value)
        self.active_phase = None
        if permit_local_recovery and self.model_block_reasons:
            # Persist before a caller can try a model phase or remove the old
            # receipt. Local-only recovery must never reset unknown paid use.
            self.save()

    def block_model_calls(self, reason):
        if reason not in self.model_block_reasons:
            self.model_block_reasons.append(reason)
        self.save()

    def remaining(self, phase="writing"):
        if phase != "local_checks" and self.model_block_reasons:
            return 0.
        return max(0., phase_limit(phase) - self.used.get(phase, 0.))

    def snapshot(self):
        return {"schema_version": 2, "lessons": self.lessons, "phases": dict(self.used),
                "phase_limit_seconds": MODEL_PHASE_SECONDS, "local_check_limit_seconds": LOCAL_CHECK_SECONDS,
                "active_phase": self.active_phase, "used_seconds": sum(self.used.values()),
                "historical_receipts": self.receipts, "model_block_reasons": list(self.model_block_reasons)}

    def save(self):
        atomic_json_write(self.path, self.snapshot())

    @contextmanager
    def track(self, phase):
        if self.active_phase:
            raise GenerationBudgetError("Generation phase accounting cannot be nested")
        if phase != "local_checks" and self.model_block_reasons:
            raise GenerationBudgetError("Model phase blocked by uncertain historical consumption: " + "; ".join(self.model_block_reasons))
        if self.remaining(phase) <= 0:
            raise GenerationBudgetError(f"Generation phase {phase} exhausted its {phase_limit(phase)}-second allowance; saved content preserved")
        self.active_phase = {"phase": phase, "started_at_epoch": time.time()}
        self.save()  # A crash cannot silently turn this invocation into zero cost.
        started = time.monotonic()
        completed = False
        try:
            yield self.remaining(phase)
            completed = True
        finally:
            self.used[phase] = self.used.get(phase, 0.) + max(0., time.monotonic() - started)
            self.active_phase = None
            self.save()
            if completed and phase == 'local_checks' and self.remaining(phase) <= 0:
                raise GenerationBudgetError('Local generation checks exceeded their 120-second allowance; successful provider content was preserved')

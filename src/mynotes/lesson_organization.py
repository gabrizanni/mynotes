"""Source-bound organization choices, separate from immutable writing receipts."""
from __future__ import annotations

from copy import deepcopy
import re

from .workflow import WorkflowError

USE_ROLES = {"introduction", "development", "recall", "supplement"}


def provisional_choice(item, packs):
    """Offer an explicit provisional placement, never claim a heuristic proves teaching."""
    ranked = []
    for lecture, identifier in item["occurrences"].items():
        if lecture not in packs:
            continue
        pack = packs[lecture]
        slide = next(row for row in pack["evidence"] if row["id"] == identifier)
        terms = {word for word in re.findall(r"\w+", str(slide.get("content", "")).casefold()) if len(word) > 3}
        turns = [row for row in pack["evidence"] if row["id"].startswith("T")]
        scored = [(len(terms & set(re.findall(r"\w+", str(row.get("text", row.get("content", ""))).casefold()))), row["id"]) for row in turns]
        score = max((value for value, _ in scored), default=0)
        ranked.append((-score, lecture, [identifier for value, identifier in scored if value == score and score >= 2][:3]))
    _, owner, identifiers = min(ranked)
    return {"owner": owner, "status": "material", "provisional": True,
            "reason": "Provisional placement from available transcript overlap; the coordinator may revise it after reading the recordings.",
            "uses": {owner: {"role": "development", "reason": "Provisional main placement, not a claim that all slide content was taught.", "evidence_ids": identifiers}}}


def validate_links(choice, candidates, packs):
    allowed = {"owner", "status", "anchor", "structural_review", "reason", "provisional", "uses", "review_before"}
    if not isinstance(choice, dict) or set(choice) - allowed or choice.get("owner") not in candidates or choice.get("status") not in {"taught", "supplement", "material", "deferred"}:
        raise WorkflowError("A slide choice needs an available owner and status taught, supplement, material or deferred")
    if choice.get("status") == "deferred":
        target = choice.get("review_before")
        if (not isinstance(choice.get("reason"), str) or not choice["reason"].strip()
                or target not in packs or target <= choice["owner"]
                or choice.get("uses") or choice.get("anchor") or choice.get("structural_review")):
            raise WorkflowError("A deferred slide needs a reason and a later prepared review_before lesson, without claimed teaching uses")
    elif "review_before" in choice:
        raise WorkflowError("Only deferred slides may specify review_before")
    if "reason" in choice and (not isinstance(choice["reason"], str) or not choice["reason"].strip()):
        raise WorkflowError("Slide organization needs a nonempty reason")
    if "provisional" in choice and type(choice["provisional"]) is not bool:
        raise WorkflowError("Provisional organization must be a boolean")
    uses = choice.get("uses", {})
    if not isinstance(uses, dict):
        raise WorkflowError("Slide uses must map lessons to source-bound contributions")
    for lecture, use in uses.items():
        if lecture not in candidates or not isinstance(use, dict) or set(use) != {"role", "reason", "evidence_ids"}:
            raise WorkflowError("A slide use needs an available lesson, role, reason and transcript evidence_ids")
        if use["role"] not in USE_ROLES or not isinstance(use["reason"], str) or not use["reason"].strip():
            raise WorkflowError("Slide use requires introduction, development, recall or supplement and a reason")
        ids = use["evidence_ids"]
        if not isinstance(ids, list) or any(not isinstance(i, str) or not i.startswith("T") for i in ids) or len(set(ids)) != len(ids):
            raise WorkflowError("Slide use evidence must identify transcript turns")
        if lecture in packs and set(ids) - {row["id"] for row in packs[lecture]["evidence"] if row["id"].startswith("T")}:
            raise WorkflowError("Slide use cites unavailable transcript evidence")
        if not ids and lecture != choice["owner"] and use["role"] != "supplement":
            raise WorkflowError("An additional teaching use requires transcript evidence; otherwise label it supplement")


def organization_revision(previous, plan):
    """Pure diff: the caller writes a new record, never edits the old manifest."""
    if not previous or previous.get("fingerprint") == plan.get("fingerprint"):
        return None
    before, after = previous.get("ownership", {}), plan["ownership"]
    changes, affected = [], set()
    for key in sorted(set(before) | set(after)):
        old, new = before.get(key), after.get(key)
        if old == new:
            continue
        changes.append({"canonical_slide_id": key, "before": deepcopy(old), "after": deepcopy(new)})
        for choice in (old, new):
            if choice:
                affected.add(choice["owner"])
                affected.update(choice.get("uses", {}))
    if not changes and previous.get("contract") == plan.get("contract"):
        return None
    return {"kind": "organization_revalidation", "previous_fingerprint": previous["fingerprint"],
            "fingerprint": plan["fingerprint"], "changes": changes, "affected_lessons": sorted(affected),
            "previous_contract": previous.get("contract"), "contract": plan["contract"],
            "reason": "Revalidate retained writing under revised organization; not an equivalent historical writing request."}


def pending_organization(ownership, lectures, packs):
    """Local coordinator review, never a second writing/model phase."""
    pending = []
    for canonical, choice in ownership.items():
        target = choice.get("review_before")
        if choice["status"] == "deferred" and target in lectures:
            pending.append({"canonical_slide_id": canonical, "review_before": target,
                            "reason": choice["reason"]})
        elif (choice.get("provisional") and choice["owner"] in lectures
              and any(row["id"].startswith("T") for row in packs[choice["owner"]]["evidence"])):
            pending.append({"canonical_slide_id": canonical, "review_before": choice["owner"],
                            "reason": "Read this lesson's complete transcript and review slide placement before writing."})
    return pending

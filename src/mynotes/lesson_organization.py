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
    allowed = {"owner", "status", "anchor", "structural_review", "reason", "provisional", "uses", "review_before", "required_visual"}
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
    if 'required_visual' in choice and type(choice['required_visual']) is not bool:
        raise WorkflowError('Required visual selection must be a boolean')
    if choice.get('required_visual') and (choice.get('structural_review') or choice['status']=='deferred'):
        raise WorkflowError('A required source figure cannot be structurally exempted or deferred')
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
    integration_changed = bool(previous.get('autonomous_organization')) != bool(plan.get('autonomous_organization'))
    if integration_changed:
        affected.update(plan['lectures'])
    if not changes and previous.get("contract") == plan.get("contract") and not integration_changed:
        return None
    return {"kind": "organization_revalidation", "previous_fingerprint": previous["fingerprint"],
            "fingerprint": plan["fingerprint"], "changes": changes, "affected_lessons": sorted(affected),
            "previous_contract": previous.get("contract"), "contract": plan["contract"],
            "integrated_organization_migration": integration_changed,
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


def integrated_schema():
    """Decisions accompany ordinary writing; they are not a second model phase."""
    return {"type": "array", "description": "Place every unresolved page across the selected lessons while writing. Each page has one main owner. Cover all supplied teaching pages, including unspoken supplementary material; never dump leftovers into the last lesson.",
            "items": {"type": "object", "additionalProperties": False,
                      "properties": {"canonical_slide_id": {"type": "string"},
                                     "owner": {"type": "string", "description": "Selected lecture_id receiving this page's main explanation. Required for joint requests."},
                                     "status": {"type": "string", "enum": ["taught", "supplement", "pending", "material"]},
                                     "reason": {"type": "string"},
                                     "evidence_ids": {"type": "array", "items": {"type": "string"}},
                                     "structural_review": {"type": "object", "additionalProperties": False,
                                         "description": "Only for a visually inspected nonteaching title or section divider. Bind the actual staged source image hash. Never exempt code, data or diagrams.",
                                         "properties": {"kind": {"type": "string", "enum": ["title", "section_divider"]},
                                                        "reason": {"type": "string"}, "image_sha256": {"type": "string"}},
                                         "required": ["kind", "reason", "image_sha256"]}},
                      "required": ["canonical_slide_id", "status", "reason", "evidence_ids"]}}


def integrate_decisions(response, plan, lecture, mapping, *, scope=None):
    """Validate a separate organization ledger without editing returned prose.

    Already reviewed legacy placement needs no new response field. A retained
    response with provisional placement can derive only what its actual cited
    narrative establishes; untouched pages stay pending until another lesson.
    """
    ownership = deepcopy(plan['ownership'])
    visible = {key for key, item in plan['inventory'].items() if lecture in item['occurrences']}
    if scope is not None:
        visible &= scope
    unresolved = {key for key in visible if ownership[key].get('provisional')
                  or ownership[key]['status'] in {'deferred', 'pending'}}
    rows = response.notes.get('organization')
    derived = rows is None
    if rows is None:
        rows = []
        notes = next((row['notes'] for row in response.notes.get('lessons', []) if row.get('lecture_id') == lecture), {})
        for canonical in sorted(unresolved):
            slide = mapping[plan['inventory'][canonical]['occurrences'][lecture]]
            blocks = [block for block in notes.get('blocks', []) if block.get('kind') in {'paragraph', 'bullets', 'table', 'formula', 'visual'}
                      and block.get('coverage_role') in {'full', 'supplement', 'new_insight'} and slide in block.get('evidence_ids', [])]
            turns = sorted({identifier for block in blocks for identifier in block.get('evidence_ids', []) if identifier.startswith('T')})
            rows.append({'canonical_slide_id': canonical,
                         'status': ('taught' if turns else 'supplement') if blocks else 'pending',
                         'reason': 'Derived from retained narrative and its intact evidence links; no new writing or claimed historical prompt equivalence.' if blocks else 'Retained writing does not establish this page in the current lesson.',
                         'evidence_ids': turns})
    if not isinstance(rows, list):
        raise WorkflowError('Integrated organization must be an array of source-bound decisions')
    seen = set()
    local_turns = {mapping[row['id']] for row in plan['packs'][lecture]['evidence']
                   if row['id'].startswith('T') and row.get('origin_lecture_id', lecture) == lecture}
    last = lecture == plan['lectures'][-1]
    lecture_scope = plan.get('style',{}).get('slide_scope')=='lecture'
    for row in rows:
        if (not isinstance(row, dict) or set(row) - {'canonical_slide_id', 'owner', 'status', 'reason', 'evidence_ids', 'structural_review'}
                or not {'canonical_slide_id', 'status', 'reason', 'evidence_ids'}.issubset(row)
                or row.get('canonical_slide_id') not in visible or row['canonical_slide_id'] in seen
                or row.get('owner', lecture) != lecture
                or row.get('status') not in {'taught', 'supplement', 'pending', 'material'}
                or not isinstance(row.get('reason'), str) or not row['reason'].strip()):
            raise WorkflowError('Integrated organization has an unknown, duplicate or invalid slide decision')
        canonical = row['canonical_slide_id']; seen.add(canonical)
        prior = ownership[canonical]
        if canonical not in unresolved:
            if prior['owner'] != lecture or row['status'] != prior['status']:
                raise WorkflowError('Integrated writing cannot reassign settled coverage from another lesson')
            continue
        ids = row['evidence_ids']
        if not isinstance(ids, list) or len(set(ids)) != len(ids) or set(ids) - local_turns:
            raise WorkflowError('Integrated organization needs this lesson\'s actual transcript evidence IDs')
        status = row['status']
        if row.get('structural_review') and status != 'material':
            raise WorkflowError('A structural review is only valid for a nonteaching material slide')
        if status == 'taught' and not ids:
            raise WorkflowError('A taught slide needs current-lesson transcript evidence')
        if status == 'pending':
            if lecture_scope:
                if not ids or prior.get('required_visual'):
                    raise WorkflowError('Pending course slides need transcript boundary evidence and cannot hide a required figure')
            elif ids or last:
                raise WorkflowError('The last selected lesson cannot leave unresolved course slide coverage')
            ownership[canonical] = {'owner': lecture, 'status': 'pending', 'reason': row['reason']}
            if lecture_scope:
                reverse={value:key for key,value in mapping.items()}
                ownership[canonical].update(boundary_evidence_ids=[reverse[i] for i in ids],
                    review_before=lecture[:-2]+f'{int(lecture[-2:])+1:02d}')
        elif status == 'material':
            review = row.get('structural_review') or prior.get('structural_review')
            if not review:
                raise WorkflowError('Only an inspected image-bound structural slide can omit substantive coverage')
            ownership[canonical] = {'owner': lecture, 'status': 'material',
                                    'reason': row['reason'], 'structural_review': deepcopy(review)}
        else:
            reverse = {value: key for key, value in mapping.items()}
            ownership[canonical] = {'owner': lecture, 'status': status, 'reason': row['reason'],
                'uses': {lecture: {'role': 'development' if status == 'taught' else 'supplement',
                                  'reason': row['reason'], 'evidence_ids': [reverse[value] for value in ids]}}}
        if prior.get('required_visual'):ownership[canonical]['required_visual']=True
    if unresolved - seen:
        raise WorkflowError('Integrated organization omits unresolved visible slide decisions')
    # Inline supplementation anchors come from settled neighbouring concepts.
    for canonical, choice in ownership.items():
        if choice['owner'] != lecture or choice['status'] != 'supplement' or choice.get('anchor'):
            continue
        item = plan['inventory'][canonical]
        neighbours = [(other['page'], key) for key, other in plan['inventory'].items()
                      if key != canonical and other['source_sha256'] == item['source_sha256']
                      and ownership[key]['owner'] == lecture and ownership[key]['status'] == 'taught']
        before = min((entry for entry in neighbours if entry[0] > item['page']), default=None)
        after = max((entry for entry in neighbours if entry[0] < item['page']), default=None)
        choice['anchor'] = {}
        if after: choice['anchor']['after'] = plan['inventory'][after[1]]['occurrences'][lecture]
        if before: choice['anchor']['before'] = plan['inventory'][before[1]]['occurrences'][lecture]
        if not choice['anchor']:
            # A slides-only lesson can explain its source in natural order;
            # coverage_role=supplement still labels every explanatory passage.
            choice.pop('anchor')
    return ownership, {'kind': 'integrated_organization', 'lecture_id': lecture,
                       'derived_from_retained_response': derived, 'decisions': deepcopy(rows),
                       'before': deepcopy(plan['ownership']), 'after': deepcopy(ownership),
                       'source_snapshots': deepcopy(plan['sources'])}


def integrate_group_decisions(response, plan, lectures, mappings):
    """Resolve a joint writing response once; no additional planning call.

    Keep the historical single-lesson receipt format, with the same final
    ledger in each receipt, so publication verifies every lesson independently.
    """
    # Some writers return a global S evidence ID in the canonical field and
    # include that page itself among supporting references. Resolve only an
    # exact, unique source-bound alias; never guess a teacher/transcript link.
    from dataclasses import replace
    normalizations = []
    original_rows = response.notes.get('organization')
    if isinstance(original_rows, list):
        aliases = {}
        for canonical, item in plan['inventory'].items():
            for lecture in lectures:
                local = item['occurrences'].get(lecture)
                if local is not None:
                    identifier = mappings[lecture][local]
                    if identifier in aliases and aliases[identifier] != canonical:
                        raise WorkflowError('Ambiguous canonical slide alias')
                    aliases[identifier] = canonical
        rows = deepcopy(original_rows)
        for row in rows:
            if not isinstance(row, dict):
                raise WorkflowError('Organization needs page objects')
            key = row.get('canonical_slide_id')
            row['canonical_slide_id'] = aliases.get(key, key)
            ids = row.get('evidence_ids')
            removed = []
            if isinstance(ids, list):
                removed = [i for i in ids if aliases.get(i) == row['canonical_slide_id']]
                row['evidence_ids'] = [i for i in ids if aliases.get(i) != row['canonical_slide_id']]
            derived = []; supporting_blocks = []
            owner = row.get('owner', lectures[0] if len(lectures) == 1 else None)
            if row.get('status') == 'taught' and row.get('evidence_ids') == [] and owner in lectures:
                item = plan['inventory'].get(row['canonical_slide_id'], {})
                local = item.get('occurrences', {}).get(owner)
                page_id = mappings[owner].get(local)
                own_turns = {mappings[owner][r['id']] for r in plan['packs'][owner]['evidence']
                             if r['id'].startswith('T') and r.get('origin_lecture_id', owner) == owner}
                note = next((r['notes'] for r in response.notes.get('lessons', []) if r.get('lecture_id') == owner), {})
                for index, block in enumerate(note.get('blocks', [])):
                    if (block.get('kind') in {'paragraph','bullets','table','formula','visual'}
                            and block.get('coverage_role') in {'full','supplement','new_insight'}
                            and page_id in block.get('evidence_ids', [])):
                        refs = [i for i in block.get('evidence_ids', []) if i in own_turns]
                        if refs: supporting_blocks.append(index)
                        derived.extend(i for i in refs if i not in derived)
                row['evidence_ids'] = derived
            if key != row['canonical_slide_id'] or removed:
                normalizations.append({'from': key, 'canonical_slide_id': row['canonical_slide_id'],
                                       'removed_self_page_references': removed})
            if derived:
                normalizations.append({'canonical_slide_id': row['canonical_slide_id'],
                    'derived_transcript_references': derived, 'supporting_narrative_blocks': supporting_blocks})
        response = replace(response, notes={**response.notes, 'organization': rows})
    if len(lectures) == 1:
        ownership, receipt = integrate_decisions(response, plan, lectures[0], mappings[lectures[0]])
        if normalizations: receipt['reference_normalizations'] = normalizations
        return ownership, {lectures[0]: receipt}
    rows = response.notes.get('organization')
    if not isinstance(rows, list):
        raise WorkflowError('Joint writing requires explicit source-bound organization')
    unresolved = {key for key, choice in plan['ownership'].items()
                  if choice.get('provisional') or choice['status'] in {'pending', 'deferred'}}
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            raise WorkflowError('Joint organization needs page objects')
        key, owner = row.get('canonical_slide_id'), row.get('owner')
        if key not in plan['inventory'] or key in seen or owner not in lectures:
            raise WorkflowError('Joint organization has an unknown/duplicate page or owner')
        pending_allowed=(plan.get('style',{}).get('slide_scope')=='lecture' and owner==lectures[-1]
                         and bool(row.get('evidence_ids')))
        if owner not in plan['inventory'][key]['occurrences'] or (row.get('status') == 'pending' and not pending_allowed):
            raise WorkflowError('Joint organization must settle every page in an available selected lesson')
        seen.add(key)
    if unresolved - seen:
        raise WorkflowError('Joint organization omits unresolved visible slide decisions')
    ownership = deepcopy(plan['ownership']); receipts = {}
    for lecture in lectures:
        decisions = [row for row in rows if row['owner'] == lecture]
        scoped = replace(response, notes={**response.notes, 'organization': decisions})
        ownership, receipt = integrate_decisions(scoped, {**plan, 'ownership': ownership}, lecture,
                                                mappings[lecture], scope={r['canonical_slide_id'] for r in decisions})
        receipts[lecture] = receipt
    for receipt in receipts.values():
        receipt.update(before=deepcopy(plan['ownership']), after=deepcopy(ownership),
                       selected_lessons=list(lectures))
        if normalizations: receipt['reference_normalizations'] = deepcopy(normalizations)
    return ownership, receipts

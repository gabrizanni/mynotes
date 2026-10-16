"""Image caps are effective preferences, not quotas or source-reading limits."""
import pytest

from mynotes.configuration import ConfigurationError, effective_style, normalize_style_profile


@pytest.mark.parametrize("limit", [None, 0, 1, 12])
def test_image_limit_global_course_and_explicit_unset(limit):
    configuration = {"style": {"profile": {"image_limit": 3}},
                     "courses": [{"code": "OPS", "style": {"image_limit": limit}}]}
    assert effective_style(configuration)["image_limit"] == 3
    assert effective_style(configuration, "OPS")["image_limit"] == limit


@pytest.mark.parametrize("limit", [-1, True, False, 1.5, "3", [], {}])
def test_image_limit_rejects_invalid_values(limit):
    with pytest.raises(ConfigurationError, match="image_limit"):
        normalize_style_profile({"image_limit": limit}, partial=True)


def test_unconfigured_image_limit_preserves_existing_image_policy():
    profile = effective_style({"style": {}, "courses": []})
    assert profile["image_limit"] is None and profile["image_policy"] == "essential"


def test_generation_rejects_visual_inclusion_above_effective_limit(tmp_path):
    import json
    from mynotes.workflow import WorkflowError, _validate_candidate_notes
    from test_providers_quality import configured_workspace, source_pack, valid_notes
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace, visual=True).read_text())
    with pytest.raises(WorkflowError, match="image policy"):
        _validate_candidate_notes(valid_notes(visual=True), "OPS7-L01", pack,
                                  style={"image_limit": 0})


def test_nonrectangular_model_table_is_rejected_before_render(tmp_path):
    import json
    from mynotes.workflow import WorkflowError, _validate_candidate_notes
    from test_providers_quality import configured_workspace, source_pack, valid_notes
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text())
    notes = valid_notes()
    notes["blocks"].insert(2, {"kind": "table", "rows": [["A", "B"], ["Only one"]], "evidence_ids": ["S001"]})
    with pytest.raises(WorkflowError, match="same number of cells"):
        _validate_candidate_notes(notes, "OPS7-L01", pack)


def test_visual_cannot_hide_its_source_from_batch_ownership_validation(tmp_path):
    import json
    from mynotes.workflow import WorkflowError, _validate_candidate_notes
    from test_providers_quality import configured_workspace, source_pack, valid_notes
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace, visual=True).read_text())
    notes = valid_notes(visual=True)
    visual = next(block for block in notes["blocks"] if block["kind"] == "visual")
    visual["evidence_ids"] = ["H001"]
    with pytest.raises(WorkflowError, match="visual_id among evidence_ids"):
        _validate_candidate_notes(notes, "OPS7-L01", pack)


def test_heading_citations_reuse_only_its_supported_child_topic(tmp_path):
    import json
    from mynotes.workflow import _validate_candidate_notes
    from test_providers_quality import configured_workspace, source_pack, valid_notes
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text())
    notes = valid_notes()
    notes["blocks"].insert(1, {"kind": "heading", "text": "Inventory trade-off", "level": 3, "evidence_ids": []})
    validated = _validate_candidate_notes(notes, "OPS7-L01", pack)
    assert validated["blocks"][1]["evidence_ids"] == ["S001", "H001"]
    assert notes["blocks"][1]["evidence_ids"] == []


def test_heading_without_support_is_still_rejected(tmp_path):
    import json
    from mynotes.workflow import WorkflowError, _validate_candidate_notes
    from test_providers_quality import configured_workspace, source_pack, valid_notes
    workspace = configured_workspace(tmp_path)
    pack = json.loads(source_pack(workspace).read_text())
    notes = valid_notes()
    notes["blocks"].insert(-1, {"kind": "heading", "text": "Unsupported heading", "level": 3, "evidence_ids": []})
    with pytest.raises(WorkflowError, match="existing evidence ids"):
        _validate_candidate_notes(notes, "OPS7-L01", pack)

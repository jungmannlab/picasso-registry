"""Shared data contracts (S0B-2): the frozen shapes stay importable and
behave as the freeze doc (CONTRACTS.md) promises."""

import pytest
from pydantic import ValidationError

from picasso_registry.contracts import (
    LocalizeFrames,
    MetricVector,
    MODULESPEC_REFERENCE,
    TYPED_METRIC_FIELDS,
    WORKFLOW_VALIDATOR_REFERENCE,
    Workflow,
    WorkflowStep,
)
from picasso_registry.schemas import Metrics


# ── 1. Metric vector ─────────────────────────────────────────────────────


def test_metric_vector_is_the_registry_metrics_model():
    # Reuse, not a second copy — so it can never drift from /metrics.
    assert MetricVector is Metrics


def test_metric_vector_typed_and_extra_fields():
    mv = MetricVector(analysis_run_id="run1", nena_nm=6.2, novel_metric=1.0)
    assert mv.nena_nm == 6.2
    # Novel, not-yet-typed metrics ride through (extra="allow").
    assert mv.model_dump()["novel_metric"] == 1.0


def test_metric_vector_requires_join_key():
    with pytest.raises(ValidationError):
        MetricVector(nena_nm=6.2)  # missing analysis_run_id


def test_typed_metric_fields_cover_groups_and_exclude_bookkeeping():
    for group_metric in ("nena_nm", "drift_nm", "k_on", "n_clusters"):
        assert group_metric in TYPED_METRIC_FIELDS
    for excluded in ("id", "analysis_run_id", "scope", "extra"):
        assert excluded not in TYPED_METRIC_FIELDS


# ── 2. Workflow YAML ─────────────────────────────────────────────────────


def test_workflow_roundtrips_module_parameters_list():
    data = [
        {"module": "load_dataset_movie", "parameters": {}},
        {"module": "identify", "parameters": {"box": 7}},
    ]
    wf = Workflow.model_validate(data)
    assert [s.module for s in wf.root] == ["load_dataset_movie", "identify"]
    assert wf.root[1].parameters == {"box": 7}
    # RootModel serializes back to the bare on-disk list.
    assert wf.model_dump() == data


def test_workflow_step_parameters_default_empty():
    step = WorkflowStep(module="identify")
    assert step.parameters == {}


def test_workflow_step_rejects_empty_module():
    with pytest.raises(ValidationError):
        WorkflowStep(module="")


def test_workflow_from_steps_normalizes_accepted_forms():
    wf = Workflow.from_steps(
        [
            "load_dataset_movie",  # bare string
            ("identify", {"box": 7}),  # native tuple
            {"module": "localize", "parameters": {"eps": 0.001}},  # mapping
            {"name": "save_single_dataset"},  # name alias, no params
        ]
    )
    assert [s.module for s in wf.root] == [
        "load_dataset_movie",
        "identify",
        "localize",
        "save_single_dataset",
    ]
    assert wf.root[1].parameters == {"box": 7}
    assert wf.root[3].parameters == {}


# ── 3. localize_frames signature ─────────────────────────────────────────


def test_localize_frames_protocol_matches_callable():
    def localize_frames(frames, info, params):
        return frames

    assert isinstance(localize_frames, LocalizeFrames)


def test_localize_frames_protocol_rejects_non_callable():
    assert not isinstance(object(), LocalizeFrames)


# ── 4. ModuleSpec (link only) ────────────────────────────────────────────


def test_reference_pointers_name_their_sources():
    assert "modulespec" in MODULESPEC_REFERENCE
    assert "validate_workflow" in WORKFLOW_VALIDATOR_REFERENCE

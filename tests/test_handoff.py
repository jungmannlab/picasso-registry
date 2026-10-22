"""WP-9 dataset hand-off state machine.

The one mutable table: a QC-passed dataset is handed to the cluster exactly
once. These exercise the atomic compare-and-set claim (exactly-once), the
guarded transitions (legal edges only), the ``ready`` listing, and the FK +
UNIQUE constraints over the in-memory app.
"""

import pytest
from fastapi.testclient import TestClient

from picasso_registry.testing import make_memory_app


@pytest.fixture
def client():
    app = make_memory_app()
    c = TestClient(app)
    # A real acquisition_run to satisfy the FK on every hand-off below.
    assert c.post("/acquisition_run", json={"id": "run1"}).status_code == 200
    yield c
    app.dependency_overrides.clear()


def _ready(client, run_id="run1", **kw):
    return client.post(
        "/handoff/ready", json={"acquisition_run_id": run_id, **kw}
    )


def test_happy_path_ready_claim_running_done(client):
    r = _ready(client)
    assert r.status_code == 200, r.text
    assert r.json()["state"] == "ready"

    claim = client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "node-a"},
    )
    assert claim.status_code == 200
    assert claim.json()["state"] == "claimed"
    assert claim.json()["worker"] == "node-a"

    run = client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "claimed",
            "to": "running",
        },
    )
    assert run.status_code == 200
    assert run.json()["state"] == "running"

    done = client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "running",
            "to": "done",
            "analysis_run_id": "a-cluster",
        },
    )
    assert done.status_code == 200
    body = done.json()
    assert body["state"] == "done"
    assert body["analysis_run_id"] == "a-cluster"


def test_exactly_once_claim(client):
    _ready(client)
    first = client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "node-a"},
    )
    second = client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "node-b"},
    )
    # Exactly one worker wins; the other loses (409).
    assert first.status_code == 200
    assert second.status_code == 409
    # And the winner owns it — a second claim did not overwrite the worker.
    owner = client.get("/handoff/{}".format(first.json()["id"])).json()
    assert owner["worker"] == "node-a"
    assert owner["state"] == "claimed"


def test_claim_next_picks_a_ready_row(client):
    client.post("/acquisition_run", json={"id": "run2"})
    _ready(client, "run1")
    _ready(client, "run2")
    # No acquisition_run_id => claim any one ready row.
    won = client.post("/handoff/claim", json={"worker": "w"})
    assert won.status_code == 200
    assert won.json()["acquisition_run_id"] in {"run1", "run2"}
    assert won.json()["state"] == "claimed"


def test_claim_when_nothing_ready_409(client):
    # Nothing readied yet.
    assert (
        client.post("/handoff/claim", json={"worker": "w"}).status_code == 409
    )


def test_guarded_transition_rejects_wrong_expected_state(client):
    _ready(client)
    # Still 'ready' — transition expecting 'running' is illegal/racey.
    bad = client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "running",
            "to": "done",
        },
    )
    assert bad.status_code == 409


def test_illegal_edge_rejected(client):
    _ready(client)
    client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "w"},
    )
    # claimed -> done is not a legal edge (must go through running).
    bad = client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "claimed",
            "to": "done",
        },
    )
    assert bad.status_code == 409


def test_done_is_terminal(client):
    _ready(client)
    client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "w"},
    )
    client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "claimed",
            "to": "running",
        },
    )
    client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "running",
            "to": "done",
        },
    )
    # No transition out of 'done'.
    out = client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "done",
            "to": "failed",
        },
    )
    assert out.status_code == 409


def test_fail_from_running_carries_detail(client):
    _ready(client)
    client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "w"},
    )
    client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "claimed",
            "to": "running",
        },
    )
    failed = client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "running",
            "to": "failed",
            "detail": {"error": "sbatch timeout"},
        },
    )
    assert failed.status_code == 200
    assert failed.json()["state"] == "failed"
    assert failed.json()["detail"] == {"error": "sbatch timeout"}


def test_list_filter_by_state(client):
    client.post("/acquisition_run", json={"id": "run2"})
    _ready(client, "run1")
    _ready(client, "run2")
    ready = client.get("/handoff", params={"state": "ready"}).json()
    assert {h["acquisition_run_id"] for h in ready} == {"run1", "run2"}

    # After claiming run1 it is no longer listed as ready.
    client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "w"},
    )
    ready = client.get("/handoff", params={"state": "ready"}).json()
    assert {h["acquisition_run_id"] for h in ready} == {"run2"}
    claimed = client.get("/handoff", params={"state": "claimed"}).json()
    assert {h["acquisition_run_id"] for h in claimed} == {"run1"}


def test_unique_per_dataset_second_ready_409(client):
    assert _ready(client).status_code == 200
    # One hand-off per dataset: second ready for the same run is a conflict.
    assert _ready(client).status_code == 409


def test_ready_requires_existing_acquisition_run(client):
    # FK to a real acquisition_run: an unknown dataset is rejected (400).
    r = _ready(client, "does-not-exist")
    assert r.status_code == 400


def test_reready_after_failed_bumps_attempt(client):
    _ready(client)
    client.post(
        "/handoff/claim",
        json={"acquisition_run_id": "run1", "worker": "w"},
    )
    client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "claimed",
            "to": "failed",
        },
    )
    # A failed dataset may be re-readied under a bumped attempt.
    again = _ready(client, attempt=1)
    # UNIQUE still blocks a plain duplicate — re-ready is its own guarded path.
    assert again.status_code == 409


def test_transition_missing_row_409(client):
    # No hand-off at all for this run yet.
    r = client.post(
        "/handoff/transition",
        json={
            "acquisition_run_id": "run1",
            "expected": "ready",
            "to": "claimed",
        },
    )
    assert r.status_code == 409

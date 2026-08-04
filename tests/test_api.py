"""REST surface: round-trip every table, cohort, node_defaults, bulk, append."""

import pytest

# One representative payload per table (id omitted -> server mints a ULID,
# except acquisition_run whose id is the externally-minted run_id).
ROUND_TRIP = {
    "experiment": {"operator": "alice", "organism": "human"},
    "sample_tag": {"experiment_id": "e1", "tag": "membrane"},
    "target_channel": {
        "target": "CD20",
        "target_class": "membrane_protein",
        "imager_conc_nM": 5.0,
        "exposure_ms": 100.0,
        "laser_power_mW": 42.0,
    },
    "reagent_provenance": {"docking_design_id": "R1"},
    "acquisition_run": {
        "id": "run-xyz",
        "status": "done",
        "acquisition_modality": "TIRF",
    },
    "fov": {"pos_x": 1.5, "frame_count": 30000},
    "illumination": {"laser_nm": 560, "measured_power_mW": 42.0},
    "environment": {"room_temp_c": 21.5, "humidity_pct": 40.0},
    "fluidics_round": {"round_index": 2, "volume_uL": 100.0},
    "sample_morphology": {"n_cells": 12, "confluence": 0.7},
    "analysis_run": {"kind": "cluster", "picasso_version": "0.7"},
    "resource_usage": {"module": "localize", "peak_ram_mb": 2048.0},
    "qc": {"passed": True, "decided_by": "auto"},
    "feedback": {"operator_decision": "accept", "used_in_final": True},
    "artifact": {"kind": "locs_hdf5", "uri": "file:///x.hdf5"},
    "interpretation": {"stage1_summary": "nominal", "confidence": 0.9},
}


@pytest.mark.parametrize("resource, payload", list(ROUND_TRIP.items()))
def test_round_trip(client, resource, payload):
    created = client.post(f"/{resource}", json=payload)
    assert created.status_code == 200, created.text
    item_id = created.json()["id"]
    assert item_id  # server minted one when omitted

    got = client.get(f"/{resource}/{item_id}")
    assert got.status_code == 200
    body = got.json()
    for key, value in payload.items():
        assert body[key] == value


def test_get_missing_is_404(client):
    assert client.get("/experiment/nope").status_code == 404


def test_metrics_requires_analysis_run_id(client):
    # the run_id join invariant: a metrics row must link to an analysis_run
    r = client.post("/metrics", json={"nena_nm": 3.1})
    assert r.status_code == 422


def test_acquisition_run_requires_id(client):
    # id is PycroFlow's run_id; the server must not mint a synthetic one
    r = client.post("/acquisition_run", json={"status": "done"})
    assert r.status_code == 422


def test_taxonomy_child_before_parent_rejected(client):
    r = client.post(
        "/sample_taxonomy", json={"id": "child", "parent_id": "missing"}
    )
    assert r.status_code == 400  # refuse to silently root an orphan


def test_taxonomy_materialized_path(client):
    client.post("/sample_taxonomy", json={"id": "any", "name": "any"})
    client.post(
        "/sample_taxonomy",
        json={"id": "cell", "name": "cell", "parent_id": "any"},
    )
    client.post(
        "/sample_taxonomy",
        json={"id": "hela", "name": "HeLa", "parent_id": "cell"},
    )
    body = client.get("/sample_taxonomy/hela").json()
    assert body["path"] == "/any/cell/hela/"


def _build_tree(client):
    client.post(
        "/sample_taxonomy",
        json={"id": "any", "defaults": {"exposure_ms": 100}},
    )
    client.post(
        "/sample_taxonomy",
        json={
            "id": "cell",
            "parent_id": "any",
            "defaults": {"laser_nm": 560},
        },
    )
    client.post(
        "/sample_taxonomy",
        json={
            "id": "hela",
            "parent_id": "cell",
            "defaults": {"exposure_ms": 200},
        },
    )
    client.post("/sample_taxonomy", json={"id": "neuron", "parent_id": "cell"})


def test_cohort_tree_distance_fallback(client):
    _build_tree(client)
    for taxon, run in [
        ("hela", "r_hela"),
        ("neuron", "r_neuron"),
        ("any", "r_any"),
    ]:
        client.post(
            "/experiment", json={"id": f"e_{taxon}", "sample_taxon_id": taxon}
        )
        client.post(
            "/acquisition_run",
            json={"id": run, "experiment_id": f"e_{taxon}"},
        )

    cohort = client.get("/cohort", params={"taxon_id": "hela"}).json()
    ranked = [(c["acquisition_run_id"], c["tree_distance"]) for c in cohort]

    # exact-node run first (distance 0), then the fallback up the tree.
    assert ranked[0] == ("r_hela", 0)
    by_run = dict(ranked)
    assert by_run["r_any"] == 2  # hela -> cell -> any
    assert by_run["r_neuron"] == 2  # hela -> cell -> neuron
    # monotonically non-decreasing distance == ranked fallback
    distances = [d for _, d in ranked]
    assert distances == sorted(distances)


def test_cohort_unknown_taxon_404(client):
    assert client.get("/cohort", params={"taxon_id": "ghost"}).status_code == (
        404
    )


def test_cohort_excludes_other_root(client):
    _build_tree(client)  # root "any" with cell/hela/neuron
    client.post(
        "/experiment", json={"id": "e_hela", "sample_taxon_id": "hela"}
    )
    client.post(
        "/acquisition_run", json={"id": "r_hela", "experiment_id": "e_hela"}
    )
    # a disjoint tree under a different root
    client.post("/sample_taxonomy", json={"id": "synthetic"})
    client.post(
        "/sample_taxonomy", json={"id": "origami", "parent_id": "synthetic"}
    )
    client.post(
        "/experiment", json={"id": "e_ori", "sample_taxon_id": "origami"}
    )
    client.post(
        "/acquisition_run", json={"id": "r_ori", "experiment_id": "e_ori"}
    )

    runs = {
        c["acquisition_run_id"]
        for c in client.get("/cohort", params={"taxon_id": "hela"}).json()
    }
    assert "r_hela" in runs
    assert "r_ori" not in runs  # different top-level class is not a fallback


def test_cohort_max_distance_cap(client):
    _build_tree(client)
    for taxon, run in [("hela", "r_hela"), ("neuron", "r_neuron")]:
        client.post(
            "/experiment", json={"id": f"e_{taxon}", "sample_taxon_id": taxon}
        )
        client.post(
            "/acquisition_run", json={"id": run, "experiment_id": f"e_{taxon}"}
        )
    capped = client.get(
        "/cohort", params={"taxon_id": "hela", "max_distance": 1}
    ).json()
    runs = {c["acquisition_run_id"] for c in capped}
    assert runs == {"r_hela"}  # r_neuron is distance 2, excluded


def test_list_stable_order(client):
    ids = [f"run{i}" for i in range(5)]
    for rid in ids:
        client.post("/acquisition_run", json={"id": rid})
    listed = [r["id"] for r in client.get("/acquisition_run").json()]
    assert listed == sorted(ids)  # ORDER BY id


def test_node_defaults_cascade(client):
    _build_tree(client)
    nd = client.get("/node_defaults", params={"taxon_id": "hela"}).json()
    # inherited from ancestors, descendant overrides
    assert nd["defaults"] == {"exposure_ms": 200, "laser_nm": 560}
    assert nd["taxon_id"] == "hela"


def test_bulk_ingest(client):
    result = client.post(
        "/bulk",
        json={
            # deliberately child-before-parent: depth-sort must fix the order
            "sample_taxonomy": [
                {"id": "leaf", "parent_id": "root"},
                {"id": "root"},
            ],
            "experiment": [{"id": "eb", "sample_taxon_id": "leaf"}],
            "acquisition_run": [{"id": "rb", "experiment_id": "eb"}],
            "metrics": [{"analysis_run_id": "ab", "nena_nm": 2.0, "foo": 1}],
        },
    )
    assert result.status_code == 200
    body = result.json()
    assert body["counts"]["sample_taxonomy"] == 2
    assert body["total"] == 5

    # child path resolved during the batch
    assert client.get("/sample_taxonomy/leaf").json()["path"] == "/root/leaf/"
    # metrics extra absorbed in bulk too
    metrics = client.get("/metrics").json()
    assert metrics[0]["extra"] == {"foo": 1}


@pytest.mark.parametrize("method", ["put", "delete", "patch"])
def test_append_only_no_mutation_routes(client, method):
    client.post("/acquisition_run", json={"id": "run1"})
    call = getattr(client, method)
    # neither the collection nor the item exposes update/delete
    assert call("/acquisition_run/run1").status_code == 405


def test_acquisition_run_rejects_empty_id(client):
    # "" must not slip past the required-id rule and get a server-minted ULID
    r = client.post("/acquisition_run", json={"id": "", "status": "done"})
    assert r.status_code == 422


def test_duplicate_acquisition_run_conflicts(client):
    # a retried log of an already-stored client-minted run_id is a clean 409,
    # not a 500 with a SQL stack trace
    assert client.post("/acquisition_run", json={"id": "dup"}).status_code == (
        200
    )
    again = client.post("/acquisition_run", json={"id": "dup"})
    assert again.status_code == 409


def test_acquisition_run_preserves_unknown_field(client):
    # an as-yet-untyped provenance field rides into extra, not silently lost
    client.post(
        "/acquisition_run", json={"id": "run-x", "stage_serial": "XZ-9"}
    )
    body = client.get("/acquisition_run/run-x").json()
    assert body["extra"] == {"stage_serial": "XZ-9"}


def test_taxonomy_ignores_caller_supplied_path(client):
    # a caller-supplied path can't override the parent-derived materialized one
    client.post("/sample_taxonomy", json={"id": "root"})
    client.post(
        "/sample_taxonomy",
        json={"id": "kid", "parent_id": "root", "path": "/wrong/kid/"},
    )
    assert client.get("/sample_taxonomy/kid").json()["path"] == "/root/kid/"


def test_cohort_like_metacharacter_isolates_roots(client):
    # two disjoint roots differing only where "_" is a LIKE single-char
    # wildcard; the root filter must not leak "axb" into "a_b"'s cohort
    for tid in ("a_b", "axb"):
        client.post("/sample_taxonomy", json={"id": tid})
        client.post(
            "/experiment", json={"id": f"e_{tid}", "sample_taxon_id": tid}
        )
        client.post(
            "/acquisition_run",
            json={"id": f"r_{tid}", "experiment_id": f"e_{tid}"},
        )
    runs = {
        c["acquisition_run_id"]
        for c in client.get("/cohort", params={"taxon_id": "a_b"}).json()
    }
    assert runs == {"r_a_b"}


def test_cohort_excludes_unclassified_run(client):
    # a run with no experiment (hence no taxon) has no tree distance and is
    # not a cohort member — excluded by design, not a silent bug
    _build_tree(client)
    client.post(
        "/experiment", json={"id": "e_hela", "sample_taxon_id": "hela"}
    )
    client.post(
        "/acquisition_run", json={"id": "r_hela", "experiment_id": "e_hela"}
    )
    client.post("/acquisition_run", json={"id": "r_orphan"})
    runs = {
        c["acquisition_run_id"]
        for c in client.get("/cohort", params={"taxon_id": "hela"}).json()
    }
    assert "r_hela" in runs
    assert "r_orphan" not in runs


def test_metrics_extra_explicit_wins(client):
    # both channels carry key "foo"; the explicit extra dict is authoritative
    client.post(
        "/metrics",
        json={"analysis_run_id": "a1", "foo": 1, "extra": {"foo": 2}},
    )
    stored = client.get("/metrics").json()[0]["extra"]
    assert stored["foo"] == 2

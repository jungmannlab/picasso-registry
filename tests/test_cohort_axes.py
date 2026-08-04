"""A2 three-axis /cohort matching (register C12): axis-2 (target) and axis-3
(modality/dimensionality/buffer) filters, metric-dependent required depth, and
preservation of the S0B-1 taxon-only behaviour and tree-distance ranking."""

import pytest

from picasso_registry.cohort import resolve_match_depth
from picasso_registry.testing import mock_registry


def _seed(client):
    """A small cell tree with runs varying modality / target / dimensionality.

    Tree: cell -> {hela, cos7}. Runs:
      r_hela_tirf  hela  TIRF  2D  target CD20   (membrane)
      r_hela_hilo  hela  HILO  2D  target CD20
      r_cos7_tirf  cos7  TIRF  3D  target Nup96  (intracellular)
    """
    client.post("/sample_taxonomy", json={"id": "cell"})
    client.post("/sample_taxonomy", json={"id": "hela", "parent_id": "cell"})
    client.post("/sample_taxonomy", json={"id": "cos7", "parent_id": "cell"})

    runs = [
        ("r_hela_tirf", "hela", "TIRF", "2D", "CD20", "membrane_protein"),
        ("r_hela_hilo", "hela", "HILO", "2D", "CD20", "membrane_protein"),
        (
            "r_cos7_tirf",
            "cos7",
            "TIRF",
            "3D",
            "Nup96",
            "intracellular_protein",
        ),
    ]
    for run, taxon, modality, dim, tgt, tclass in runs:
        exp = f"e_{run}"
        client.post(
            "/experiment",
            json={
                "id": exp,
                "sample_taxon_id": taxon,
                "dimensionality": dim,
                "buffer": "PBS",
            },
        )
        client.post(
            "/target_channel",
            json={"experiment_id": exp, "target": tgt, "target_class": tclass},
        )
        client.post(
            "/acquisition_run",
            json={
                "id": run,
                "experiment_id": exp,
                "acquisition_modality": modality,
            },
        )


def _runs(cohort):
    return {c["acquisition_run_id"] for c in cohort}


# ── axis-3 modality / dimensionality / buffer filters ────────────────────


def test_modality_filter_constrains_cohort(client):
    _seed(client)
    cohort = client.get(
        "/cohort", params={"taxon_id": "hela", "modality": "TIRF"}
    ).json()
    assert _runs(cohort) == {"r_hela_tirf", "r_cos7_tirf"}  # HILO excluded


def test_dimensionality_filter_constrains_cohort(client):
    _seed(client)
    cohort = client.get(
        "/cohort", params={"taxon_id": "hela", "dimensionality": "2D"}
    ).json()
    assert _runs(cohort) == {"r_hela_tirf", "r_hela_hilo"}  # 3D cos7 excluded


def test_invalid_modality_is_422(client):
    _seed(client)
    r = client.get(
        "/cohort", params={"taxon_id": "hela", "modality": "confocal"}
    )
    assert r.status_code == 422  # closed A2 enum


# ── axis-2 target overlap ────────────────────────────────────────────────


def test_target_filter_constrains_cohort(client):
    _seed(client)
    only_nup = client.get(
        "/cohort", params={"taxon_id": "hela", "target": "Nup96"}
    ).json()
    assert _runs(only_nup) == {"r_cos7_tirf"}


def test_target_set_overlap(client):
    _seed(client)
    cohort = client.get(
        "/cohort",
        params={"taxon_id": "hela", "target_set": ["CD20", "Nup96"]},
    ).json()
    assert _runs(cohort) == {"r_hela_tirf", "r_hela_hilo", "r_cos7_tirf"}


# ── metric-dependent required depth ──────────────────────────────────────


def test_localization_metric_requires_modality_only(client):
    _seed(client)
    # broad depth: modality required, target NOT — so a different-target run
    # (cos7/Nup96) still counts as long as modality matches.
    missing = client.get(
        "/cohort", params={"taxon_id": "hela", "metric": "nena_nm"}
    )
    assert missing.status_code == 400  # modality required

    ok = client.get(
        "/cohort",
        params={"taxon_id": "hela", "metric": "nena_nm", "modality": "TIRF"},
    ).json()
    assert _runs(ok) == {"r_hela_tirf", "r_cos7_tirf"}


def test_structure_metric_additionally_requires_target(client):
    _seed(client)
    # fine depth: modality + target required.
    no_target = client.get(
        "/cohort",
        params={
            "taxon_id": "hela",
            "metric": "n_clusters",
            "modality": "TIRF",
        },
    )
    assert no_target.status_code == 400  # target required

    ok = client.get(
        "/cohort",
        params={
            "taxon_id": "hela",
            "metric": "n_clusters",
            "modality": "TIRF",
            "target": "CD20",
        },
    ).json()
    # cos7/Nup96 is TIRF but wrong target -> excluded at fine depth.
    assert _runs(ok) == {"r_hela_tirf"}


def test_explicit_match_depth_overrides_metric(client):
    _seed(client)
    # match_depth=broad forces localization-level even for a structure metric.
    ok = client.get(
        "/cohort",
        params={
            "taxon_id": "hela",
            "metric": "n_clusters",
            "match_depth": "broad",
            "modality": "TIRF",
        },
    ).json()
    assert _runs(ok) == {"r_hela_tirf", "r_cos7_tirf"}  # target not required


# ── ranking + back-compat ────────────────────────────────────────────────


def test_tree_distance_ranking_within_constrained_set(client):
    _seed(client)
    cohort = client.get(
        "/cohort", params={"taxon_id": "hela", "modality": "TIRF"}
    ).json()
    ranked = [(c["acquisition_run_id"], c["tree_distance"]) for c in cohort]
    # exact node (hela) before the cos7 sibling (distance 2), still ranked.
    assert ranked[0] == ("r_hela_tirf", 0)
    assert dict(ranked)["r_cos7_tirf"] == 2
    assert [d for _, d in ranked] == sorted(d for _, d in ranked)


def test_taxon_only_call_is_backward_compatible(client):
    _seed(client)
    cohort = client.get("/cohort", params={"taxon_id": "hela"}).json()
    # no axis args -> every run under the root, exactly like S0B-1.
    assert _runs(cohort) == {"r_hela_tirf", "r_hela_hilo", "r_cos7_tirf"}


# ── depth policy unit ────────────────────────────────────────────────────


def test_resolve_match_depth_policy():
    assert resolve_match_depth(None, None) is None  # taxon-only
    assert resolve_match_depth("nena_nm", None) == "broad"  # group A
    assert resolve_match_depth("drift_nm", None) == "broad"  # group B
    assert resolve_match_depth("k_on", None) == "fine"  # group C kinetics
    assert resolve_match_depth("n_clusters", None) == "fine"  # group D
    assert resolve_match_depth("novel_unknown", None) == "broad"  # default
    # explicit selector wins over the metric-derived depth.
    assert resolve_match_depth("n_clusters", "broad") == "broad"


# ── client + mock exercise the new params ────────────────────────────────


def test_client_mock_new_cohort_params():
    with mock_registry() as reg:
        reg.add_taxon(id="cell")
        reg.add_taxon(id="hela", parent_id="cell")
        reg.log_experiment(id="e1", sample_taxon_id="hela")
        reg.create("target_channel", experiment_id="e1", target="CD20")
        reg.log_acquisition(
            id="r1", experiment_id="e1", acquisition_modality="TIRF"
        )
        # broad-metric cohort through the typed client surface.
        cohort = reg.cohort(
            "hela", metric="nena_nm", modality="TIRF", target_set=["CD20"]
        )
        assert _runs(cohort) == {"r1"}
        # a fine-metric cohort with no target raises (server 400 -> HTTPError).
        with pytest.raises(Exception):
            reg.cohort("hela", metric="n_clusters", modality="TIRF")

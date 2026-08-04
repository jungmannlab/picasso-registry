"""A2 descriptor /cohort filters (register C12): axis-2 (target) and axis-3
(modality/dimensionality/buffer) act as independent optional filters on top of
the S0B-1 sample-taxon tree-distance ranking. How much must match is the
caller's choice — the registry just exposes the axes.
"""

from picasso_registry.testing import mock_registry


def _seed(client):
    """A small cell tree with runs varying modality / target / dimensionality.

    Tree: cell -> {hela, cos7}. Modality/dimensionality are per-experiment;
    target is per-channel.
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
                "acquisition_modality": modality,
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
            json={"id": run, "experiment_id": exp},
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


def test_combined_axis3_filters_intersect(client):
    _seed(client)
    cohort = client.get(
        "/cohort",
        params={
            "taxon_id": "hela",
            "modality": "TIRF",
            "dimensionality": "2D",
        },
    ).json()
    assert _runs(cohort) == {"r_hela_tirf"}  # only TIRF & 2D


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


def test_target_and_modality_compose(client):
    _seed(client)
    # caller expresses a tighter comparison by combining axes itself.
    cohort = client.get(
        "/cohort",
        params={"taxon_id": "hela", "modality": "TIRF", "target": "CD20"},
    ).json()
    assert _runs(cohort) == {"r_hela_tirf"}  # TIRF & CD20


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


# ── client + mock exercise the new params ────────────────────────────────


def test_client_mock_new_cohort_params():
    with mock_registry() as reg:
        reg.add_taxon(id="cell")
        reg.add_taxon(id="hela", parent_id="cell")
        reg.log_experiment(
            id="e1", sample_taxon_id="hela", acquisition_modality="TIRF"
        )
        reg.create("target_channel", experiment_id="e1", target="CD20")
        reg.log_acquisition(id="r1", experiment_id="e1")
        # axis filters through the typed client surface (incl. list param).
        cohort = reg.cohort("hela", modality="TIRF", target_set=["CD20"])
        assert _runs(cohort) == {"r1"}
        # a non-matching axis value yields an empty cohort (not an error).
        assert reg.cohort("hela", modality="HILO") == []

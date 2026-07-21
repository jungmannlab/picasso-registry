"""The client + in-memory mock other repos import in their own tests."""

from picasso_registry.testing import MockRegistryClient, mock_registry


def test_mock_registry_importable_and_roundtrips():
    with mock_registry() as reg:
        assert reg.health()["status"] == "ok"
        reg.log_acquisition(id="run1", status="done")
        assert reg.get("acquisition_run", "run1")["status"] == "done"


def test_mock_registry_isolated_state():
    # A fresh in-memory DB per context: data must not leak across uses.
    with mock_registry() as reg:
        reg.log_acquisition(id="only-here")
    with mock_registry() as reg2:
        assert reg2.list("acquisition_run") == []


def test_client_surface_end_to_end():
    reg = MockRegistryClient()
    reg.add_taxon(id="root", defaults={"a": 1})
    reg.add_taxon(id="child", parent_id="root", defaults={"b": 2})
    reg.log_experiment(id="e1", sample_taxon_id="child")
    reg.log_acquisition(id="r1", experiment_id="e1")
    reg.log_analysis(id="a1", acquisition_run_id="r1")
    reg.log_metrics(analysis_run_id="a1", nena_nm=3.0, custom=7)

    assert reg.node_defaults("child")["defaults"] == {"a": 1, "b": 2}
    cohort = reg.cohort("child")
    assert cohort[0]["acquisition_run_id"] == "r1"
    assert cohort[0]["tree_distance"] == 0

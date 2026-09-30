"""A15 / C24 (WP-REG-SIM): acquisition_run.data_source + sim_params —
closed-vocabulary acquired-vs-simulated provenance, set at ingest."""

import pytest

from picasso_registry.testing import mock_registry


def test_simulated_run_round_trips_flag_and_sim_params():
    with mock_registry() as reg:
        reg.log_acquisition(
            id="sim-run-1",
            data_source="simulated",
            sim_params={"grid": "20nm", "n_sites": 12, "conc_pm": 50},
        )
        run = reg.get("acquisition_run", "sim-run-1")
        assert run["data_source"] == "simulated"
        assert run["sim_params"]["n_sites"] == 12


def test_data_source_defaults_to_null_unknown():
    with mock_registry() as reg:
        reg.log_acquisition(id="legacy-run")
        assert reg.get("acquisition_run", "legacy-run")["data_source"] is None


def test_cohort_items_carry_data_source():
    """Learned-range consumers must be able to drop simulated members
    without an N+1 per-run lookup (A15 default exclusion)."""
    with mock_registry() as reg:
        reg.add_taxon(id="t-root", name="DNA-Origami")
        reg.log_experiment(id="e-sim", sample_taxon_id="t-root")
        reg.log_acquisition(
            id="r-sim", experiment_id="e-sim", data_source="simulated"
        )
        reg.log_experiment(id="e-real", sample_taxon_id="t-root")
        reg.log_acquisition(id="r-real", experiment_id="e-real")
        items = reg.cohort("t-root")
        by_run = {it["acquisition_run_id"]: it["data_source"] for it in items}
        assert by_run == {"r-sim": "simulated", "r-real": None}


def test_data_source_vocabulary_is_closed():
    with mock_registry() as reg:
        with pytest.raises(Exception) as exc:
            reg.log_acquisition(id="bad-run", data_source="synthetic")
        assert "422" in str(exc.value)
        # the invalid write must not have landed in the append-only store
        with pytest.raises(Exception):
            reg.get("acquisition_run", "bad-run")

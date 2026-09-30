"""WP-DASH: the flat read model, the template's composite quality ranking,
and the monet-style auth topology (public shell, read-scoped data)."""

import json

import pytest

from picasso_registry.auth import AuthConfig, TokenInfo
from picasso_registry.backfill_liveloc import ingest_paths
from picasso_registry.dashboard import composite_scores
from picasso_registry.testing import MockRegistryClient, make_memory_app
from picasso_registry.testing import mock_registry

# reuse the backfill fixture — the dashboard's first real corpus
from test_backfill_liveloc import QC


def _ingest_fixture(reg, tmp_path, n=3):
    for i in range(n):
        qc = dict(
            QC,
            measurement=dict(QC["measurement"], position_label=str(i)),
            metrics=dict(QC["metrics"], nena_global_nm=3.0 + i),
        )
        (tmp_path / f"m_Pos{i}_qc.json").write_text(json.dumps(qc))
    ingest_paths(
        reg, [str(tmp_path)], data_source="simulated", log=lambda *_: None
    )


def test_flat_read_model_joins_all_tables(tmp_path):
    with mock_registry() as reg:
        _ingest_fixture(reg, tmp_path)
        rows = reg._get("/dashboard/api/measurements")
        assert len(rows) == 3
        row = rows[0]
        # identity + provenance
        assert row["name"].startswith("20nmGrid")
        assert row["data_source"] == "simulated"
        assert row["software_version"] == "V0.8"
        # experiment.extra.sample fields
        assert row["dye"] == "Cy3B" and row["sample_type"] == "DNA-Origami"
        # target_channel: nM back to the dashboard's pM convention
        assert row["conc_pm"] == 500.0 and row["power_mw"] == 35.0
        # metrics typed + extra
        assert row["n_locs"] == 812345
        assert row["nena_zoom_nm"] == 3.1 and row["specificity"] == 0.91
        # ranking attached, best (lowest NeNA) scores highest
        by_nena = sorted(rows, key=lambda r: r["nena_nm"])
        scores = [r["quality_score"] for r in by_nena]
        assert scores[0] > scores[-1]


def test_composite_ranking_reproduces_template_ordering():
    """The compare_qc.py recipe on a hand-checkable fixture: normalize each
    quality metric direction-aware, average. Run B dominates (best FRC+NeNA,
    best SBR), run C is worst on everything."""
    rows = [
        {"frc_nm": 20.0, "nena_nm": 3.0, "specificity": 0.9, "sbr": 4.0},
        {"frc_nm": 10.0, "nena_nm": 2.0, "specificity": 0.8, "sbr": 6.0},
        {"frc_nm": 30.0, "nena_nm": 4.0, "specificity": 0.7, "sbr": 2.0},
    ]
    a, b, c = composite_scores(rows)
    assert b > a > c
    # hand computation: B = (1 + 1 + 0.5 + 1)/4
    assert b == pytest.approx((1 + 1 + 0.5 + 1) / 4)
    assert c == pytest.approx(0.0)


def test_composite_ranking_handles_missing_metrics():
    rows = [
        {"frc_nm": 10.0},  # only one metric -> normalized alone (0.5 span)
        {"nena_nm": None, "sbr": "not-a-number"},
        {},
    ]
    scores = composite_scores(rows)
    assert scores[0] is not None
    assert scores[1] is None and scores[2] is None


def test_dashboard_shell_is_public_but_data_needs_read_token():
    """monet's topology: the HTML shell carries no data and loads without a
    token; the read model requires read scope."""
    auth = AuthConfig({"tok-read": TokenInfo(scope="read", label="lab")})
    reg = MockRegistryClient(make_memory_app(auth=auth))  # no token
    page = reg.client.get("/dashboard")
    assert page.status_code == 200
    assert "registry_dashboard_token" in page.text  # the login flow is there
    assert reg.client.get("/dashboard/api/measurements").status_code == 401
    ok = reg.client.get(
        "/dashboard/api/measurements",
        headers={"Authorization": "Bearer tok-read"},
    )
    assert ok.status_code == 200 and ok.json() == []
    reg.close()


def test_read_model_is_read_only():
    """The dashboard adds no write path (WP-DASH verification item)."""
    with mock_registry() as reg:
        r = reg.client.post("/dashboard/api/measurements", json={})
        assert r.status_code == 405

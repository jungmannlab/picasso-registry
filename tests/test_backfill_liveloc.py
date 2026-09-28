"""Backfill of LiveLocalization *_qc.json files: deterministic identity,
metric-key mapping onto the typed columns (everything else preserved in
`extra`), file-level idempotency, and the artifact link back to the source
file. Runs against the in-memory mock (the real app + routes)."""

import json

import pytest

from picasso_registry.backfill_liveloc import (
    ingest_paths,
    main,
    map_qc,
)
from picasso_registry.testing import mock_registry

QC = {
    "schema_version": "qc-1",
    "software_version": "V0.8",
    "created": "2024-11-03T14:22:31",
    "measurement": {
        "name": "20nmGrid_500pM",
        "folder": "/pool/users/hgrabmayr/2024-11-03_20nmGrid",
        "position_label": "3",
        "n_positions": 9,
        "microscope": "mercury",
        "mode": "standard",
    },
    "sample": {
        "sample_type": "DNA-Origami",
        "project": "grid QC",
        "origami_structure": "20 nm grid",
        "dye": "Cy3B",
        "buffer": "B+ 500mM",
        "imager": "R4-9nt",
        "conc_pm": 500,
        "power_mw": 35.0,
        "mode": "TIRF",
        "notes": "fresh flow cell",
    },
    "acquisition": {
        "exposure_ms": 100,
        "total_frames": 9000,
        "frames_per_position": 1000,
        "pixelsize_nm": 130,
        "width_px": 1152,
        "height_px": 1152,
        "imager_sequence": "R4",
    },
    "metrics": {
        "localizations": 812345,
        "loc_density_fov_um2": 41.2,
        "frc_resolution_nm": 18.4,
        "nena_zoom_nm": 3.1,
        "nena_global_nm": 3.4,
        "specificity": 0.91,
        "sbr": 4.2,
        "photons_per_loc": 5300.0,
        "background": [110.0, 112.5, 111.0],  # per-batch series -> extra
    },
    "clustering": {"algorithm": "dbscan", "eps": 0.9, "min_samples": 10},
    "advisor": [
        {"metric": "nena_zoom_nm", "severity": "info", "message": "ok"}
    ],
    "metadata_complete": True,
    "metadata_missing": [],
}


@pytest.fixture()
def qc_file(tmp_path):
    path = tmp_path / "20nmGrid_500pM_Pos3_qc.json"
    path.write_text(json.dumps(QC))
    return path


def test_map_qc_is_deterministic_and_mount_independent(qc_file, tmp_path):
    tables = map_qc(QC, str(qc_file))
    again = map_qc(QC, str(tmp_path / "other-mount" / "copy_qc.json"))
    # ids derive from created+name+position, NOT the file path
    assert (
        tables["acquisition_run"][0]["id"] == again["acquisition_run"][0]["id"]
    )
    # a different position is a different measurement
    other = dict(QC, measurement=dict(QC["measurement"], position_label="4"))
    assert (
        map_qc(other, str(qc_file))["acquisition_run"][0]["id"]
        != tables["acquisition_run"][0]["id"]
    )


def test_metric_keys_map_onto_typed_columns(qc_file):
    ((m,),) = [map_qc(QC, str(qc_file))["metrics"]]
    assert m["n_locs"] == 812345
    assert m["frc_nm"] == 18.4
    assert m["nena_nm"] == 3.4  # whole-FOV NeNA -> the typed column
    assert m["photons_median"] == 5300.0
    assert m["density_locs_um2"] == 41.2
    assert m["sbr"] == 4.2
    # zoom NeNA + specificity stay under their original keys (-> extra)
    assert m["nena_zoom_nm"] == 3.1 and m["specificity"] == 0.91
    # non-scalar background must NOT hit the typed float column
    assert "background" not in m
    assert isinstance(m["background_raw"], list)


def test_ingest_round_trip_and_idempotency(qc_file, capsys):
    with mock_registry() as reg:
        counts = ingest_paths(reg, [str(qc_file)], log=lambda *_: None)
        assert counts == {"ingested": 1, "skipped": 0, "failed": 0}

        run_id = map_qc(QC, str(qc_file))["acquisition_run"][0]["id"]
        run = reg.get("acquisition_run", run_id)
        assert run["microscope_id"] == "mercury"
        assert run["status"] == "backfilled"
        assert run["extra"]["software_version"] == "V0.8"

        (metrics,) = reg.list("metrics")
        assert metrics["n_locs"] == 812345
        assert metrics["nena_nm"] == 3.4
        assert metrics["extra"]["nena_zoom_nm"] == 3.1
        assert metrics["extra"]["background_raw"] == [110.0, 112.5, 111.0]

        (ana,) = reg.list("analysis_run")
        assert ana["kind"] == "liveloc-qc"
        assert ana["acquisition_run_id"] == run_id

        (art,) = reg.list("artifact")
        assert art["uri"] == str(qc_file)
        assert art["checksum"].startswith("sha256:")

        (exp,) = reg.list("experiment")
        assert exp["extra"]["sample"]["dye"] == "Cy3B"
        assert exp["sample_taxon_id"] is None  # no taxonomy guessing in v1

        (chan,) = reg.list("target_channel")
        assert chan["imager_conc_nM"] == 0.5  # 500 pM
        assert chan["target"] == "20 nm grid"

        # re-run: file-level idempotency, nothing duplicated
        counts = ingest_paths(reg, [str(qc_file)], log=lambda *_: None)
        assert counts == {"ingested": 0, "skipped": 1, "failed": 0}
        assert len(reg.list("metrics")) == 1


def test_directory_sweep_dry_run_posts_nothing(tmp_path):
    for pos in ("1", "2"):
        qc = dict(QC, measurement=dict(QC["measurement"], position_label=pos))
        (tmp_path / f"m_Pos{pos}_qc.json").write_text(json.dumps(qc))
    with mock_registry() as reg:
        counts = ingest_paths(
            reg, [str(tmp_path)], dry_run=True, log=lambda *_: None
        )
        assert counts["ingested"] == 2
        assert reg.list("acquisition_run") == []


def test_file_without_created_is_skipped(tmp_path):
    qc = {k: v for k, v in QC.items() if k != "created"}
    path = tmp_path / "old_qc.json"
    path.write_text(json.dumps(qc))
    with mock_registry() as reg:
        counts = ingest_paths(reg, [str(path)], log=lambda *_: None)
        assert counts == {"ingested": 0, "skipped": 1, "failed": 0}


def test_invalid_json_fails_that_file_only(tmp_path, qc_file):
    bad = tmp_path / "broken_qc.json"
    bad.write_text("{not json")
    with mock_registry() as reg:
        counts = ingest_paths(
            reg, [str(bad), str(qc_file)], log=lambda *_: None
        )
        assert counts == {"ingested": 1, "skipped": 0, "failed": 1}


def test_console_entry_dry_run(qc_file, capsys, monkeypatch):
    """main() wires args; --dry-run must not touch the network at all."""
    import picasso_registry.backfill_liveloc as mod

    class NoNetwork:
        def get(self, *a, **k):
            raise ConnectionError("offline")

        def bulk_ingest(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("dry run must not POST")

    monkeypatch.setattr(
        mod, "RegistryClient", lambda *a, **k: NoNetwork(), raising=False
    )
    monkeypatch.setattr(
        "picasso_registry.client.RegistryClient",
        lambda *a, **k: NoNetwork(),
    )
    rc = main([str(qc_file), "--dry-run"])
    assert rc == 0
    assert "1 ingested" in capsys.readouterr().out

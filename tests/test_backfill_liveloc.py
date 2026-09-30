"""Backfill of LiveLocalization *_qc.json files: deterministic identity,
metric-key mapping onto the typed columns (everything else preserved in
`extra`), file-level idempotency, and the artifact link back to the source
file. Runs against the in-memory mock (the real app + routes)."""

import json
import os

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


def test_identity_is_timezone_independent(qc_file):
    """Naive `created` stamps are pinned to UTC for id minting: the same
    file must mint the same run id no matter the host TZ, or a re-run from
    another machine would double-ingest the whole archive."""
    import time

    old_tz = os.environ.get("TZ")
    ids = []
    try:
        for tz in ("UTC", "Europe/Berlin", "America/New_York"):
            os.environ["TZ"] = tz
            time.tzset()
            ids.append(map_qc(QC, str(qc_file))["acquisition_run"][0]["id"])
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()
    assert len(set(ids)) == 1
    # pin the exact value: any change to the minting scheme re-identifies
    # every measurement in every deployed registry — must be deliberate
    assert ids[0] == "01JBS6FW2RFJQ277GW65RYABNQ"


def test_created_z_suffix_parses(qc_file):
    qc = dict(QC, created="2024-11-03T14:22:31Z")
    tables = map_qc(qc, str(qc_file))
    # Z == +00:00 == the UTC pin for naive stamps -> identity differs only
    # through the identity *string* (created_raw), never the clock reading
    assert tables["acquisition_run"][0]["started_at"].endswith("Z")


def test_reserved_metric_keys_cannot_clobber_the_row(qc_file):
    """A qc.json metrics key named like the row's own fields must never
    replace the analysis_run join or scope."""
    qc = dict(
        QC,
        metrics=dict(
            QC["metrics"], analysis_run_id="evil", scope="zoom", extra="x"
        ),
    )
    ((m,),) = [map_qc(qc, str(qc_file))["metrics"]]
    assert m["analysis_run_id"] != "evil"
    assert m["scope"] == "liveloc"
    assert m["analysis_run_id_raw"] == "evil"
    assert m["scope_raw"] == "zoom" and m["extra_raw"] == "x"


def test_rename_collision_preserves_both_values(qc_file):
    """A file carrying BOTH a legacy key and its registry-style target must
    not silently drop either ('nothing is dropped')."""
    qc = dict(QC, metrics=dict(QC["metrics"], n_locs=999))
    ((m,),) = [map_qc(qc, str(qc_file))["metrics"]]
    values = {m.get("n_locs"), m.get("n_locs_raw"), m.get("localizations_raw")}
    assert 999 in values and 812345 in values


def test_nonscalar_under_any_typed_column_is_suffixed(qc_file):
    qc = dict(QC, metrics=dict(QC["metrics"], drift_nm=[2.1, 2.4]))
    ((m,),) = [map_qc(qc, str(qc_file))["metrics"]]
    assert "drift_nm" not in m and m["drift_nm_raw"] == [2.1, 2.4]


def test_typed_column_sets_stay_in_sync_with_the_schema():
    """The hardcoded column sets must exactly cover schemas.Metrics, or new
    schema columns silently fall through to the 422-able passthrough."""
    from picasso_registry import schemas
    from picasso_registry.backfill_liveloc import (
        _RESERVED_METRIC_KEYS,
        _TYPED_SCALAR_COLUMNS,
    )

    schema_fields = set(schemas.Metrics.model_fields)
    assert _TYPED_SCALAR_COLUMNS | _RESERVED_METRIC_KEYS == schema_fields | {
        "id"
    }
    assert not (_TYPED_SCALAR_COLUMNS & _RESERVED_METRIC_KEYS)


def test_zero_power_is_kept(qc_file):
    """A dark-control 0.0 mW must not fall through to another block's value
    (falsy-zero trap)."""
    qc = dict(QC, sample=dict(QC["sample"], power_mw=0.0))
    ((chan,),) = [map_qc(qc, str(qc_file))["target_channel"]]
    assert chan["laser_power_mW"] == 0.0


def test_null_values_are_compacted_out(qc_file):
    """Absent/null blocks must not pad every row's extra with null keys —
    key presence is the vintage signal."""
    qc = {k: v for k, v in QC.items() if k != "clustering"}
    tables = map_qc(qc, str(qc_file))
    (ana,) = tables["analysis_run"]
    assert "clustering" not in ana and "dye_analysis" not in ana
    (exp,) = tables["experiment"]
    assert "protocol" not in exp


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


def test_data_source_flag_stamps_a15_provenance(qc_file):
    """--data-source simulated must land on the typed acquisition_run column
    (A15/C24) — append-only, so it has to be right at ingest."""
    with mock_registry() as reg:
        ingest_paths(
            reg, [str(qc_file)], data_source="simulated", log=lambda *_: None
        )
        (run,) = reg.list("acquisition_run")
        assert run["data_source"] == "simulated"


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


def test_duplicate_copy_skips_but_collision_fails(tmp_path, qc_file):
    """Same identity twice in one sweep: identical bytes = a backup copy
    (quiet skip); different bytes = a real collision (loud failure)."""
    copy = tmp_path / "copies" / "backup_qc.json"
    copy.parent.mkdir()
    copy.write_bytes(qc_file.read_bytes())
    collider = tmp_path / "collider_qc.json"
    collider.write_text(
        json.dumps(dict(QC, metrics=dict(QC["metrics"], sbr=99.0)))
    )
    with mock_registry() as reg:
        counts = ingest_paths(
            reg,
            [str(qc_file), str(copy), str(collider)],
            log=lambda *_: None,
        )
        assert counts == {"ingested": 1, "skipped": 1, "failed": 1}
        (metrics,) = reg.list("metrics")
        assert metrics["sbr"] == 4.2  # the collider never landed


def test_broken_registry_aborts_instead_of_reingesting(qc_file):
    """A non-404 failure on the existence check (auth, DNS, wrong URL) must
    abort the sweep loudly — not read as 'not ingested yet'."""

    class Boom(Exception):
        pass

    class BrokenClient:
        def get(self, *a, **k):
            raise Boom("401 unauthorized")

        def bulk_ingest(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("must not POST against a broken registry")

    with pytest.raises(Boom):
        ingest_paths(BrokenClient(), [str(qc_file)], log=lambda *_: None)


def test_nonexistent_path_is_a_usage_error(tmp_path):
    with pytest.raises(FileNotFoundError):
        list(ingest_paths(None, [str(tmp_path / "typo")], log=lambda *_: None))
    with pytest.raises(SystemExit) as exc:
        main([str(tmp_path / "typo"), "--dry-run"])
    assert exc.value.code == 2


def test_console_entry_dry_run(qc_file, capsys, monkeypatch):
    """main() wires args; --dry-run existence-checks but never POSTs, and
    omitting --data-source warns loudly (append-only, unretrofittable)."""

    class NotFound(Exception):
        def __init__(self):
            self.response = type("R", (), {"status_code": 404})()

    class ReadOnly:
        def get(self, *a, **k):
            raise NotFound()

        def bulk_ingest(self, *a, **k):  # pragma: no cover - must not run
            raise AssertionError("dry run must not POST")

    monkeypatch.setattr(
        "picasso_registry.client.RegistryClient",
        lambda *a, **k: ReadOnly(),
    )
    rc = main([str(qc_file), "--dry-run"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "1 ingested" in out
    assert "--data-source not given" in out

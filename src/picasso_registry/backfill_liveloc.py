"""Backfill LiveLocalization ``*_qc.json`` files into the registry.

The V0.7/V0.8 LiveLocalization tool writes one ``<name>[_PosN]_qc.json`` per
measurement position (``schema_version: "qc-1"``) with identity, sample
metadata, acquisition parameters and the final quality metrics. This module
maps those files onto the registry schema and POSTs them via ``/bulk`` —
the historical-archive half of bootstrapping real cohort history (the
``_locs.hdf5`` re-computation backfill is WP-7 and separate).

    picasso-registry-backfill-liveloc /pool/users --url http://registry:8000 \\
        --data-source acquired

Mapping decisions (deliberate, documented here so they stay stable):

* **Identity.** qc.json carries no unique id, so every row id is a
  *deterministic* ULID derived from ``created`` + ``measurement.name`` +
  ``position_label`` (timestamp bits from ``created``, randomness bits from a
  SHA-256 of the identity + a per-table salt). Naive ``created`` timestamps
  are interpreted **as UTC by convention** — the wall-clock reading is a
  label, not an instant, and pinning it keeps the ids identical no matter
  which machine/timezone re-runs the sweep. The folder path is deliberately
  NOT part of the identity: paths differ across mounts/backups and would
  double-ingest copies of the same measurement (caveat: a legacy file
  *without* ``measurement.name`` falls back to the file stem, which weakens
  that guarantee to same-filename copies). Determinism makes re-runs
  idempotent: a file whose acquisition_run already exists is skipped, and
  within one sweep a second file with the same identity is a quiet skip when
  its bytes are identical (a backup copy) but a loud failure when they
  differ (a real collision). Note the V0.8 GUI's "Update qc.json" button
  rewrites the file with a *fresh* ``created``, so a re-exported measurement
  ingests as a **second, unlinked run**; the artifact checksums are what tie
  the vintages together.
* **Metrics.** Keys are renamed onto the registry's typed columns
  (:data:`METRIC_RENAMES`); any scalar whose (renamed) key matches a typed
  numeric column lands in that column, and everything else — unmapped keys,
  non-scalars (per-batch series), values colliding with an occupied slot or
  a reserved/row-plumbing name — rides along under its original key or a
  ``*_raw`` suffix, where the service folds it into the append-only
  ``extra`` JSON. Nothing is dropped. ``nena_nm`` is the whole-FOV NeNA
  (``nena_global_nm``); the zoom-ROI NeNA stays ``nena_zoom_nm`` in
  ``extra`` (A1 tracks them as distinct).
* **Provenance.** Rows land as ``analysis_run.kind = "liveloc-qc"`` with the
  tool's ``software_version``, so V0.8-computed metrics are always
  distinguishable from pipeline-recomputed (WP-7) metrics on the same run.
  ``--data-source`` stamps the A15/C24 acquired-vs-simulated flag — operator
  knowledge, not guessable from the files, and unretrofittable (append-only).
* **No taxonomy guessing.** The free-text sample fields (project/cell/target/
  dye) need a curated vocabulary survey before they map onto the descriptor
  taxonomy; v1 keeps the whole ``sample`` block verbatim in
  ``experiment.extra`` and leaves ``sample_taxon_id`` NULL, so taxonomy can be
  attached later without re-ingesting.
* **Time series stay in the file.** ``series``/``series_frc`` don't fit the
  metrics table; the qc.json itself is linked as an ``artifact`` (sha256 +
  size), so trend analysis reads the original. Small per-run summaries
  (``derived_slopes``, ``regions``, advisor findings) ride in
  ``analysis_run.extra``.

Needs the ``[client]`` extra (requests + python-ulid).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

#: Client-side service URL / bearer token env vars (NOT PAINT_REGISTRY_URL,
#: which is the *service's* database URL).
SERVICE_URL_ENV = "PAINT_REGISTRY_SERVICE_URL"
TOKEN_ENV = "PAINT_REGISTRY_TOKEN"

#: qc.json metric key -> registry typed column (schemas.Metrics).
METRIC_RENAMES = {
    "localizations": "n_locs",
    "frc_resolution_nm": "frc_nm",
    "nena_global_nm": "nena_nm",
    "photons_per_loc": "photons_median",
    "loc_density_fov_um2": "density_locs_um2",
}

#: Every scalar (int/float) numeric column of schemas.Metrics: a scalar
#: qc.json value whose (renamed) key matches lands in the typed column.
#: Kept in sync with the schema by tests/test_backfill_liveloc.py.
_TYPED_SCALAR_COLUMNS = frozenset(
    {
        "n_locs",
        "nena_nm",
        "frc_nm",
        "decorr_nm",
        "photons_median",
        "lp_x_nm",
        "lp_y_nm",
        "lp_z_nm",
        "psf_sigma_x",
        "psf_sigma_y",
        "psf_ellipticity",
        "net_gradient_median",
        "spots_per_frame",
        "usable_frame_frac",
        "drift_nm",
        "drift_residual_nm",
        "drift_rate_nm_min",
        "z_focus_residual_nm",
        "sbr",
        "background",
        "dark_time_s",
        "bright_time_s",
        "k_on",
        "k_off",
        "binding_freq_hz",
        "events_per_site",
        "damage_decay_rate",
        "duty_cycle",
        "density_locs_um2",
        "qpaint_count",
        "labeling_efficiency",
        "n_clusters",
        "mean_cluster_size",
        "nnd_median_nm",
        "spinna_fit_quality",
        "g5m_n_molecules",
        "g5m_false_pos_est",
        "registration_error_nm",
    }
)

#: Non-scalar typed columns + the row's own plumbing: a qc.json metric key
#: with one of these names must never overwrite the row fields we set (the
#: analysis_run join!) nor hit a JSON/dict-typed column with the wrong shape.
_RESERVED_METRIC_KEYS = frozenset(
    {"id", "analysis_run_id", "scope", "extra", "spinna_oligomer_fractions"}
)

ANALYSIS_KIND = "liveloc-qc"


def _ulid_cls():
    """Lazy python-ulid import with an actionable error if absent."""
    try:
        from ulid import ULID
    except ModuleNotFoundError:
        raise SystemExit(
            "python-ulid is required for the backfill — install the client "
            "stack: pip install 'picasso-registry[client]'"
        )
    return ULID


def _parse_created(created_raw: str) -> datetime:
    """Parse the qc.json ``created`` stamp; naive values are pinned to UTC.

    The pin is a *convention for id-minting determinism*, not a claim about
    the lab clock: ``datetime.timestamp()`` on a naive value would use the
    running machine's local timezone, so the "deterministic" ids would change
    with the host TZ/DST and a re-run from another machine would re-ingest
    the whole archive. A trailing ``Z`` (not understood by Python 3.10's
    ``fromisoformat``) is normalized; explicit offsets are honored.
    """
    created = datetime.fromisoformat(created_raw.replace("Z", "+00:00"))
    if created.tzinfo is None:
        created = created.replace(tzinfo=timezone.utc)
    return created


def _deterministic_ulid(created: datetime, identity: str, salt: str) -> str:
    """A ULID whose timestamp bits come from ``created`` (UTC-pinned) and
    whose randomness bits are a SHA-256 of ``identity`` + ``salt`` — stable
    across re-runs and machines, so replayed ingests dedupe on the primary
    key instead of duplicating."""
    ts_ms = int(created.timestamp() * 1000)
    digest = hashlib.sha256(f"{salt}|{identity}".encode()).digest()
    return str(_ulid_cls().from_bytes(ts_ms.to_bytes(6, "big") + digest[:10]))


def _scalar(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _compact(row: dict) -> dict:
    """Drop None-valued keys: the service folds unknown keys into the
    append-only ``extra`` JSON, and always-present-but-null keys would pad
    every row and erase the vintage signal of key *presence*."""
    return {k: v for k, v in row.items() if v is not None}


class SkipFile(Exception):
    """This qc.json cannot be ingested (reason in str); skip it."""


def map_qc(
    payload: dict,
    source: str,
    raw: bytes | None = None,
    data_source: str | None = None,
) -> dict[str, list[dict]]:
    """Map one parsed qc.json onto registry /bulk tables (pure function).

    ``raw`` is the file's byte content for the artifact checksum/size; when
    omitted (e.g. mapping an already-parsed payload) the artifact row carries
    only the URI. ``data_source`` stamps the A15/C24 acquired-vs-simulated
    provenance flag — it MUST be "simulated" when ingesting a simulation
    databank (append-only: the flag can't be retrofitted, and sims are
    default-excluded from learned cohort ranges only through it).
    """
    measurement = payload.get("measurement") or {}
    sample = payload.get("sample") or {}
    acquisition = payload.get("acquisition") or {}
    qc_metrics = payload.get("metrics") or {}

    created_raw = payload.get("created")
    if not created_raw:
        raise SkipFile("no 'created' timestamp — cannot mint a stable run id")
    created = _parse_created(created_raw)

    name = measurement.get("name") or Path(source).stem
    position = measurement.get("position_label")
    identity = f"{created_raw}|{name}|{position}"
    ids = {
        salt: _deterministic_ulid(created, identity, salt)
        for salt in ("exp", "chan", "acq", "fov", "ana")
    }

    experiment = {
        "id": ids["exp"],
        "created_at": created_raw,
        "operator": measurement.get("user") or sample.get("user"),
        "buffer": sample.get("buffer"),
        "notes": sample.get("notes"),
        # The whole sample block verbatim: the taxonomy/descriptor mapping
        # happens later, from curated vocabulary, without re-ingesting.
        "sample": sample,
        "sample_provenance": payload.get("sample_provenance"),
        "metadata_complete": payload.get("metadata_complete"),
        "metadata_missing": payload.get("metadata_missing"),
        "protocol": payload.get("protocol"),
    }

    conc_pm = sample.get("conc_pm")
    power_mw = sample.get("power_mw")
    if not _scalar(power_mw):
        power_mw = acquisition.get("power_mw")
    target_channel = {
        "id": ids["chan"],
        "experiment_id": ids["exp"],
        "target": sample.get("target") or sample.get("origami_structure"),
        "binder": sample.get("binder"),
        "dye": sample.get("dye"),
        "imager_seq": sample.get("imager")
        or acquisition.get("imager_sequence"),
        "imager_conc_nM": conc_pm / 1000.0 if _scalar(conc_pm) else None,
        "exposure_ms": acquisition.get("exposure_ms"),
        "laser_power_mW": power_mw if _scalar(power_mw) else None,
    }

    acquisition_run = {
        "id": ids["acq"],
        "experiment_id": ids["exp"],
        "microscope_id": measurement.get("microscope"),
        "started_at": created_raw,
        "status": "backfilled",
        "data_source": data_source,
        "raw_data_path": measurement.get("folder"),
        "measurement": measurement,
        "acquisition": acquisition,
        "software_version": payload.get("software_version"),
        "schema_version": payload.get("schema_version"),
        "source_json": source,
    }

    frame_count = acquisition.get("frames_per_position")
    if not _scalar(frame_count):
        frame_count = acquisition.get("total_frames")
    fov = {
        "id": ids["fov"],
        "acquisition_run_id": ids["acq"],
        "target_channel_id": ids["chan"],
        "exposure_ms": acquisition.get("exposure_ms"),
        "frame_count": frame_count if _scalar(frame_count) else None,
        "position_label": position,
        "n_positions": measurement.get("n_positions"),
        "pixelsize_nm": acquisition.get("pixelsize_nm"),
        "width_px": acquisition.get("width_px"),
        "height_px": acquisition.get("height_px"),
    }

    analysis_run = {
        "id": ids["ana"],
        "acquisition_run_id": ids["acq"],
        "fov_id": ids["fov"],
        "kind": ANALYSIS_KIND,
        "attempt": 1,
        "status": "done",
        "finished_at": created_raw,
        "software_version": payload.get("software_version"),
        "schema_version": payload.get("schema_version"),
        "clustering": payload.get("clustering"),
        "advisor": payload.get("advisor"),
        "frc_trend": payload.get("frc_trend"),
        "db_anomalies": payload.get("db_anomalies"),
        "filter_suggestion": payload.get("filter_suggestion"),
        "dye_analysis": payload.get("dye_analysis"),
        # V0.7-origami-backfill vintage: small per-run trend slopes and
        # per-ROI summaries (the dense series stay in the artifact file).
        "derived_slopes": payload.get("derived_slopes"),
        "regions": payload.get("regions"),
    }

    metrics: dict[str, Any] = {
        "analysis_run_id": ids["ana"],
        "scope": "liveloc",
    }
    for key, value in qc_metrics.items():
        target = METRIC_RENAMES.get(key, key)
        if (
            target in _TYPED_SCALAR_COLUMNS
            and _scalar(value)
            and target not in metrics
        ):
            metrics[target] = value
        elif target in _TYPED_SCALAR_COLUMNS or target in (
            _RESERVED_METRIC_KEYS
        ):
            # would fail the typed column's validation (non-scalar), lose a
            # value to an occupied slot (rename collision), or clobber the
            # row's own fields — suffix it so it lands in `extra` instead.
            metrics[f"{key}_raw"] = value
        else:
            # unknown key: keep it as-is; the service folds it into `extra`.
            metrics[key] = value

    artifact = {
        "analysis_run_id": ids["ana"],
        "kind": "liveloc-qc-json",
        "uri": source,
        "checksum": (
            "sha256:" + hashlib.sha256(raw).hexdigest() if raw else None
        ),
        "size_bytes": len(raw) if raw else None,
    }

    return {
        "experiment": [_compact(experiment)],
        "target_channel": [_compact(target_channel)],
        "acquisition_run": [_compact(acquisition_run)],
        "fov": [_compact(fov)],
        "analysis_run": [_compact(analysis_run)],
        "metrics": [metrics],
        "artifact": [_compact(artifact)],
    }


def _already_ingested(client, run_id: str) -> bool:
    """True if the acquisition_run exists, False on a definite 404. Any
    other failure (auth, DNS, timeout, non-registry endpoint) raises — a
    broken deployment must abort the sweep loudly, not read as 'not yet
    ingested' (re-POST storm) or, under --dry-run, as a bogus preview."""
    try:
        client.get("acquisition_run", run_id)
        return True
    except Exception as exc:
        response = getattr(exc, "response", None)
        if getattr(response, "status_code", None) == 404:
            return False
        raise


def iter_qc_files(paths: list[str]):
    """Yield qc.json paths from files and (recursively) directories.

    Directory scans match ``*_qc.json`` plus bare ``qc.json`` (the two
    LiveLocalization naming forms); explicitly listed files are taken as
    given. A path that doesn't exist is a usage error, raised here so a
    typo'd mount never reads as an empty (successful) sweep.
    """
    for entry in paths:
        p = Path(entry)
        if not p.exists():
            raise FileNotFoundError(entry)
        if p.is_dir():
            matches = sorted(
                set(p.rglob("*_qc.json")) | set(p.rglob("qc.json"))
            )
            yield from matches
        else:
            yield p


def ingest_paths(
    client,
    paths: list[str],
    *,
    dry_run: bool = False,
    data_source: str | None = None,
    log=print,
) -> dict[str, int]:
    """Ingest every qc.json under ``paths``; returns counts. One file is one
    /bulk transaction; a file that fails to parse/map/POST is reported and
    skipped, never aborts the sweep (backfill is append-only and
    re-runnable) — but an unreachable/broken registry aborts loudly."""
    counts = {"ingested": 0, "skipped": 0, "failed": 0}
    seen: dict[str, tuple[str, str]] = {}  # run_id -> (first path, sha256)
    for path in iter_qc_files(paths):
        try:
            raw = Path(path).read_bytes()
            payload = json.loads(raw)
            tables = map_qc(
                payload, str(path), raw=raw, data_source=data_source
            )
        except SkipFile as exc:
            log(f"SKIP  {path}: {exc}")
            counts["skipped"] += 1
            continue
        except (OSError, ValueError) as exc:
            log(f"FAIL  {path}: unreadable/invalid ({exc})")
            counts["failed"] += 1
            continue
        run_id = tables["acquisition_run"][0]["id"]
        sha = hashlib.sha256(raw).hexdigest()
        if run_id in seen:
            first_path, first_sha = seen[run_id]
            if sha == first_sha:
                log(f"SKIP  {path}: duplicate copy of {first_path}")
                counts["skipped"] += 1
            else:
                log(
                    f"FAIL  {path}: identity collision with {first_path} "
                    "(same created+name+position, different content) — "
                    "not ingested; disambiguate the files"
                )
                counts["failed"] += 1
            continue
        seen[run_id] = (str(path), sha)
        if _already_ingested(client, run_id):
            log(f"SKIP  {path}: already ingested (run {run_id})")
            counts["skipped"] += 1
            continue
        if dry_run:
            log(f"DRY   {path}: would ingest as run {run_id}")
            counts["ingested"] += 1
            continue
        try:
            client.bulk_ingest(**tables)
        except Exception as exc:
            log(f"FAIL  {path}: {exc}")
            counts["failed"] += 1
            continue
        log(f"OK    {path}: run {run_id}")
        counts["ingested"] += 1
    return counts


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="picasso-registry-backfill-liveloc",
        description=(
            "Backfill LiveLocalization *_qc.json files into the registry "
            "(idempotent: re-runs skip already-ingested measurements)."
        ),
    )
    parser.add_argument(
        "paths",
        nargs="+",
        help="qc.json files and/or directories to scan recursively",
    )
    parser.add_argument(
        "--url",
        default=os.environ.get(SERVICE_URL_ENV, "http://127.0.0.1:8000"),
        help=f"registry service URL (env {SERVICE_URL_ENV}; "
        "default http://127.0.0.1:8000)",
    )
    parser.add_argument(
        "--token",
        default=os.environ.get(TOKEN_ENV),
        help=f"write-scope bearer token (env {TOKEN_ENV}; omit on loopback)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="map + report, POST nothing (existence checks still query "
        "the registry)",
    )
    parser.add_argument(
        "--data-source",
        choices=["acquired", "simulated"],
        default=None,
        help="A15/C24 provenance stamped on every ingested run. REQUIRED "
        "knowledge, not guessable from the files: pass 'simulated' for a "
        "simulation databank (sims are default-excluded from learned cohort "
        "ranges via this flag, and it cannot be retrofitted — the store is "
        "append-only). Omitted = recorded as unknown (NULL).",
    )
    args = parser.parse_args(argv)

    if args.data_source is None:
        print(
            "note: --data-source not given — every ingested run's "
            "data_source will be recorded as unknown (NULL) permanently "
            "(append-only). Pass --data-source acquired|simulated if you "
            "know the provenance (you do)."
        )

    from .client import RegistryClient

    client = RegistryClient(args.url, token=args.token)
    try:
        counts = ingest_paths(
            client,
            args.paths,
            dry_run=args.dry_run,
            data_source=args.data_source,
        )
    except FileNotFoundError as exc:
        parser.error(f"path does not exist: {exc}")
    except Exception as exc:
        raise SystemExit(
            f"aborted: cannot reach/query the registry at {args.url}: {exc}"
        )
    if sum(counts.values()) == 0:
        print(
            "warning: no qc.json files found under the given paths "
            "(patterns: *_qc.json, qc.json — check the path and casing)"
        )
    print(
        "backfill{}: {ingested} ingested, {skipped} skipped, "
        "{failed} failed".format(
            " (dry run)" if args.dry_run else "", **counts
        )
    )
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())

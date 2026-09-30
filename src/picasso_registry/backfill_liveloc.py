"""Backfill LiveLocalization ``*_qc.json`` files into the registry.

The V0.7/V0.8 LiveLocalization tool writes one ``<name>[_PosN]_qc.json`` per
measurement position (``schema_version: "qc-1"``) with identity, sample
metadata, acquisition parameters and the final quality metrics. This module
maps those files onto the registry schema and POSTs them via ``/bulk`` —
the historical-archive half of bootstrapping real cohort history (the
``_locs.hdf5`` re-computation backfill is WP-7 and separate).

    picasso-registry-backfill-liveloc /pool/users --url http://registry:8000

Mapping decisions (deliberate, documented here so they stay stable):

* **Identity.** qc.json carries no unique id, so every row id is a
  *deterministic* ULID derived from ``created`` + ``measurement.name`` +
  ``position_label`` (timestamp bits from ``created``, randomness bits from a
  SHA-256 of the identity + a per-table salt). The folder path is deliberately
  NOT part of the identity: paths differ across mounts/backups and would
  double-ingest copies of the same measurement. Determinism makes re-runs
  idempotent: a file whose acquisition_run already exists is skipped.
* **Metrics.** Keys are renamed onto the registry's typed columns
  (:data:`METRIC_RENAMES`); only int/float values go into typed columns, and
  every unmapped or non-scalar key rides along top-level, where the service
  folds it into the append-only ``extra`` JSON — nothing is dropped.
  ``nena_nm`` is the whole-FOV NeNA (``nena_global_nm``); the zoom-ROI NeNA
  stays ``nena_zoom_nm`` in ``extra`` (A1 tracks them as distinct).
* **Provenance.** Rows land as ``analysis_run.kind = "liveloc-qc"`` with the
  tool's ``software_version``, so V0.8-computed metrics are always
  distinguishable from pipeline-recomputed (WP-7) metrics on the same run.
* **No taxonomy guessing.** The free-text sample fields (project/cell/target/
  dye) need a curated vocabulary survey before they map onto the descriptor
  taxonomy; v1 keeps the whole ``sample`` block verbatim in
  ``experiment.extra`` and leaves ``sample_taxon_id`` NULL, so taxonomy can be
  attached later without re-ingesting.
* **Time series stay in the file.** ``series``/``series_frc`` don't fit the
  metrics table; the qc.json itself is linked as an ``artifact`` (sha256 +
  size), so trend analysis reads the original.

Needs the ``[client]`` extra (requests + python-ulid).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any

from ulid import ULID

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

#: Typed Metrics columns this backfill may fill directly (a value only lands
#: in a typed column if it is a plain int/float; everything else -> extra).
_TYPED_TARGETS = {
    "n_locs",
    "frc_nm",
    "nena_nm",
    "photons_median",
    "density_locs_um2",
    "sbr",
    "background",
}

ANALYSIS_KIND = "liveloc-qc"


def _deterministic_ulid(created: datetime, identity: str, salt: str) -> str:
    """A ULID whose timestamp bits come from ``created`` and whose randomness
    bits are a SHA-256 of ``identity`` + ``salt`` — stable across re-runs, so
    replayed ingests dedupe on the primary key instead of duplicating."""
    ts_ms = int(created.timestamp() * 1000)
    digest = hashlib.sha256(f"{salt}|{identity}".encode()).digest()
    return str(ULID.from_bytes(ts_ms.to_bytes(6, "big") + digest[:10]))


def _scalar(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


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
    created = datetime.fromisoformat(created_raw)

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
        "laser_power_mW": sample.get("power_mw")
        or acquisition.get("power_mw"),
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

    fov = {
        "id": ids["fov"],
        "acquisition_run_id": ids["acq"],
        "target_channel_id": ids["chan"],
        "exposure_ms": acquisition.get("exposure_ms"),
        "frame_count": acquisition.get("frames_per_position")
        or acquisition.get("total_frames"),
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
        if target in _TYPED_TARGETS and _scalar(value):
            metrics[target] = value
        elif target in _TYPED_TARGETS:
            # non-scalar under a typed column's name (e.g. a per-batch
            # background series) — suffix it so it can't fail the typed
            # column's validation and lands in `extra` instead.
            metrics[f"{key}_raw"] = value
        else:
            # unmapped key: keep it as-is; the service folds it into `extra`.
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
        "experiment": [experiment],
        "target_channel": [target_channel],
        "acquisition_run": [acquisition_run],
        "fov": [fov],
        "analysis_run": [analysis_run],
        "metrics": [metrics],
        "artifact": [artifact],
    }


def _already_ingested(client, run_id: str) -> bool:
    """True if the acquisition_run exists. Any error -> False: an unreachable
    server then fails loudly at the /bulk POST instead of silently skipping."""
    try:
        client.get("acquisition_run", run_id)
        return True
    except Exception:
        return False


def iter_qc_files(paths: list[str]):
    """Yield qc.json paths from files and (recursively) directories."""
    for entry in paths:
        p = Path(entry)
        if p.is_dir():
            yield from sorted(p.rglob("*qc.json"))
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
    /bulk transaction; a failing file is reported and skipped, never aborts
    the sweep (backfill is append-only and re-runnable)."""
    counts = {"ingested": 0, "skipped": 0, "failed": 0}
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
        help="map + report, POST nothing",
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

    from .client import RegistryClient

    client = RegistryClient(args.url, token=args.token)
    counts = ingest_paths(
        client,
        args.paths,
        dry_run=args.dry_run,
        data_source=args.data_source,
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

"""WP-DASH — the registry's browse / compare / rank dashboard (read layer).

Template: ``context/Databank_Dashboard/`` (Recommendation Part VI, "Dashboard
& the read layer"). Two pieces, mirroring monet's dashboard topology exactly
(A13 web-client + A9/C18 tokens):

* ``GET /dashboard`` — the **public HTML shell** (a single self-contained
  page, no data in it). The browser stores a lab-wide **read** token in
  localStorage and sends ``Authorization: Bearer …`` on every data fetch; a
  401 re-opens the login overlay. Public-shell + guarded-data is the same
  carve-out monet ships; it is allow-listed in the route-coverage test.
* ``GET /dashboard/api/measurements`` — the **derived flat read model**
  (read-scoped): one row per acquisition run, joining experiment / target
  channel / analysis run / metrics into the dashboard-friendly shape the
  Databank prototype built as its ``measurements`` table. Computed on demand
  from the normalized store (a query cache, NOT a second source of truth);
  at today's volumes that is a handful of full-table reads per refresh.

The **composite quality ranking** follows the template's recipe
(``compare_qc.py``): min-max-normalize each *quality* metric — FRC and NeNA
flipped (lower is better), specificity and SBR as-is — then average the
available components. Throughput/slope numbers are deliberately NOT part of
the score (the template's rule: slopes are comparison, not quality). The
scoring lives here in Python so the brief's verification ("ranking reproduces
the template's composite ordering") is a unit test; the page re-applies the
same recipe client-side to whatever subset the user has filtered.
"""

from importlib.resources import files

from fastapi import APIRouter, Depends
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from . import models
from .auth import require_scope
from .db import get_session

router = APIRouter()

_READ = [Depends(require_scope("read"))]

#: (metric key in the flat row, higher_is_better) — the template's QUALITY
#: set; direction-aware normalization per compare_qc.py.
QUALITY_METRICS = (
    ("frc_nm", False),
    ("nena_nm", False),
    ("specificity", True),
    ("sbr", True),
)


def composite_scores(rows: list[dict]) -> list[float | None]:
    """The template's composite quality score for each row, over this set.

    Min-max-normalize each quality metric across the rows that have it
    (direction-aware), then average a row's available components. Rows with
    no quality metric at all score None. Normalization is *per compared
    set* — the same run scores differently in different cohorts, exactly as
    in ``compare_qc.py``.
    """
    spans = {}
    for key, _ in QUALITY_METRICS:
        values = [
            r[key]
            for r in rows
            if isinstance(r.get(key), (int, float))
            and not isinstance(r.get(key), bool)
        ]
        if values:
            spans[key] = (min(values), max(values))
    scores: list[float | None] = []
    for r in rows:
        parts = []
        for key, higher_better in QUALITY_METRICS:
            v = r.get(key)
            if key not in spans or not isinstance(v, (int, float)):
                continue
            lo, hi = spans[key]
            norm = 0.5 if hi == lo else (v - lo) / (hi - lo)
            parts.append(norm if higher_better else 1.0 - norm)
        scores.append(sum(parts) / len(parts) if parts else None)
    return scores


def _sample(exp) -> dict:
    return (exp.extra or {}).get("sample", {}) if exp is not None else {}


def _flat_rows(session: Session, limit: int) -> list[dict]:
    """One dashboard row per acquisition run (the derived read model)."""
    runs = (
        session.query(models.AcquisitionRun)
        .order_by(models.AcquisitionRun.id)
        .limit(limit)
        .all()
    )
    exps = {e.id: e for e in session.query(models.Experiment).all()}
    chan_by_exp: dict = {}
    for c in session.query(models.TargetChannel).all():
        chan_by_exp.setdefault(c.experiment_id, c)
    anas_by_run: dict = {}
    for a in session.query(models.AnalysisRun).order_by(models.AnalysisRun.id):
        anas_by_run.setdefault(a.acquisition_run_id, []).append(a)
    metrics_by_ana = {}
    for m in session.query(models.Metrics).order_by(models.Metrics.id):
        metrics_by_ana.setdefault(m.analysis_run_id, m)

    rows = []
    for run in runs:
        exp = exps.get(run.experiment_id)
        sample = _sample(exp)
        chan = chan_by_exp.get(run.experiment_id)
        # prefer the newest analysis that actually carries a metrics row
        ana = metrics = None
        for cand in reversed(anas_by_run.get(run.id, [])):
            if cand.id in metrics_by_ana:
                ana, metrics = cand, metrics_by_ana[cand.id]
                break
        acq_extra = run.extra or {}
        m_extra = (metrics.extra or {}) if metrics is not None else {}
        conc_nm = chan.imager_conc_nM if chan is not None else None
        rows.append(
            {
                "run_id": run.id,
                "name": (acq_extra.get("measurement") or {}).get("name"),
                "started_at": (
                    run.started_at.isoformat() if run.started_at else None
                ),
                "microscope": run.microscope_id,
                "status": run.status,
                "data_source": run.data_source,
                "software_version": acq_extra.get("software_version"),
                "operator": (exp.operator if exp is not None else None)
                or sample.get("user"),
                "sample_type": sample.get("sample_type"),
                "project": sample.get("project"),
                "target": sample.get("target")
                or sample.get("origami_structure"),
                "dye": sample.get("dye"),
                "imager": sample.get("imager"),
                "buffer": sample.get("buffer"),
                "mode": sample.get("mode"),
                "conc_pm": (
                    conc_nm * 1000.0
                    if isinstance(conc_nm, (int, float))
                    else None
                ),
                "power_mw": (
                    chan.laser_power_mW if chan is not None else None
                ),
                "exposure_ms": (
                    chan.exposure_ms if chan is not None else None
                ),
                "analysis_kind": ana.kind if ana is not None else None,
                "n_locs": metrics.n_locs if metrics else None,
                "nena_nm": metrics.nena_nm if metrics else None,
                "nena_zoom_nm": m_extra.get("nena_zoom_nm"),
                "frc_nm": metrics.frc_nm if metrics else None,
                "specificity": m_extra.get("specificity"),
                "sbr": metrics.sbr if metrics else None,
                "background": metrics.background if metrics else None,
                "photons_median": (
                    metrics.photons_median if metrics else None
                ),
                "density_locs_um2": (
                    metrics.density_locs_um2 if metrics else None
                ),
            }
        )
    for row, score in zip(rows, composite_scores(rows)):
        row["quality_score"] = score
    return rows


@router.get(
    "/dashboard/api/measurements",
    tags=["dashboard"],
    dependencies=_READ,
)
def dashboard_measurements(
    limit: int = 10000, session: Session = Depends(get_session)
) -> list[dict]:
    """The flat read model: one row per acquisition run, ranking included."""
    return _flat_rows(session, limit)


@router.get("/dashboard", response_class=HTMLResponse, tags=["dashboard"])
def dashboard_page() -> str:
    """The public HTML shell (no data; every fetch needs a read token)."""
    return files("picasso_registry").joinpath("dashboard.html").read_text()

"""FastAPI app. Scaffold: health + a couple of log endpoints + cohort stub.

Work order S0B-1: complete the REST surface (CRUD + log_* + query_cohort +
node_defaults), export the OpenAPI spec as the contract artifact, and add the
bulk-ingest endpoint used by the backfill (work order 7).
"""

from __future__ import annotations

from fastapi import FastAPI

from . import __version__
from .schemas import AcquisitionRunIn, MetricsIn

app = FastAPI(title="picasso-registry", version=__version__)


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "version": __version__}


@app.post("/acquisition_run")
def log_acquisition(run: AcquisitionRunIn) -> dict:
    # TODO: persist append-only via SessionLocal
    return {"id": run.id, "stored": True}


@app.post("/metrics")
def log_metrics(metrics: MetricsIn) -> dict:
    # TODO: persist; typed columns + JSON extra
    return {"analysis_run_id": metrics.analysis_run_id, "stored": True}


@app.get("/cohort")
def query_cohort(taxon_id: str) -> dict:
    # TODO: tree-distance fallback over sample_taxonomy
    return {"taxon_id": taxon_id, "runs": []}


def main() -> None:  # console entry point
    import uvicorn

    uvicorn.run("picasso_registry.app:app", host="127.0.0.1", port=8000)

"""FastAPI service — the registry's full REST surface (the contract).

Append-only: every table gets ``POST`` (create) and ``GET`` (read one / list);
there are deliberately no update/delete routes. On top of the generic CRUD the
service exposes the derived queries the stack depends on — ``GET /cohort``
(taxon tree-distance fallback, ranked) and ``GET /node_defaults`` (the inherited
default cascade) — plus ``POST /bulk`` for backfill ingest. Persistence goes
through ``crud`` and ``get_session`` so the in-memory mock can swap the DB.

``REGISTRY`` is the single source of truth for the per-table wiring: the CRUD
routes and the bulk-ingest order both derive from it, so adding a table is a
one-line change and ``/bulk`` can never silently drop a table the API accepts.
The entries are in FK-safe order (taxonomy first) so a single bulk transaction
resolves child rows against already-persisted parents.

This module intentionally avoids ``from __future__ import annotations``: the
route factory annotates request bodies with schema classes held in locals, and
FastAPI must see the real class objects (not stringized annotations) to build
the request models and the OpenAPI spec.
"""

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from . import __version__, crud, models, schemas
from .db import get_session
from .taxonomy import deep_merge, path_ids, tree_distance

# (url path / bulk field, schema, ORM model, persist fn), FK-safe order.
REGISTRY = [
    (
        "sample_taxonomy",
        schemas.SampleTaxonomy,
        models.SampleTaxonomy,
        crud.persist_taxonomy,
    ),
    ("experiment", schemas.Experiment, models.Experiment, crud.persist),
    ("sample_tag", schemas.SampleTag, models.SampleTag, crud.persist),
    (
        "target_channel",
        schemas.TargetChannel,
        models.TargetChannel,
        crud.persist,
    ),
    (
        "reagent_provenance",
        schemas.ReagentProvenance,
        models.ReagentProvenance,
        crud.persist,
    ),
    (
        "acquisition_run",
        schemas.AcquisitionRun,
        models.AcquisitionRun,
        crud.persist,
    ),
    ("fov", schemas.Fov, models.Fov, crud.persist),
    ("illumination", schemas.Illumination, models.Illumination, crud.persist),
    ("environment", schemas.Environment, models.Environment, crud.persist),
    (
        "fluidics_round",
        schemas.FluidicsRound,
        models.FluidicsRound,
        crud.persist,
    ),
    (
        "sample_morphology",
        schemas.SampleMorphology,
        models.SampleMorphology,
        crud.persist,
    ),
    ("analysis_run", schemas.AnalysisRun, models.AnalysisRun, crud.persist),
    ("metrics", schemas.Metrics, models.Metrics, crud.persist),
    (
        "resource_usage",
        schemas.ResourceUsage,
        models.ResourceUsage,
        crud.persist,
    ),
    ("qc", schemas.Qc, models.Qc, crud.persist),
    ("feedback", schemas.Feedback, models.Feedback, crud.persist),
    ("artifact", schemas.Artifact, models.Artifact, crud.persist),
    (
        "interpretation",
        schemas.Interpretation,
        models.Interpretation,
        crud.persist,
    ),
]


def _register_crud(app, name, schema, orm_cls, persist_fn):
    """Register POST / GET-one / GET-list routes for one table."""

    def create(payload: schema, session: Session = Depends(get_session)):
        return persist_fn(session, orm_cls, payload.model_dump())

    def get_one(item_id: str, session: Session = Depends(get_session)):
        obj = session.get(orm_cls, item_id)
        if obj is None:
            raise HTTPException(status_code=404, detail=f"{name} not found")
        return obj

    def list_rows(
        limit: int = 100,
        offset: int = 0,
        session: Session = Depends(get_session),
    ):
        # Stable ORDER BY id so pagination and result[0] are well-defined
        # (ULID ids sort by creation time) — undefined otherwise on Postgres.
        return (
            session.query(orm_cls)
            .order_by(orm_cls.id)
            .offset(offset)
            .limit(limit)
            .all()
        )

    create.__name__ = f"create_{name}"
    get_one.__name__ = f"get_{name}"
    list_rows.__name__ = f"list_{name}"

    app.post(f"/{name}", response_model=schema, tags=[name])(create)
    app.get(f"/{name}/{{item_id}}", response_model=schema, tags=[name])(
        get_one
    )
    app.get(f"/{name}", response_model=list[schema], tags=[name])(list_rows)


def create_app() -> FastAPI:
    """Build a fresh app instance (used by the service, tests, and export)."""
    app = FastAPI(
        title="picasso-registry",
        version=__version__,
        description=(
            "Append-only provenance & metrics database for the DNA-PAINT "
            "automation stack. Everything joins on run_id "
            "(acquisition_run.id)."
        ),
    )

    def _unknown_parent(request: Request, exc: crud.UnknownParent):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.add_exception_handler(crud.UnknownParent, _unknown_parent)

    def _conflict(request: Request, exc: crud.Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    app.add_exception_handler(crud.Conflict, _conflict)

    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get(
        "/cohort", response_model=list[schemas.CohortItem], tags=["query"]
    )
    def query_cohort(
        taxon_id: str,
        limit: int = 50,
        max_distance: int | None = None,
        session: Session = Depends(get_session),
    ):
        """Acquisition runs ranked by sample-taxon tree distance.

        Exact-node matches rank first; when history is sparse the ranking
        falls back *up* the tree (closer ancestors/siblings first). Only runs
        under the same taxonomy root are considered — a different top-level
        sample class is not a fallback — and ``max_distance`` optionally caps
        how far up the tree to generalize.
        """
        node = session.get(models.SampleTaxonomy, taxon_id)
        if node is None:
            raise HTTPException(status_code=404, detail="unknown taxon")
        # Select only the columns the cohort needs (not whole ORM rows) so a
        # busy taxonomy root doesn't materialize entire entities just to rank
        # them. The joins are intentionally INNER: a run with no experiment,
        # or whose experiment has no sample taxon, has no tree distance and so
        # is not a cohort member (use GET /acquisition_run for all runs).
        query = (
            session.query(
                models.AcquisitionRun.id,
                models.Experiment.id,
                models.SampleTaxonomy.id,
                models.SampleTaxonomy.name,
                models.SampleTaxonomy.path,
            )
            .join(
                models.Experiment,
                models.AcquisitionRun.experiment_id == models.Experiment.id,
            )
            .join(
                models.SampleTaxonomy,
                models.Experiment.sample_taxon_id == models.SampleTaxonomy.id,
            )
        )
        ids = path_ids(node.path)
        if ids:
            # Restrict to the same taxonomy root. autoescape escapes LIKE
            # metacharacters in the id so a root like "a_b" cannot match a
            # sibling root ("axb") through the "_" single-char wildcard.
            query = query.filter(
                models.SampleTaxonomy.path.startswith(
                    f"/{ids[0]}/", autoescape=True
                )
            )
        else:
            # A node with no materialized path has no resolvable root; rank
            # only exact-node matches rather than scanning every root.
            query = query.filter(models.SampleTaxonomy.id == node.id)
        items = [
            schemas.CohortItem(
                acquisition_run_id=run_id,
                experiment_id=exp_id,
                taxon_id=tax_id,
                taxon_name=tax_name,
                tree_distance=tree_distance(node.path, tax_path),
            )
            for run_id, exp_id, tax_id, tax_name, tax_path in query.all()
        ]
        if max_distance is not None:
            items = [it for it in items if it.tree_distance <= max_distance]
        items.sort(key=lambda it: (it.tree_distance, it.acquisition_run_id))
        return items[:limit]

    @app.get(
        "/node_defaults",
        response_model=schemas.NodeDefaults,
        tags=["query"],
    )
    def node_defaults(taxon_id: str, session: Session = Depends(get_session)):
        """Inherited defaults for a node (descendant overrides ancestor)."""
        node = session.get(models.SampleTaxonomy, taxon_id)
        if node is None:
            raise HTTPException(status_code=404, detail="unknown taxon")
        ids = path_ids(node.path)  # root -> node
        by_id = {
            n.id: n
            for n in session.query(models.SampleTaxonomy)
            .filter(models.SampleTaxonomy.id.in_(ids))
            .all()
        }
        defaults: dict = {}
        expected: dict = {}
        qc_rules: dict = {}
        for nid in ids:  # root first so descendants override
            n = by_id.get(nid)
            if n is None:
                continue
            defaults = deep_merge(defaults, n.defaults or {})
            expected = deep_merge(expected, n.expected_metrics or {})
            qc_rules = deep_merge(qc_rules, n.qc_rules or {})
        return schemas.NodeDefaults(
            taxon_id=taxon_id,
            defaults=defaults,
            expected_metrics=expected,
            qc_rules=qc_rules,
        )

    @app.post("/bulk", response_model=schemas.BulkResult, tags=["ingest"])
    def bulk_ingest(
        payload: schemas.BulkIngest,
        session: Session = Depends(get_session),
    ):
        """Insert many rows across tables in one transaction (backfill)."""
        counts: dict = {}
        for name, _schema, orm_cls, persist_fn in REGISTRY:
            rows = getattr(payload, name) or []
            if name == "sample_taxonomy":
                # parents before children so each child's path resolves
                rows = crud.order_taxonomy_by_depth(rows)
            for row in rows:
                persist_fn(session, orm_cls, row.model_dump(), commit=False)
            if rows:
                counts[name] = len(rows)
        session.commit()
        return schemas.BulkResult(counts=counts, total=sum(counts.values()))

    for name, schema, orm_cls, persist_fn in REGISTRY:
        _register_crud(app, name, schema, orm_cls, persist_fn)

    return app


app = create_app()


def main() -> None:  # console entry point
    import uvicorn

    uvicorn.run("picasso_registry.app:app", host="127.0.0.1", port=8000)

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

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from sqlalchemy import exists
from sqlalchemy.orm import Session

from . import __version__, crud, models, schemas
from .auth import (
    DEFAULT_TOKENS_ENV,
    AuthConfig,
    is_loopback_host,
    parse_tokens,
    require_scope,
)
from .db import get_session
from .taxonomy import deep_merge, path_ids, tree_distance
from .tokens import DEFAULT_ENV_FILE, DEFAULT_ENV_FILE_ENV, load_env_file

# Route-level auth dependencies (ADR 001 / C18): read on every GET, write on
# every POST/bulk. Attached via ``dependencies=[...]`` so they guard the route
# without touching handler signatures, and so the ``HTTPBearer`` security scheme
# lands in the OpenAPI contract. Enforcement is a no-op until tokens are
# configured (loopback dev / in-memory mock stay zero-config).
_READ = [Depends(require_scope("read"))]
_WRITE = [Depends(require_scope("write"))]

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

    app.post(
        f"/{name}",
        response_model=schema,
        tags=[name],
        dependencies=_WRITE,
    )(create)
    app.get(
        f"/{name}/{{item_id}}",
        response_model=schema,
        tags=[name],
        dependencies=_READ,
    )(get_one)
    app.get(
        f"/{name}",
        response_model=list[schema],
        tags=[name],
        dependencies=_READ,
    )(list_rows)


def create_app(auth: AuthConfig | None = None) -> FastAPI:
    """Build a fresh app instance (used by the service, tests, and export).

    ``auth`` is the token map the ``require_scope`` dependencies enforce at
    request time; it defaults to :meth:`AuthConfig.from_env`, so an unset
    ``PAINT_REGISTRY_TOKENS`` yields an empty (disabled) config and the loopback
    dev path / in-memory mock stay zero-config. It is stored on ``app.state`` so
    the shared dependencies read it per request without a module-global.

    On the default (from-env) path the default ``.env`` is loaded first, so a
    token map written by ``picasso-registry token`` is honored even when the
    module app is served directly (``gunicorn picasso_registry.app:app``),
    not only via the console script.
    """
    app = FastAPI(
        title="picasso-registry",
        version=__version__,
        description=(
            "Append-only provenance & metrics database for the DNA-PAINT "
            "automation stack. Everything joins on run_id "
            "(acquisition_run.id)."
        ),
    )
    if auth is None:
        load_env_file()
        auth = AuthConfig.from_env()
    app.state.auth = auth

    def _unknown_parent(request: Request, exc: crud.UnknownParent):
        return JSONResponse(status_code=400, content={"detail": str(exc)})

    app.add_exception_handler(crud.UnknownParent, _unknown_parent)

    def _conflict(request: Request, exc: crud.Conflict):
        return JSONResponse(status_code=409, content={"detail": str(exc)})

    app.add_exception_handler(crud.Conflict, _conflict)

    # /health is deliberately unauthenticated: liveness/readiness probes and
    # uptime monitors (k8s, Docker, the reverse proxy) can't carry a bearer
    # token, and it exposes only {status, version}. This is the one intentional
    # carve-out from "read on every GET" (asserted by the route-coverage test).
    @app.get("/health", tags=["meta"])
    def health() -> dict:
        return {"status": "ok", "version": __version__}

    @app.get(
        "/cohort",
        response_model=list[schemas.CohortItem],
        tags=["query"],
        dependencies=_READ,
    )
    def query_cohort(
        taxon_id: str,
        limit: int = 50,
        max_distance: int | None = None,
        modality: schemas.Modality | None = None,
        dimensionality: schemas.DimensionalityValue | None = None,
        buffer: str | None = None,
        target: str | None = None,
        target_set: list[str] | None = Query(default=None),
        target_class: schemas.TargetClass | None = None,
        session: Session = Depends(get_session),
    ):
        """Acquisition runs ranked by sample-taxon tree distance, optionally
        constrained on the other A2 descriptor axes (register C12).

        Ranking (axis 1) is unchanged: exact-node matches first, then falling
        back *up* the tree; only runs under the same taxonomy **root** are
        considered and ``max_distance`` caps how far to generalize.

        On top of that, axis 2 (``target`` / ``target_set`` names, or the
        closed ``target_class``) and axis 3 (``modality`` / ``dimensionality``
        / ``buffer``) are **independent optional filters** — each narrows the
        cohort when supplied. *How much* must match is comparison-dependent and
        left to the caller (the recommender/agent decides which axes to
        constrain for a given metric); the registry does not hard-code that
        policy. With no axis args this is exactly the S0B-1 taxon-only cohort.
        """
        node = session.get(models.SampleTaxonomy, taxon_id)
        if node is None:
            raise HTTPException(status_code=404, detail="unknown taxon")

        # Axis-2 target overlap. An empty/absent target set applies no target
        # filter here — over HTTP an empty list is indistinguishable from an
        # omitted one, so the "explicit empty set matches nothing" guarantee
        # is enforced client-side (see RegistryClient.cohort) before the call.
        targets = set(target_set or [])
        if target:
            targets.add(target)

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
        # Axis 3 filters (applied when supplied) — all single-valued on the
        # experiment.
        if modality:
            query = query.filter(
                models.Experiment.acquisition_modality == modality
            )
        if dimensionality:
            query = query.filter(
                models.Experiment.dimensionality == dimensionality
            )
        if buffer:
            query = query.filter(models.Experiment.buffer == buffer)
        # Axis 2 filters: the run's experiment must have a target_channel
        # matching the requested target name(s) and/or the closed target_class.
        # EXISTS keeps it one row per run rather than fanning out across
        # channels. (A single channel need not satisfy both — target and
        # target_class are independent EXISTS clauses.)
        tc = models.TargetChannel
        if targets:
            query = query.filter(
                exists().where(
                    (tc.experiment_id == models.Experiment.id)
                    & (tc.target.in_(targets))
                )
            )
        if target_class:
            query = query.filter(
                exists().where(
                    (tc.experiment_id == models.Experiment.id)
                    & (tc.target_class == target_class)
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
        dependencies=_READ,
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

    @app.post(
        "/bulk",
        response_model=schemas.BulkResult,
        tags=["ingest"],
        dependencies=_WRITE,
    )
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


def reload_auth(
    application=None,
    env_file: str | None = None,
    *,
    host: str | None = None,
    prefer_env: bool = False,
) -> AuthConfig:
    """Re-read the token map and refresh ``app.state.auth`` in place.

    Lets ``picasso-registry token add/revoke/rotate`` take effect on a running
    server without a restart (tokens are otherwise read only at startup).
    Only the token key is re-read from ``env_file`` — never the other
    PAINT_REGISTRY_* keys, which startup owns. With ``prefer_env=True``
    (the process env, not the file, supplied the map at startup — e.g. a
    systemd ``Environment=``) the file never overrides it, keeping the
    documented "process env wins" precedence stable across reloads. A missing
    file counts as an empty map, so deleting the ``.env`` + SIGHUP revokes
    everything — except that an empty map on a non-loopback ``host`` is
    refused fail-closed (ADR 001): the previous config is kept and a
    ``RuntimeError`` raised; a restart then hits the startup guard, which
    refuses the bind. Reassigning ``app.state.auth`` is a single attribute
    set, so an in-flight request sees either the old or the new config —
    both valid.
    """
    import os

    application = app if application is None else application
    raw = os.environ.get(DEFAULT_TOKENS_ENV)
    if env_file and not prefer_env:
        from dotenv import dotenv_values

        raw = (
            dotenv_values(env_file).get(DEFAULT_TOKENS_ENV)
            if os.path.exists(env_file)
            else None
        )
    cfg = AuthConfig(parse_tokens(raw))
    if host is not None and not is_loopback_host(host) and not cfg.enabled:
        raise RuntimeError(
            "refusing to reload an empty token map on non-loopback host "
            f"{host!r}; keeping the previous tokens (restart the service to "
            "apply — the startup guard then refuses the unauthenticated bind)"
        )
    # Mirror the accepted map into the env only after the guard, so a refused
    # reload leaves no trace.
    if raw is None:
        os.environ.pop(DEFAULT_TOKENS_ENV, None)
    else:
        os.environ[DEFAULT_TOKENS_ENV] = raw
    application.state.auth = cfg
    return cfg


def install_auth_reload(
    application=None,
    env_file: str | None = None,
    *,
    host: str | None = None,
    prefer_env: bool = False,
) -> bool:
    """Install a SIGHUP handler that live-reloads auth (Unix only).

    Returns True if installed. SIGHUP doesn't exist on Windows, where a token
    change needs a service restart instead. Must be called from the main
    thread (before ``uvicorn.run``); uvicorn only claims SIGINT/SIGTERM, so
    SIGHUP is ours. Note the side effect: the handler replaces SIGHUP's
    default terminate-on-hangup, so the caller should install it only when a
    reload can matter (``main`` gates on auth being enabled). Under
    ``--reload`` (dev) uvicorn serves from a child process the handler
    doesn't reach — dev is loopback/token-free anyway.
    """
    import logging
    import signal

    if not hasattr(signal, "SIGHUP"):
        return False
    logger = logging.getLogger(__name__)

    def _handler(signum, frame):
        try:
            cfg = reload_auth(
                application, env_file, host=host, prefer_env=prefer_env
            )
            logger.info(
                "SIGHUP: reloaded auth (%d token(s) configured)",
                len(cfg.tokens),
            )
        except Exception:
            logger.error(
                "SIGHUP auth reload failed — keeping the previous token map",
                exc_info=True,
            )

    signal.signal(signal.SIGHUP, _handler)
    return True


def main(argv: list[str] | None = None) -> None:
    """Console entry point: run the service under uvicorn.

    Host / port / DB URL / reload are configurable via CLI flags or the
    matching env vars, so the same image runs in dev (loopback SQLite) and as
    a networked service (Postgres) without a code change::

        picasso-registry                          # 127.0.0.1:8000, SQLite
        picasso-registry --host 0.0.0.0 --port 80
        PAINT_REGISTRY_URL=postgresql+psycopg://… picasso-registry
        picasso-registry token add --scope write --label microscope-mercury

    ``picasso-registry token …`` (the token-store admin CLI) is dispatched by
    the console entry point (``cli.main``) *before* this module is imported,
    so a malformed token map can't crash the tool that repairs it. The
    PAINT_REGISTRY_* settings (including the token map that CLI writes) can
    also come from a ``.env`` file: ``--env-file PATH``,
    ``$PAINT_REGISTRY_ENV_FILE``, or a ``./.env`` if present — explicit
    process env vars win over the file. On Unix a token-armed service
    live-reloads the token map on ``SIGHUP``.

    The DB URL is read from ``PAINT_REGISTRY_URL`` by ``db.py`` at import time;
    ``--db-url`` (and a URL first supplied by the ``.env``) sets that env var
    *and* rebinds the engine (``db.configure``) so it takes effect even though
    ``db.py`` was already imported. Migrations (``alembic upgrade head``) are
    the production path for creating the schema — see the README "Deploy /
    run" section.
    """
    import argparse
    import os
    import sys

    if argv is None:
        argv = sys.argv[1:]

    # Startup provenance snapshots, taken before the .env can add anything:
    # whether the *process env* supplied the token map decides SIGHUP-reload
    # precedence, and a .env-supplied DB URL must rebind the import-time
    # engine below.
    tokens_from_process_env = os.environ.get(DEFAULT_TOKENS_ENV) is not None
    url_before = os.environ.get("PAINT_REGISTRY_URL")

    # The other flags read their env-var defaults at parser construction, so
    # the .env must be loaded first — pre-scan --env-file before the real
    # parse. A missing *explicit* path is an error (reported via the real
    # parser for a proper usage message) and suppresses the ./.env fallback —
    # never load a file the caller didn't ask for on an error path.
    pre = argparse.ArgumentParser(add_help=False)
    pre.add_argument(
        "--env-file", default=os.environ.get(DEFAULT_ENV_FILE_ENV)
    )
    env_file = pre.parse_known_args(argv)[0].env_file
    env_file_error = None
    if env_file is not None and not os.path.exists(env_file):
        env_file_error = f"env file not found: {env_file!r}"
        env_file = None
    elif env_file is None and os.path.exists(DEFAULT_ENV_FILE):
        env_file = DEFAULT_ENV_FILE
    if env_file is not None:
        from dotenv import load_dotenv

        load_dotenv(env_file, override=False)

    parser = argparse.ArgumentParser(
        prog="picasso-registry",
        epilog=(
            "subcommand: `picasso-registry token add|list|revoke|rotate` "
            "manages the bearer-token store (see `picasso-registry token "
            "-h`)."
        ),
    )
    parser.add_argument(
        "--env-file",
        default=None,
        help="load PAINT_REGISTRY_* settings (incl. the token map written by "
        "`picasso-registry token`) from this .env before starting (env "
        f"{DEFAULT_ENV_FILE_ENV}; default: ./{DEFAULT_ENV_FILE} if present; "
        "process env vars win)",
    )
    parser.add_argument(
        "--host",
        default=os.environ.get("PAINT_REGISTRY_HOST", "127.0.0.1"),
        help="bind host (env PAINT_REGISTRY_HOST; default 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="bind port (env PAINT_REGISTRY_PORT; default 8000)",
    )
    parser.add_argument(
        "--db-url",
        default=None,
        help="database URL (rebinds the engine; also sets PAINT_REGISTRY_URL)",
    )
    parser.add_argument(
        "--reload",
        action="store_true",
        help="auto-reload on code change (dev only)",
    )
    args = parser.parse_args(argv)
    if env_file_error:
        parser.error(env_file_error)

    # Resolve the port from the env default lazily so a malformed
    # PAINT_REGISTRY_PORT gives a clean usage error, not a raw traceback at
    # parser-construction time. A bad --port on the CLI is already caught by
    # argparse's own ``type=int``.
    port = args.port
    if port is None:
        raw = os.environ.get("PAINT_REGISTRY_PORT", "8000")
        try:
            port = int(raw)
        except ValueError:
            parser.error(
                f"invalid PAINT_REGISTRY_PORT {raw!r}: not an integer"
            )

    # Refresh the served app's auth from the (possibly .env-augmented) env:
    # the module-level ``app = create_app()`` ran at import, before the .env
    # was loaded, and uvicorn's import string resolves to that same object —
    # without this refresh the guard below could pass while the *served*
    # config stayed empty (401 for valid tokens, unauthenticated on-box
    # writes). A malformed map gets the same clean-usage-error treatment as a
    # malformed port above.
    try:
        auth_cfg = AuthConfig.from_env()
    except ValueError as exc:
        parser.error(f"invalid {DEFAULT_TOKENS_ENV}: {exc}")
    app.state.auth = auth_cfg

    # Fail-closed host guard (ADR 001 / C18): refuse to *start* on a
    # non-loopback host unless tokens are configured, so a misconfigured
    # networked bind fails fast with a clear error instead of serving. This
    # covers the console-script / Docker path; the belt-and-suspenders is the
    # request-time net in ``auth.require_scope`` (a disabled-auth service
    # refuses any non-loopback request), which holds the invariant even when the
    # module app is served directly (gunicorn/uvicorn, skipping this guard). The
    # loopback dev path stays zero-config.
    if not is_loopback_host(args.host) and not auth_cfg.enabled:
        parser.error(
            f"refusing to bind non-loopback host {args.host!r} without auth: "
            "set PAINT_REGISTRY_TOKENS (token:scope:label,...) or bind "
            "127.0.0.1 (see docs/adr/001-service-authentication.md)"
        )

    if args.db_url:
        # db.py reads PAINT_REGISTRY_URL and builds engine/SessionLocal at
        # import time — which already happened when this module imported .db.
        # Setting the env var alone would be ignored (uvicorn re-imports the
        # already-loaded app in-process), so rebind the engine explicitly.
        os.environ["PAINT_REGISTRY_URL"] = args.db_url
        from .db import configure

        configure(args.db_url)
    elif os.environ.get("PAINT_REGISTRY_URL") != url_before:
        # The .env supplied the URL after db.py already bound the engine at
        # import (the cli entry point pre-loads it, but a direct main() call
        # lands here) — rebind, mirroring the --db-url path, instead of
        # silently serving against the default SQLite.
        from .db import configure

        configure(os.environ["PAINT_REGISTRY_URL"])

    # Live-reload tokens on SIGHUP (Unix) so `picasso-registry token` changes
    # apply without downtime. With reload=False uvicorn serves the already-
    # imported module-level ``app`` in this process, so the handler reaches
    # it. Installed only when auth is armed: a token-free dev run keeps
    # SIGHUP's default terminate-on-hangup semantics (no orphan on a dropped
    # SSH session).
    if auth_cfg.enabled:
        install_auth_reload(
            app,
            env_file,
            host=args.host,
            prefer_env=tokens_from_process_env,
        )

    import uvicorn

    uvicorn.run(
        "picasso_registry.app:app",
        host=args.host,
        port=port,
        reload=args.reload,
    )

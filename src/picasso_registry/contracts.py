"""Shared cross-repo data contracts (S0B-2) — the frozen shapes the
DNA-PAINT stack agrees on so the parallel work orders converge.

This module is the *importable* half of the contract; the human-readable
freeze doc is ``CONTRACTS.md`` at the repo root (read that first). Four
contracts are published here:

1. **Metric vector** (:data:`MetricVector`) — the typed metric columns
   (groups A–D) plus a free-form ``extra`` passthrough. This is a direct
   reuse of the registry's :class:`~picasso_registry.schemas.Metrics`
   wire model (no second copy to drift), re-exported under a stack-facing
   name. picasso-workflow computes one of these per analysis run and POSTs
   it to ``/metrics``.

2. **Workflow YAML** (:class:`Workflow` / :class:`WorkflowStep`) — the
   ordered ``[{module, parameters}]`` shape that picasso-workflow's
   ``validate_workflow`` + ``MODULE_REGISTRY`` accept. The models here
   freeze the *shape* only; the *semantics* (which module names exist,
   capability flow, ordering, scope) stay owned by picasso-workflow's
   ``validate_workflow`` — see :data:`WORKFLOW_VALIDATOR_REFERENCE`.

3. **``localize_frames`` signature** (:class:`LocalizeFrames`) — the
   GUI-free localization entry point
   ``picasso.localize.localize_frames(frames, info, params) -> locs``.
   This function is *planned* (built in WP-2 / Iteration-1); the Protocol
   here freezes the call shape its callers may assume.

4. **ModuleSpec** — already implemented in picasso-workflow; not rebuilt
   here. :data:`MODULESPEC_REFERENCE` points at the canonical source.

Nothing in this module is wired into the FastAPI app, so it does not
affect ``openapi.json``.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from pydantic import BaseModel, Field, RootModel

from .schemas import Metrics

__all__ = [
    "MetricVector",
    "TYPED_METRIC_FIELDS",
    "WorkflowStep",
    "Workflow",
    "WORKFLOW_VALIDATOR_REFERENCE",
    "LocalizeFrames",
    "MODULESPEC_REFERENCE",
]


# ── 1. Metric vector ─────────────────────────────────────────────────────
# Reuse the registry wire model verbatim — a metric vector *is* a Metrics
# row. Aliasing (not subclassing/copying) guarantees the contract can never
# drift from what the registry actually accepts at ``POST /metrics``.
MetricVector = Metrics

# The typed metric columns (groups A–D), derived from the model so this list
# self-maintains as the schema grows. Excludes the join key / bookkeeping
# fields and the free-form ``extra`` passthrough.
_NON_METRIC_FIELDS = frozenset({"id", "analysis_run_id", "scope", "extra"})
TYPED_METRIC_FIELDS: tuple[str, ...] = tuple(
    name for name in Metrics.model_fields if name not in _NON_METRIC_FIELDS
)


# ── 2. Workflow YAML ─────────────────────────────────────────────────────


class WorkflowStep(BaseModel):
    """One step of a workflow: a module name + its parameter dict.

    On disk (YAML) a step is a mapping ``{module: <name>, parameters:
    {...}}``. ``parameters`` defaults to empty so a bare ``{module: name}``
    is valid. This validates the *shape* only — whether ``module`` names a
    real, correctly-ordered module is picasso-workflow's job (see
    :data:`WORKFLOW_VALIDATOR_REFERENCE`).
    """

    module: str = Field(min_length=1)
    parameters: dict = Field(default_factory=dict)


class Workflow(RootModel[list[WorkflowStep]]):
    """An ordered list of :class:`WorkflowStep` — the workflow-YAML shape.

    A ``RootModel`` so it round-trips as a bare YAML/JSON list (``[{module,
    parameters}, ...]``), exactly the on-disk form, rather than nesting it
    under a ``steps:`` key.
    """

    @classmethod
    def from_steps(cls, steps) -> "Workflow":
        """Normalize the forms picasso-workflow's ``validate_workflow``
        accepts into the canonical ``{module, parameters}`` shape:

        * ``("module_name", {params})`` — the runner's native tuple
        * ``"module_name"`` — a bare string (no parameters)
        * ``{"module": name, "parameters": {...}}`` — the YAML mapping
        * ``{"name": name, ...}`` — ``name`` accepted as a ``module`` alias

        Malformed steps raise a clear ``ValueError``/``TypeError`` naming
        the offending index rather than leaking a low-level crash. An
        absent or empty ``parameters`` (including a YAML ``parameters:``
        that parses to ``None``) means "no parameters". Module resolution
        uses the same falsy fallback as picasso-workflow's ``_step_name``
        (``module or name``), so the two agree on the same input.
        """
        norm: list[dict] = []
        for i, step in enumerate(steps):
            if isinstance(step, str):
                module, params = step, {}
            elif isinstance(step, (tuple, list)):
                if not step:
                    raise ValueError(f"workflow step {i} is empty")
                module = step[0]
                params = step[1] if len(step) > 1 else {}
            elif isinstance(step, dict):
                # Falsy fallback (module or name), matching picasso-
                # workflow's canonical _step_name resolution.
                module = step.get("module") or step.get("name")
                params = step.get("parameters")
            else:
                raise TypeError(
                    f"workflow step {i} is not a str/tuple/mapping: "
                    f"{step!r}"
                )
            if not module:
                raise ValueError(
                    f"workflow step {i} is missing a 'module'/'name': "
                    f"{step!r}"
                )
            params = {} if params is None else params
            if not isinstance(params, dict):
                raise TypeError(
                    f"workflow step {i} parameters must be a mapping, "
                    f"got {type(params).__name__}"
                )
            norm.append({"module": module, "parameters": params})
        return cls.model_validate(norm)


# Canonical semantic validator for workflows — lives in picasso-workflow,
# not here. This module freezes the shape; that function owns membership,
# capability flow, ordering and scope against ``MODULE_REGISTRY``.
WORKFLOW_VALIDATOR_REFERENCE = (
    "picasso_workflow.modulespec.validate_workflow "
    "(+ MODULE_REGISTRY) — ../picasso-workflow/picasso_workflow/"
    "modulespec.py"
)


# ── 3. localize_frames signature ─────────────────────────────────────────


@runtime_checkable
class LocalizeFrames(Protocol):
    """The GUI-free localization entry point contract.

    Target signature (built in WP-2 / Iteration-1; not yet in picasso)::

        picasso.localize.localize_frames(frames, info, params) -> locs

    * ``frames`` — an in-memory frame stack / iterator (3D array-like,
      ``(n_frames, height, width)``).
    * ``info`` — the picasso info list-of-dicts (movie metadata + camera
      info: ``Baseline`` / ``Sensitivity`` / ``Gain`` / ``Pixelsize``).
    * ``params`` — localization parameters (at least ``Min. Net Gradient``
      and ``Box Size``).
    * returns ``locs`` — the localization table as a **pandas DataFrame**
      (numpy recarrays are legacy; this stack's picasso returns DataFrames,
      so the contract standardizes on that). Columns include ``frame, x, y,
      photons, sx, sy, bg, lpx, lpy, net_gradient`` (+ ``z``/``lpz`` for 3D).

    Contract obligations on the implementation (WP-2):

    * **Absolute frame indices** — ``frame`` values must be absolute and
      contiguous across successive batches so concatenating per-batch
      outputs yields one growing table (do not restart at 0 per batch).
    * **GUI-free** — importable and runnable without a display; no Qt/GUI
      imports on the call path. (``picasso.localize`` is already
      GUI-free; it wraps the existing ``identify`` + ``fit2D`` path.)
    * **Parity** — output matches the existing GUI ``fit2D`` path on the
      same movie.

    ``runtime_checkable``, so ``isinstance(fn, LocalizeFrames)`` confirms
    ``fn`` is callable; the argument shape is documented, not enforced.
    """

    def __call__(self, frames, info, params): ...


# ── 4. ModuleSpec (link only) ────────────────────────────────────────────
# Already implemented in picasso-workflow — the canonical module-annotation
# layer the recommender consumes. Not rebuilt here.
MODULESPEC_REFERENCE = (
    "picasso_workflow.modulespec.ModuleSpec — "
    "../picasso-workflow/picasso_workflow/modulespec.py"
)

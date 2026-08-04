# Shared data contracts (S0B-2)

The **frozen cross-repo data shapes** for the DNA-PAINT full-automation
stack. S0B-2 publishes these so the parallel work orders (WP-1…WP-16) can
build against a stable interface instead of guessing at each other's shapes.
This document is the human-facing source of truth; the importable half lives
in [`picasso_registry.contracts`](src/picasso_registry/contracts.py).

**Freeze policy.** These are contracts, not conveniences: a change to any
shape below is a cross-repo contract change — its own work order that updates
the owning repo, the registry client, and every dependent together, never a
silent edit. Each contract names the **single repo that owns it**; this repo
(picasso-registry) hosts the freeze doc and owns contracts 1.

| # | Contract | Owner | Importable as |
|---|----------|-------|---------------|
| 1 | Metric vector | **picasso-registry** | `contracts.MetricVector` (= `schemas.Metrics`) |
| 2 | Workflow YAML | **picasso-workflow** (`validate_workflow`) | `contracts.Workflow` / `WorkflowStep` (shape) |
| 3 | `localize_frames` signature | **picasso** (planned, WP-2) | `contracts.LocalizeFrames` (Protocol) |
| 4 | `ModuleSpec` | **picasso-workflow** (implemented) | link only — `contracts.MODULESPEC_REFERENCE` |

```python
from picasso_registry.contracts import (
    MetricVector, TYPED_METRIC_FIELDS,
    Workflow, WorkflowStep, WORKFLOW_VALIDATOR_REFERENCE,
    LocalizeFrames, MODULESPEC_REFERENCE,
)
```

---

## 1. Metric vector — `MetricVector`

The typed metric columns (design-doc Part VI groups **A–D**) plus a free-form
`extra` passthrough. **`MetricVector` is the registry's
[`schemas.Metrics`](src/picasso_registry/schemas.py) wire model, reused
verbatim** (`MetricVector = Metrics`) — there is deliberately no second copy
to drift from what `POST /metrics` actually accepts.

- **Required:** `analysis_run_id` — every metric vector joins to an
  `analysis_run` (the `run_id` join invariant).
- **Typed columns** (`TYPED_METRIC_FIELDS`), by group:
  - **A — localization quality:** `n_locs`, `nena_nm`, `frc_nm`, `decorr_nm`,
    `photons_median`, `lp_x_nm`/`lp_y_nm`/`lp_z_nm`, `psf_sigma_x`/`psf_sigma_y`,
    `psf_ellipticity`, `net_gradient_median`, `spots_per_frame`,
    `usable_frame_frac`.
  - **B — drift & stability:** `drift_nm`, `drift_residual_nm`,
    `drift_rate_nm_min`, `z_focus_residual_nm`.
  - **C — kinetics / counting / damage:** `sbr`, `background`, `dark_time_s`,
    `bright_time_s`, `k_on`, `k_off`, `binding_freq_hz`, `events_per_site`,
    `damage_decay_rate`, `duty_cycle`, `density_locs_um2`, `qpaint_count`,
    `labeling_efficiency`.
  - **D — downstream structure / biology:** `n_clusters`, `mean_cluster_size`,
    `nnd_median_nm`, `spinna_oligomer_fractions`, `spinna_fit_quality`,
    `g5m_n_molecules`, `g5m_false_pos_est`, `registration_error_nm`.
- **`extra` passthrough:** the model sets `extra="allow"`, so a **novel,
  not-yet-typed metric** may be sent as a top-level key and is preserved in
  the JSON `extra` column — no schema change to log a new metric. Promote a
  metric to a typed column (a contract change) once it stabilizes.

```python
from picasso_registry.contracts import MetricVector

mv = MetricVector(analysis_run_id="run1", nena_nm=6.2, my_new_metric=0.9)
mv.model_dump()["my_new_metric"]  # 0.9 — rode through via extra
```

The authoritative serialization is the OpenAPI schema (`openapi.json`,
`#/components/schemas/Metrics`), regenerated with
`python -m picasso_registry.export_openapi`.

---

## 2. Workflow YAML — ordered `[{module, parameters}]`

A workflow is an **ordered list of steps**, each a mapping of a module name
to its parameter dict. On disk (YAML):

```yaml
- module: load_dataset_movie
  parameters: {}
- module: identify
  parameters: {box: 7}
- module: localize
  parameters: {}
- module: undrift_rcc
  parameters: {}
- module: save_single_dataset
  parameters: {}
```

**Ownership / validation split.** `contracts.Workflow` (a pydantic
`RootModel[list[WorkflowStep]]`) freezes the **shape** and round-trips the
bare list. It does **not** validate semantics. The **semantic authority** is
picasso-workflow's
[`validate_workflow(steps, scope, registry=None)`](../picasso-workflow/picasso_workflow/modulespec.py)
against `MODULE_REGISTRY` — it checks that each `module` is a registered
`ModuleSpec`, that the step is valid in the requested **scope**
(`single` / `aggregation`), that each module's `requires` capabilities are
provided by earlier steps, and that `after` ordering holds. It returns a list
of human-readable error strings (empty ⇒ valid); it does not raise.

**Accepted step forms.** picasso-workflow's runner and `validate_workflow`
accept three equivalent step forms; `Workflow.from_steps(...)` normalizes all
of them into the canonical `{module, parameters}` mapping:

| Form | Example |
|------|---------|
| native tuple (runner) | `("identify", {"box": 7})` |
| bare string (no params) | `"identify"` |
| mapping (`module` or `name` key) | `{"module": "identify", "parameters": {...}}` |

```python
from picasso_registry.contracts import Workflow

wf = Workflow.from_steps([("load_dataset_movie", {}), "identify"])
wf.model_dump()  # [{'module': 'load_dataset_movie', 'parameters': {}}, ...]
# then hand wf to picasso-workflow's validate_workflow for the real check.
```

The registry stores a workflow as text on `analysis_run.workflow_yaml` and its
resolved params on `analysis_run.parameters`.

---

## 3. `localize_frames` signature — GUI-free localization

The frozen call shape for the headless localization entry point that Phase-1
leaf work (WP-2 / Iteration-1) will add to **picasso**:

```python
picasso.localize.localize_frames(frames, info, params) -> locs
```

> **Status: planned.** This function does **not exist in picasso yet** — the
> Protocol here freezes the interface callers may build against now. WP-2
> implements it by wrapping the existing (already GUI-free) `identify` +
> `fit2D` path in [`picasso/picasso/localize.py`](../picasso/picasso/localize.py).

- **`frames`** — in-memory frame stack / iterator, 3D array-like
  `(n_frames, height, width)`.
- **`info`** — picasso info list-of-dicts: movie metadata + **camera info**
  (`Baseline`, `Sensitivity`, `Gain`, `Pixelsize`).
- **`params`** — localization parameters, at least `Min. Net Gradient` and
  `Box Size`.
- **`locs`** (return) — the localization table as a **pandas DataFrame**,
  columns `frame, x, y, photons, sx, sy, bg, lpx, lpy, net_gradient` (+
  `z`/`lpz` for 3D).

**Return type: pandas DataFrame (decided).** The brief and design-doc Part VI
say "recarray", but numpy recarrays are legacy — the picasso version this stack
uses returns a **pandas DataFrame** (same columns), so the contract
standardizes on that. Persisting to `*_locs.hdf5` is a separate concern owned
by picasso's I/O layer, not this signature.

**Implementation obligations (WP-2):**
- **Absolute, contiguous `frame` indices across batches** — successive batches
  concatenate into one growing table (do not restart at 0 per batch).
- **GUI-free** — importable and runnable with no display; no Qt/GUI imports on
  the call path.
- **Parity** — output equals the existing GUI `fit2D` path on the same movie.

`contracts.LocalizeFrames` is a `runtime_checkable` Protocol: `isinstance(fn,
LocalizeFrames)` confirms `fn` is callable; the argument shape is documented,
not enforced.

---

## 4. `ModuleSpec` — link only (no build)

The module-annotation layer is **already implemented** in picasso-workflow and
is the canonical reference; S0B-2 does not rebuild it. It is the frozen
dataclass describing each analysis module (its `requires`/`provides`
capabilities, `scopes`, ordering `after`, picasso `relation`, etc.) that
`validate_workflow` and the recommender consume.

- **Canonical source:**
  [`../picasso-workflow/picasso_workflow/modulespec.py`](../picasso-workflow/picasso_workflow/modulespec.py)
  — `@dataclass(frozen=True) class ModuleSpec` and the `MODULE_REGISTRY` it
  populates.
- **Reference doc:**
  [`../../planning/picasso-workflow_Module-Annotations_Reference.md`](../../planning/picasso-workflow_Module-Annotations_Reference.md).
- Importable pointer: `contracts.MODULESPEC_REFERENCE`.

---

## Dependent briefs referencing these contracts

- **WP-2 / Iteration-1** — implements contract **3** (`localize_frames`).
- **S0B-1b** — extends the registry cohort surface; keeps `MetricVector`
  (`extra="allow"`) — contract **1**.
- **WP-3** — registry MVP hardening; client writes `MetricVector`s — contract **1**.
- **picasso-workflow recommender work** — consumes `ModuleSpec` /
  `MODULE_REGISTRY` and emits contract **2** workflows.

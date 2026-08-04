"""Pydantic models = the wire contract; one per table, mirroring models.py.

Each schema doubles as the request body (``id`` optional — the server mints a
ULID when omitted) and the response model (``from_attributes`` reads the ORM
row). ``Metrics`` sets ``extra="allow"`` so novel, not-yet-typed metrics ride
through into the JSON ``extra`` column without a schema change.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ── A2 descriptor controlled vocabularies (register C12, 2026-08-04) ──────
# Closed axis enums frozen by A2. Leaf vocabularies that A2 left open (cell
# lines, protein/glycan target *names*, organism) stay free-form strings —
# only these three axes and the cohort match-depth selector are closed sets.
Modality = Literal["TIRF", "HILO", "spinning_disk", "light_sheet"]  # axis 3
DimensionalityValue = Literal["2D", "3D"]  # axis 3
TargetClass = Literal[  # axis 2 target class (names stay open)
    "intracellular_protein", "membrane_protein", "glycan"
]


class _ORM(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str | None = None


class _ORMExtra(_ORM):
    """Base for tables carrying a JSON ``extra`` column.

    ``extra="allow"`` lets unknown, not-yet-typed top-level fields ride
    through the request instead of being silently dropped; the persistence
    layer folds them into the ``extra`` column so provenance is preserved in
    this append-only store.
    """

    model_config = ConfigDict(from_attributes=True, extra="allow")


# ── Setup ────────────────────────────────────────────────────────────────


class SampleTaxonomy(_ORM):
    name: str | None = None
    parent_id: str | None = None
    path: str | None = None
    ontology_ref: str | None = None
    defaults: dict | None = None
    expected_metrics: dict | None = None
    qc_rules: dict | None = None
    version: str | None = None


class Experiment(_ORMExtra):
    created_at: datetime | None = None
    operator: str | None = None
    sample_taxon_id: str | None = None
    organism: str | None = None
    fixation: str | None = None
    mounting: str | None = None
    # A2 axis 3 (single-valued per experiment — a run doesn't mix modalities).
    acquisition_modality: Modality | None = None
    dimensionality: str | None = None
    buffer: str | None = None
    notes: str | None = None
    extra: dict | None = None


class SampleTag(_ORM):
    experiment_id: str | None = None
    tag: str | None = None


class TargetChannel(_ORM):
    experiment_id: str | None = None
    target: str | None = None  # axis 2 target name (open vocabulary)
    target_class: TargetClass | None = None  # A2 axis 2 (closed enum)
    binder: str | None = None
    binder_lot: str | None = None
    dye: str | None = None  # = fluorophore (per-target illumination bundle)
    dye_lot: str | None = None
    imager_seq: str | None = None
    imager_batch: str | None = None
    imager_conc_nM: float | None = None
    # A2: exposure & laser power are per-target (per-channel), not run-level.
    exposure_ms: float | None = None
    laser_power_mW: float | None = None
    round_index: int | None = None


class ReagentProvenance(_ORMExtra):
    experiment_id: str | None = None
    prep_datetime: datetime | None = None
    hours_since_labeling: float | None = None
    docking_design_id: str | None = None
    extra: dict | None = None


# ── Acquisition ──────────────────────────────────────────────────────────


class AcquisitionRun(_ORMExtra):
    # the run_id minted by PycroFlow — required, non-empty, never
    # server-minted (min_length rejects "" before it can mint a ULID).
    id: str = Field(min_length=1)
    experiment_id: str | None = None
    microscope_id: str | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    status: str | None = None
    pycroflow_version: str | None = None
    mm_version: str | None = None
    config_yaml: str | None = None
    raw_data_path: str | None = None
    raw_retained: bool | None = None
    extra: dict | None = None


class Fov(_ORMExtra):
    acquisition_run_id: str | None = None
    target_channel_id: str | None = None
    pos_x: float | None = None
    pos_y: float | None = None
    pos_z: float | None = None
    tirf_angle_deg: float | None = None
    exposure_ms: float | None = None
    frame_count: int | None = None
    frame_rate_hz: float | None = None
    camera_temp_c: float | None = None
    camera_gain: float | None = None
    roi: dict | None = None
    extra: dict | None = None


class Illumination(_ORMExtra):
    fov_id: str | None = None
    laser_nm: int | None = None
    target_power_mW: float | None = None
    measured_power_mW: float | None = None
    monet_calibration_id: str | None = None
    extra: dict | None = None


class Environment(_ORMExtra):
    fov_id: str | None = None
    recorded_at: datetime | None = None
    room_temp_c: float | None = None
    stage_temp_c: float | None = None
    humidity_pct: float | None = None
    objective: str | None = None
    na: float | None = None
    immersion_lot: str | None = None
    extra: dict | None = None


class FluidicsRound(_ORMExtra):
    acquisition_run_id: str | None = None
    round_index: int | None = None
    duration_s: float | None = None
    volume_uL: float | None = None
    flow_rate_uL_s: float | None = None
    incubation_s: float | None = None
    crosstalk_residual: float | None = None
    sensor_events: dict | None = None
    extra: dict | None = None


class SampleMorphology(_ORMExtra):
    fov_id: str | None = None
    confluence: float | None = None
    n_cells: int | None = None
    marker_intensity: float | None = None
    autofluorescence: float | None = None
    usable_fraction: float | None = None
    seg_features: dict | None = None
    extra: dict | None = None


# ── Analysis ─────────────────────────────────────────────────────────────


class AnalysisRun(_ORMExtra):
    fov_id: str | None = None
    acquisition_run_id: str | None = None
    kind: str | None = None
    compute_location: str | None = None
    slurm_job_id: str | None = None
    status: str | None = None
    picasso_version: str | None = None
    workflow_yaml: str | None = None
    parameters: dict | None = None
    recommender_version: str | None = None
    recommended_spec: dict | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    extra: dict | None = None


class Metrics(_ORMExtra):
    # Required: every metrics row joins to an analysis_run (run_id invariant).
    analysis_run_id: str
    scope: str | None = None
    # A — localization quality
    n_locs: int | None = None
    nena_nm: float | None = None
    frc_nm: float | None = None
    decorr_nm: float | None = None
    photons_median: float | None = None
    lp_x_nm: float | None = None
    lp_y_nm: float | None = None
    lp_z_nm: float | None = None
    psf_sigma_x: float | None = None
    psf_sigma_y: float | None = None
    psf_ellipticity: float | None = None
    net_gradient_median: float | None = None
    spots_per_frame: float | None = None
    usable_frame_frac: float | None = None
    # B — drift & stability
    drift_nm: float | None = None
    drift_residual_nm: float | None = None
    drift_rate_nm_min: float | None = None
    z_focus_residual_nm: float | None = None
    # C — kinetics / counting / damage
    sbr: float | None = None
    background: float | None = None
    dark_time_s: float | None = None
    bright_time_s: float | None = None
    k_on: float | None = None
    k_off: float | None = None
    binding_freq_hz: float | None = None
    events_per_site: float | None = None
    damage_decay_rate: float | None = None
    duty_cycle: float | None = None
    density_locs_um2: float | None = None
    qpaint_count: float | None = None
    labeling_efficiency: float | None = None
    # D — downstream structure / biology
    n_clusters: int | None = None
    mean_cluster_size: float | None = None
    nnd_median_nm: float | None = None
    spinna_oligomer_fractions: dict | None = None
    spinna_fit_quality: float | None = None
    g5m_n_molecules: int | None = None
    g5m_false_pos_est: float | None = None
    registration_error_nm: float | None = None
    extra: dict | None = None


class ResourceUsage(_ORMExtra):
    analysis_run_id: str | None = None
    module: str | None = None
    compute_seconds: float | None = None
    peak_ram_mb: float | None = None
    gpu_util_pct: float | None = None
    raw_bytes: int | None = None
    locs_bytes: int | None = None
    slurm_walltime_s: float | None = None
    slurm_cores: int | None = None
    slurm_mem_mb: int | None = None
    transfer_seconds: float | None = None
    extra: dict | None = None


# ── Feedback / labels & artifacts ────────────────────────────────────────


class Qc(_ORM):
    analysis_run_id: str | None = None
    passed: bool | None = None
    decided_by: str | None = None
    reason: str | None = None
    decided_at: datetime | None = None


class Feedback(_ORM):
    acquisition_run_id: str | None = None
    fov_id: str | None = None
    analysis_run_id: str | None = None
    operator_decision: str | None = None
    recommender_edit: dict | None = None
    iteration_count: int | None = None
    used_in_final: bool | None = None
    failure_mode: str | None = None
    note: str | None = None


class Artifact(_ORM):
    analysis_run_id: str | None = None
    kind: str | None = None
    uri: str | None = None
    checksum: str | None = None
    size_bytes: int | None = None


class Interpretation(_ORM):
    acquisition_run_id: str | None = None
    analysis_run_id: str | None = None
    stage1_summary: str | None = None
    stage1_stats: dict | None = None
    stage2_flags: dict | None = None
    stage2_summary: str | None = None
    stage3_summary: str | None = None
    citations: dict | None = None
    suggested_next: dict | None = None
    confidence: float | None = None
    model_version: str | None = None
    prompt_version: str | None = None
    corpus_version: str | None = None
    operator_feedback: str | None = None
    created_at: datetime | None = None


# ── Derived query results & bulk ingest ──────────────────────────────────


class CohortItem(BaseModel):
    """One ranked cohort member (closest tree-distance first)."""

    acquisition_run_id: str
    experiment_id: str
    taxon_id: str
    taxon_name: str | None = None
    tree_distance: int


class NodeDefaults(BaseModel):
    """The inherited cascade resolved for a taxonomy node."""

    taxon_id: str
    defaults: dict = {}
    expected_metrics: dict = {}
    qc_rules: dict = {}


class BulkIngest(BaseModel):
    """A batch of rows across tables, inserted in one transaction (backfill).

    Taxonomy is applied first so child paths resolve; the rest follow the FK
    chain. Any omitted list is skipped.
    """

    sample_taxonomy: list[SampleTaxonomy] = []
    experiment: list[Experiment] = []
    sample_tag: list[SampleTag] = []
    target_channel: list[TargetChannel] = []
    reagent_provenance: list[ReagentProvenance] = []
    acquisition_run: list[AcquisitionRun] = []
    fov: list[Fov] = []
    illumination: list[Illumination] = []
    environment: list[Environment] = []
    fluidics_round: list[FluidicsRound] = []
    sample_morphology: list[SampleMorphology] = []
    analysis_run: list[AnalysisRun] = []
    metrics: list[Metrics] = []
    resource_usage: list[ResourceUsage] = []
    qc: list[Qc] = []
    feedback: list[Feedback] = []
    artifact: list[Artifact] = []
    interpretation: list[Interpretation] = []


class BulkResult(BaseModel):
    counts: dict[str, int]
    total: int

"""SQLAlchemy models — the provenance contract everything joins on.

Append-only: rows are inserted, never mutated. Everything reconstructs a run's
full lineage from ``acquisition_run.id`` (the ``run_id`` minted by PycroFlow)
through the FK chain (fov -> analysis_run -> metrics, etc.). The tables mirror
the design-doc Part VI schema (groups A-J) plus the Part VII ``interpretation``
table; typed columns cover what we query/optimize on and a JSON ``extra`` column
absorbs the evolving rest so the schema doesn't churn.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
)
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ── Setup ────────────────────────────────────────────────────────────────


class SampleTaxonomy(Base):
    """Faceted, hierarchical sample descriptor (adjacency list + path)."""

    __tablename__ = "sample_taxonomy"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str | None] = mapped_column(String)
    parent_id: Mapped[str | None] = mapped_column(
        ForeignKey("sample_taxonomy.id")
    )
    path: Mapped[str | None] = mapped_column(String)  # materialized path
    ontology_ref: Mapped[str | None] = mapped_column(String)
    defaults: Mapped[dict | None] = mapped_column(JSON)
    expected_metrics: Mapped[dict | None] = mapped_column(JSON)
    qc_rules: Mapped[dict | None] = mapped_column(JSON)
    version: Mapped[str | None] = mapped_column(String)


class Experiment(Base):
    __tablename__ = "experiment"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )
    operator: Mapped[str | None] = mapped_column(String)
    sample_taxon_id: Mapped[str | None] = mapped_column(
        ForeignKey("sample_taxonomy.id")
    )
    organism: Mapped[str | None] = mapped_column(String)
    fixation: Mapped[str | None] = mapped_column(String)
    mounting: Mapped[str | None] = mapped_column(String)
    dimensionality: Mapped[str | None] = mapped_column(String)
    buffer: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict | None] = mapped_column(JSON)


class SampleTag(Base):
    """Free orthogonal attributes (many-to-many with experiment)."""

    __tablename__ = "sample_tag"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiment.id")
    )
    tag: Mapped[str | None] = mapped_column(String)


class TargetChannel(Base):
    __tablename__ = "target_channel"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiment.id")
    )
    target: Mapped[str | None] = mapped_column(String)
    binder: Mapped[str | None] = mapped_column(String)
    binder_lot: Mapped[str | None] = mapped_column(String)
    dye: Mapped[str | None] = mapped_column(String)
    dye_lot: Mapped[str | None] = mapped_column(String)
    imager_seq: Mapped[str | None] = mapped_column(String)
    imager_batch: Mapped[str | None] = mapped_column(String)
    imager_conc_nM: Mapped[float | None] = mapped_column(Float)
    round_index: Mapped[int | None] = mapped_column(Integer)


class ReagentProvenance(Base):
    """Group H — reagents & sample provenance."""

    __tablename__ = "reagent_provenance"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiment.id")
    )
    prep_datetime: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    hours_since_labeling: Mapped[float | None] = mapped_column(Float)
    docking_design_id: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict | None] = mapped_column(JSON)


# ── Acquisition ──────────────────────────────────────────────────────────


class AcquisitionRun(Base):
    __tablename__ = "acquisition_run"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # the run_id
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiment.id")
    )
    microscope_id: Mapped[str | None] = mapped_column(String)
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    status: Mapped[str | None] = mapped_column(String)
    pycroflow_version: Mapped[str | None] = mapped_column(String)
    mm_version: Mapped[str | None] = mapped_column(String)
    config_yaml: Mapped[str | None] = mapped_column(String)
    raw_data_path: Mapped[str | None] = mapped_column(String)
    raw_retained: Mapped[bool | None] = mapped_column(Boolean)
    extra: Mapped[dict | None] = mapped_column(JSON)


class Fov(Base):
    __tablename__ = "fov"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    acquisition_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("acquisition_run.id")
    )
    target_channel_id: Mapped[str | None] = mapped_column(
        ForeignKey("target_channel.id")
    )
    pos_x: Mapped[float | None] = mapped_column(Float)
    pos_y: Mapped[float | None] = mapped_column(Float)
    pos_z: Mapped[float | None] = mapped_column(Float)
    tirf_angle_deg: Mapped[float | None] = mapped_column(Float)
    exposure_ms: Mapped[float | None] = mapped_column(Float)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    frame_rate_hz: Mapped[float | None] = mapped_column(Float)
    camera_temp_c: Mapped[float | None] = mapped_column(Float)
    camera_gain: Mapped[float | None] = mapped_column(Float)
    roi: Mapped[dict | None] = mapped_column(JSON)
    extra: Mapped[dict | None] = mapped_column(JSON)


class Illumination(Base):
    """Links to monet (references a monet calibration, does not duplicate)."""

    __tablename__ = "illumination"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    fov_id: Mapped[str | None] = mapped_column(ForeignKey("fov.id"))
    laser_nm: Mapped[int | None] = mapped_column(Integer)
    target_power_mW: Mapped[float | None] = mapped_column(Float)
    measured_power_mW: Mapped[float | None] = mapped_column(Float)
    monet_calibration_id: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict | None] = mapped_column(JSON)


class Environment(Base):
    """Group F — environment & hardware context."""

    __tablename__ = "environment"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    fov_id: Mapped[str | None] = mapped_column(ForeignKey("fov.id"))
    recorded_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    room_temp_c: Mapped[float | None] = mapped_column(Float)
    stage_temp_c: Mapped[float | None] = mapped_column(Float)
    humidity_pct: Mapped[float | None] = mapped_column(Float)
    objective: Mapped[str | None] = mapped_column(String)
    na: Mapped[float | None] = mapped_column(Float)
    immersion_lot: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict | None] = mapped_column(JSON)


class FluidicsRound(Base):
    """Group E — Exchange/SUM multiplexing round."""

    __tablename__ = "fluidics_round"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    acquisition_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("acquisition_run.id")
    )
    round_index: Mapped[int | None] = mapped_column(Integer)
    duration_s: Mapped[float | None] = mapped_column(Float)
    volume_uL: Mapped[float | None] = mapped_column(Float)
    flow_rate_uL_s: Mapped[float | None] = mapped_column(Float)
    incubation_s: Mapped[float | None] = mapped_column(Float)
    crosstalk_residual: Mapped[float | None] = mapped_column(Float)
    sensor_events: Mapped[dict | None] = mapped_column(JSON)
    extra: Mapped[dict | None] = mapped_column(JSON)


class SampleMorphology(Base):
    """Group G — sample & morphology (for cell-finding)."""

    __tablename__ = "sample_morphology"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    fov_id: Mapped[str | None] = mapped_column(ForeignKey("fov.id"))
    confluence: Mapped[float | None] = mapped_column(Float)
    n_cells: Mapped[int | None] = mapped_column(Integer)
    marker_intensity: Mapped[float | None] = mapped_column(Float)
    autofluorescence: Mapped[float | None] = mapped_column(Float)
    usable_fraction: Mapped[float | None] = mapped_column(Float)
    seg_features: Mapped[dict | None] = mapped_column(JSON)
    extra: Mapped[dict | None] = mapped_column(JSON)


# ── Analysis ─────────────────────────────────────────────────────────────


class AnalysisRun(Base):
    __tablename__ = "analysis_run"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    fov_id: Mapped[str | None] = mapped_column(ForeignKey("fov.id"))
    acquisition_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("acquisition_run.id")
    )
    kind: Mapped[str | None] = mapped_column(String)  # live|preprocess|cluster
    compute_location: Mapped[str | None] = mapped_column(String)
    slurm_job_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    picasso_version: Mapped[str | None] = mapped_column(String)
    workflow_yaml: Mapped[str | None] = mapped_column(String)
    parameters: Mapped[dict | None] = mapped_column(JSON)
    recommender_version: Mapped[str | None] = mapped_column(String)
    recommended_spec: Mapped[dict | None] = mapped_column(JSON)  # #4 suggested
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )
    extra: Mapped[dict | None] = mapped_column(JSON)


class Metrics(Base):
    """Groups A-D — hybrid: typed columns + JSON ``extra`` for the rest."""

    __tablename__ = "metrics"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    scope: Mapped[str | None] = mapped_column(String)
    # A — localization quality
    n_locs: Mapped[int | None] = mapped_column(BigInteger)
    nena_nm: Mapped[float | None] = mapped_column(Float)
    frc_nm: Mapped[float | None] = mapped_column(Float)
    decorr_nm: Mapped[float | None] = mapped_column(Float)
    photons_median: Mapped[float | None] = mapped_column(Float)
    lp_x_nm: Mapped[float | None] = mapped_column(Float)
    lp_y_nm: Mapped[float | None] = mapped_column(Float)
    lp_z_nm: Mapped[float | None] = mapped_column(Float)
    psf_sigma_x: Mapped[float | None] = mapped_column(Float)
    psf_sigma_y: Mapped[float | None] = mapped_column(Float)
    psf_ellipticity: Mapped[float | None] = mapped_column(Float)
    net_gradient_median: Mapped[float | None] = mapped_column(Float)
    spots_per_frame: Mapped[float | None] = mapped_column(Float)
    usable_frame_frac: Mapped[float | None] = mapped_column(Float)
    # B — drift & stability
    drift_nm: Mapped[float | None] = mapped_column(Float)
    drift_residual_nm: Mapped[float | None] = mapped_column(Float)
    drift_rate_nm_min: Mapped[float | None] = mapped_column(Float)
    z_focus_residual_nm: Mapped[float | None] = mapped_column(Float)
    # C — kinetics / counting / damage
    sbr: Mapped[float | None] = mapped_column(Float)
    background: Mapped[float | None] = mapped_column(Float)
    dark_time_s: Mapped[float | None] = mapped_column(Float)
    bright_time_s: Mapped[float | None] = mapped_column(Float)
    k_on: Mapped[float | None] = mapped_column(Float)
    k_off: Mapped[float | None] = mapped_column(Float)
    binding_freq_hz: Mapped[float | None] = mapped_column(Float)
    events_per_site: Mapped[float | None] = mapped_column(Float)
    damage_decay_rate: Mapped[float | None] = mapped_column(Float)
    duty_cycle: Mapped[float | None] = mapped_column(Float)
    density_locs_um2: Mapped[float | None] = mapped_column(Float)
    qpaint_count: Mapped[float | None] = mapped_column(Float)
    labeling_efficiency: Mapped[float | None] = mapped_column(Float)
    # D — downstream structure / biology
    n_clusters: Mapped[int | None] = mapped_column(Integer)
    mean_cluster_size: Mapped[float | None] = mapped_column(Float)
    nnd_median_nm: Mapped[float | None] = mapped_column(Float)
    spinna_oligomer_fractions: Mapped[dict | None] = mapped_column(JSON)
    spinna_fit_quality: Mapped[float | None] = mapped_column(Float)
    g5m_n_molecules: Mapped[int | None] = mapped_column(Integer)
    g5m_false_pos_est: Mapped[float | None] = mapped_column(Float)
    registration_error_nm: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[dict | None] = mapped_column(JSON)  # evolving metrics


class ResourceUsage(Base):
    """Group I — compute & operational cost."""

    __tablename__ = "resource_usage"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    module: Mapped[str | None] = mapped_column(String)
    compute_seconds: Mapped[float | None] = mapped_column(Float)
    peak_ram_mb: Mapped[float | None] = mapped_column(Float)
    gpu_util_pct: Mapped[float | None] = mapped_column(Float)
    raw_bytes: Mapped[int | None] = mapped_column(BigInteger)
    locs_bytes: Mapped[int | None] = mapped_column(BigInteger)
    slurm_walltime_s: Mapped[float | None] = mapped_column(Float)
    slurm_cores: Mapped[int | None] = mapped_column(Integer)
    slurm_mem_mb: Mapped[int | None] = mapped_column(Integer)
    transfer_seconds: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[dict | None] = mapped_column(JSON)


# ── Feedback / labels & artifacts ────────────────────────────────────────


class Qc(Base):
    """Group J — QC gate + label."""

    __tablename__ = "qc"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    passed: Mapped[bool | None] = mapped_column(Boolean)
    decided_by: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String)
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True)
    )


class Feedback(Base):
    """Group J — ML labels (operator/recommender feedback)."""

    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    acquisition_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("acquisition_run.id")
    )
    fov_id: Mapped[str | None] = mapped_column(ForeignKey("fov.id"))
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    operator_decision: Mapped[str | None] = mapped_column(String)  # accept|rej
    recommender_edit: Mapped[dict | None] = mapped_column(JSON)
    iteration_count: Mapped[int | None] = mapped_column(Integer)
    used_in_final: Mapped[bool | None] = mapped_column(Boolean)
    failure_mode: Mapped[str | None] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(String)


class Artifact(Base):
    __tablename__ = "artifact"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    kind: Mapped[str | None] = mapped_column(
        String
    )  # locs_hdf5|render_png|...
    uri: Mapped[str | None] = mapped_column(String)
    checksum: Mapped[str | None] = mapped_column(String)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)


class Interpretation(Base):
    """Part VII — per-run interpretation (initiative #9)."""

    __tablename__ = "interpretation"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    acquisition_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("acquisition_run.id")
    )
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    stage1_summary: Mapped[str | None] = mapped_column(String)
    stage1_stats: Mapped[dict | None] = mapped_column(JSON)
    stage2_flags: Mapped[dict | None] = mapped_column(JSON)
    stage2_summary: Mapped[str | None] = mapped_column(String)
    stage3_summary: Mapped[str | None] = mapped_column(String)
    citations: Mapped[dict | None] = mapped_column(JSON)
    suggested_next: Mapped[dict | None] = mapped_column(JSON)
    confidence: Mapped[float | None] = mapped_column(Float)
    model_version: Mapped[str | None] = mapped_column(String)
    prompt_version: Mapped[str | None] = mapped_column(String)
    corpus_version: Mapped[str | None] = mapped_column(String)
    operator_feedback: Mapped[str | None] = mapped_column(String)
    created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), default=_utcnow
    )

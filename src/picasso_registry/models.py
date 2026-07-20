"""SQLAlchemy models. Append-only; joined by run_id (acquisition_run.id).

NOTE (work order S0B-1): this is the contract everything joins on. The core
tables are scaffolded here; complete the full set per the implementation plan
(target_channel, reagent_provenance, illumination, environment, fluidics_round,
sample_morphology, resource_usage) before Gate 0.
"""

from __future__ import annotations

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from .db import Base


class Experiment(Base):
    __tablename__ = "experiment"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[str | None] = mapped_column(String)
    operator: Mapped[str | None] = mapped_column(String)
    sample_taxon_id: Mapped[str | None] = mapped_column(
        ForeignKey("sample_taxonomy.id")
    )
    organism: Mapped[str | None] = mapped_column(String)
    fixation: Mapped[str | None] = mapped_column(String)
    mounting: Mapped[str | None] = mapped_column(String)
    notes: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict | None] = mapped_column(JSON)


class SampleTaxonomy(Base):
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


class AcquisitionRun(Base):
    __tablename__ = "acquisition_run"
    id: Mapped[str] = mapped_column(String, primary_key=True)  # the run_id
    experiment_id: Mapped[str | None] = mapped_column(
        ForeignKey("experiment.id")
    )
    microscope_id: Mapped[str | None] = mapped_column(String)
    status: Mapped[str | None] = mapped_column(String)
    pycroflow_version: Mapped[str | None] = mapped_column(String)
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
    pos_x: Mapped[float | None] = mapped_column(Float)
    pos_y: Mapped[float | None] = mapped_column(Float)
    pos_z: Mapped[float | None] = mapped_column(Float)
    tirf_angle_deg: Mapped[float | None] = mapped_column(Float)
    exposure_ms: Mapped[float | None] = mapped_column(Float)
    frame_count: Mapped[int | None] = mapped_column(Integer)
    extra: Mapped[dict | None] = mapped_column(JSON)


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
    parameters: Mapped[dict | None] = mapped_column(JSON)
    recommended_spec: Mapped[dict | None] = mapped_column(JSON)
    recommender_version: Mapped[str | None] = mapped_column(String)
    extra: Mapped[dict | None] = mapped_column(JSON)


class Metrics(Base):
    __tablename__ = "metrics"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    scope: Mapped[str | None] = mapped_column(String)
    # --- typed core (groups A-D); extend per the plan ---
    n_locs: Mapped[int | None] = mapped_column(Integer)
    nena_nm: Mapped[float | None] = mapped_column(Float)
    loc_precision_nm: Mapped[float | None] = mapped_column(Float)
    frc_nm: Mapped[float | None] = mapped_column(Float)
    drift_nm: Mapped[float | None] = mapped_column(Float)
    sbr: Mapped[float | None] = mapped_column(Float)
    background: Mapped[float | None] = mapped_column(Float)
    dark_time_s: Mapped[float | None] = mapped_column(Float)
    bright_time_s: Mapped[float | None] = mapped_column(Float)
    density_locs_um2: Mapped[float | None] = mapped_column(Float)
    extra: Mapped[dict | None] = mapped_column(JSON)  # evolving metrics


class Qc(Base):
    __tablename__ = "qc"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    passed: Mapped[bool | None] = mapped_column(Boolean)
    decided_by: Mapped[str | None] = mapped_column(String)
    reason: Mapped[str | None] = mapped_column(String)


class Feedback(Base):
    __tablename__ = "feedback"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    acquisition_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("acquisition_run.id")
    )
    operator_decision: Mapped[str | None] = mapped_column(String)
    recommender_edit: Mapped[dict | None] = mapped_column(JSON)
    used_in_final: Mapped[bool | None] = mapped_column(Boolean)
    failure_mode: Mapped[str | None] = mapped_column(String)


class Artifact(Base):
    __tablename__ = "artifact"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    analysis_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("analysis_run.id")
    )
    kind: Mapped[str | None] = mapped_column(String)
    uri: Mapped[str | None] = mapped_column(String)
    checksum: Mapped[str | None] = mapped_column(String)
    size_bytes: Mapped[int | None] = mapped_column(Integer)


class Interpretation(Base):
    __tablename__ = "interpretation"
    id: Mapped[str] = mapped_column(String, primary_key=True)
    acquisition_run_id: Mapped[str | None] = mapped_column(
        ForeignKey("acquisition_run.id")
    )
    stage1_stats: Mapped[dict | None] = mapped_column(JSON)
    stage2_flags: Mapped[dict | None] = mapped_column(JSON)
    stage3_summary: Mapped[str | None] = mapped_column(String)
    citations: Mapped[dict | None] = mapped_column(JSON)
    suggested_next: Mapped[dict | None] = mapped_column(JSON)
    model_version: Mapped[str | None] = mapped_column(String)
    prompt_version: Mapped[str | None] = mapped_column(String)
    corpus_version: Mapped[str | None] = mapped_column(String)

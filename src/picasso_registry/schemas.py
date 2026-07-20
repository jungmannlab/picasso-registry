"""Pydantic models = the wire contract. Extend to mirror models.py."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class MetricsIn(BaseModel):
    model_config = ConfigDict(extra="allow")  # JSON 'extra' metrics allowed
    analysis_run_id: str
    scope: str | None = None
    n_locs: int | None = None
    nena_nm: float | None = None
    loc_precision_nm: float | None = None
    frc_nm: float | None = None


class AcquisitionRunIn(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: str  # run_id (mint with ULID in PycroFlow)
    experiment_id: str | None = None
    microscope_id: str | None = None
    status: str | None = None

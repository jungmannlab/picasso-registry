"""Append-only persistence helpers.

Every writer path funnels through here so the append-only rule (insert, never
mutate) and the id-minting / ``extra``-absorption conventions live in one place.
A missing ``id`` is minted as a ULID; ``None`` values are dropped so column
defaults (e.g. ``created_at``) apply and absent features stay NULL.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ulid import ULID

from . import models
from .taxonomy import build_path

# WP-9 hand-off state machine: the legal transitions. Everything else 409s.
# ready --claim--> claimed --running--> running --done--> done
# claimed/running --failed--> failed (then optionally re-readied, new attempt).
HANDOFF_TRANSITIONS: dict[str, set[str]] = {
    "ready": {"claimed"},
    "claimed": {"running", "failed"},
    "running": {"done", "failed"},
    "done": set(),
    "failed": set(),
}


class UnknownParent(Exception):
    """A taxonomy node references a parent that does not (yet) exist."""

    def __init__(self, parent_id: str) -> None:
        super().__init__(f"unknown parent_id: {parent_id!r}")
        self.parent_id = parent_id


class Conflict(Exception):
    """An insert violated a uniqueness/integrity constraint.

    Most often a re-posted client-minted id (e.g. a retried acquisition_run
    after a lost response); surfaced as HTTP 409 rather than a 500.
    """


def new_id() -> str:
    """Mint a fresh, sortable ULID string."""
    return str(ULID())


def order_taxonomy_by_depth(rows: list) -> list:
    """Stable-sort taxonomy rows so in-batch parents precede their children.

    Depth counts only ancestors present in the same batch; nodes whose parent
    is already in the DB (or absent) sort as roots. Lets a bulk insert resolve
    each child's materialized path against an already-persisted parent
    regardless of the order the caller supplied.
    """
    by_id = {r.id: r for r in rows if getattr(r, "id", None) is not None}

    def depth(row) -> int:
        d, seen, cur = 0, set(), getattr(row, "parent_id", None)
        while cur in by_id and cur not in seen:
            seen.add(cur)
            d += 1
            cur = by_id[cur].parent_id
        return d

    return sorted(rows, key=depth)


def _columns(orm_cls: type) -> set[str]:
    return set(orm_cls.__table__.columns.keys())


def _row(orm_cls: type, data: dict) -> dict[str, Any]:
    """Column dict for ``orm_cls`` from ``data``.

    Keeps known, non-``None`` columns (so column defaults apply and absent
    features stay NULL). When the table has a JSON ``extra`` column, unknown
    top-level keys are folded into it rather than dropped, so novel,
    not-yet-typed fields are preserved in this append-only store. An explicit
    ``extra`` dict wins over a loose top-level key of the same name.
    """
    cols = _columns(orm_cls)
    known = {k: v for k, v in data.items() if k in cols and v is not None}
    if "extra" in cols:
        merged = {k: v for k, v in data.items() if k not in cols}
        merged.update(known.get("extra") or {})
        if merged:
            known["extra"] = merged
    return known


def _insert(session: Session, obj: Any, *, commit: bool) -> Any:
    """Add + flush ``obj``, mapping an integrity violation to ``Conflict``.

    On an ``IntegrityError`` (e.g. re-posting an already-stored client-minted
    id) the transaction is rolled back and a ``Conflict`` is raised, so the
    route returns a clean 409 instead of a 500 with a SQL stack trace.
    """
    session.add(obj)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise Conflict(f"{obj.__tablename__}: {exc.orig}") from exc
    if commit:
        session.commit()
    return obj


def persist(
    session: Session, orm_cls: type, data: dict, *, commit: bool = True
):
    """Insert one row of ``orm_cls`` from ``data``.

    Unknown keys are folded into the ``extra`` column when the table has one,
    otherwise ignored. A missing ``id`` is minted; tables whose id is
    client-supplied (e.g. acquisition_run) require a non-empty id at the
    schema layer, so a blank id is rejected before it reaches here.
    """
    row = _row(orm_cls, data)
    if not row.get("id"):
        row["id"] = new_id()
    return _insert(session, orm_cls(**row), commit=commit)


def persist_taxonomy(
    session: Session, orm_cls: type, data: dict, *, commit: bool = True
):
    """Insert a taxonomy node, computing its materialized ``path``.

    The ``path`` is always derived from the parent chain — a caller-supplied
    ``path`` is ignored so it can never contradict ``parent_id`` and corrupt
    the ancestry that cohort distance and the node-defaults cascade depend on
    (wrong forever in this append-only store). The parent must already be
    persisted (bulk-ingest orders taxonomy first and flushes each row) so the
    chain resolves.
    """
    data = dict(data)
    if not data.get("id"):
        data["id"] = new_id()
    parent_path = None
    parent_id = data.get("parent_id")
    if parent_id:
        parent = session.get(models.SampleTaxonomy, parent_id)
        if parent is None:
            # Refuse to silently root an orphan: the append-only path
            # would be wrong forever. The parent must be created first.
            raise UnknownParent(parent_id)
        parent_path = parent.path
    data["path"] = build_path(data["id"], parent_path)
    return _insert(session, orm_cls(**_row(orm_cls, data)), commit=commit)


# ── WP-9 hand-off state machine (mutable job-queue state) ─────────────────


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def ready_handoff(
    session: Session,
    acquisition_run_id: str,
    *,
    analysis_run_id: str | None = None,
    detail: dict | None = None,
    attempt: int | None = None,
) -> models.Handoff:
    """Insert a ``ready`` hand-off row for a dataset (the QC gate's mark).

    UNIQUE on ``acquisition_run_id`` makes a second ready for the same dataset
    a clean 409 (``Conflict``) rather than a duplicate queue entry — a dataset
    is readied once per attempt. Re-readying a *failed* dataset is a separate,
    explicit path (see ``reready_failed_handoff``), not a plain re-POST.
    """
    row = models.Handoff(
        id=new_id(),
        acquisition_run_id=acquisition_run_id,
        analysis_run_id=analysis_run_id,
        state="ready",
        detail=detail,
        attempt=attempt if attempt is not None else 0,
    )
    return _insert(session, row, commit=True)


def claim_handoff(
    session: Session,
    worker: str,
    *,
    acquisition_run_id: str | None = None,
) -> models.Handoff | None:
    """Atomically claim a ``ready`` dataset (the exactly-once guarantee).

    A single guarded UPDATE flips ``ready -> claimed`` only while the row is
    still ``ready``; ``rowcount == 1`` means this caller won, ``0`` means it
    lost (someone else claimed it, or it is not ready). Two concurrent claimers
    therefore cannot both win: the database serializes the conditional UPDATE,
    so exactly one sees rowcount 1. With ``acquisition_run_id`` omitted this
    claims any one ready row ("claim next"), picking the oldest by ``id``.

    Returns the claimed ``Handoff`` on a win, or ``None`` on a loss / no work.
    """
    target_id = acquisition_run_id
    if target_id is None:
        # Claim-next: pick a candidate id, then CAS-claim *that* id. The guarded
        # UPDATE still enforces exactly-once — if another worker grabs the same
        # candidate first, our rowcount is 0 and we return None (the caller
        # retries for the next one).
        row = (
            session.query(models.Handoff.acquisition_run_id)
            .filter(models.Handoff.state == "ready")
            .order_by(models.Handoff.id)
            .first()
        )
        if row is None:
            return None
        target_id = row[0]

    now = _utcnow()
    result = session.execute(
        update(models.Handoff)
        .where(
            models.Handoff.acquisition_run_id == target_id,
            models.Handoff.state == "ready",
        )
        .values(state="claimed", worker=worker, updated_at=now)
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    session.expire_all()
    return (
        session.query(models.Handoff)
        .filter(models.Handoff.acquisition_run_id == target_id)
        .one()
    )


def transition_handoff(
    session: Session,
    acquisition_run_id: str,
    *,
    expected: str,
    to: str,
    worker: str | None = None,
    analysis_run_id: str | None = None,
    detail: dict | None = None,
) -> models.Handoff | None:
    """Guarded state transition ``expected -> to`` on one hand-off row.

    Rejects any transition not in ``HANDOFF_TRANSITIONS`` up front (illegal
    edge), then applies a single guarded UPDATE keyed on the row still being in
    ``expected``: ``rowcount 1`` wins, ``0`` loses (wrong current state / racey
    / terminal) and the caller gets ``None`` -> 409. Optional ``worker`` /
    ``analysis_run_id`` / ``detail`` are set on the won row (e.g. the failure
    reason on ``-> failed``).
    """
    if to not in HANDOFF_TRANSITIONS.get(expected, set()):
        return None
    values: dict[str, Any] = {"state": to, "updated_at": _utcnow()}
    if worker is not None:
        values["worker"] = worker
    if analysis_run_id is not None:
        values["analysis_run_id"] = analysis_run_id
    if detail is not None:
        values["detail"] = detail
    result = session.execute(
        update(models.Handoff)
        .where(
            models.Handoff.acquisition_run_id == acquisition_run_id,
            models.Handoff.state == expected,
        )
        .values(**values)
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    session.expire_all()
    return (
        session.query(models.Handoff)
        .filter(models.Handoff.acquisition_run_id == acquisition_run_id)
        .one()
    )


def reready_failed_handoff(
    session: Session,
    acquisition_run_id: str,
    *,
    detail: dict | None = None,
) -> models.Handoff | None:
    """Re-ready a ``failed`` dataset, bumping ``attempt`` (guarded UPDATE).

    Only a row currently in ``failed`` may be re-queued; the guarded UPDATE
    (``... WHERE state='failed'``) makes this exactly-once too (rowcount 0 =>
    None => 409). ``attempt`` is incremented so the retry is distinguishable in
    the queue history.
    """
    result = session.execute(
        update(models.Handoff)
        .where(
            models.Handoff.acquisition_run_id == acquisition_run_id,
            models.Handoff.state == "failed",
        )
        .values(
            state="ready",
            worker=None,
            attempt=models.Handoff.attempt + 1,
            detail=detail,
            updated_at=_utcnow(),
        )
    )
    if result.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    session.expire_all()
    return (
        session.query(models.Handoff)
        .filter(models.Handoff.acquisition_run_id == acquisition_run_id)
        .one()
    )

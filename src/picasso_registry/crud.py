"""Append-only persistence helpers.

Every writer path funnels through here so the append-only rule (insert, never
mutate) and the id-minting / ``extra``-absorption conventions live in one place.
A missing ``id`` is minted as a ULID; ``None`` values are dropped so column
defaults (e.g. ``created_at``) apply and absent features stay NULL.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from ulid import ULID

from . import models
from .taxonomy import build_path


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

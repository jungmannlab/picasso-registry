"""Append-only persistence helpers.

Every writer path funnels through here so the append-only rule (insert, never
mutate) and the id-minting / ``extra``-absorption conventions live in one place.
A missing ``id`` is minted as a ULID; ``None`` values are dropped so column
defaults (e.g. ``created_at``) apply and absent features stay NULL.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session
from ulid import ULID

from . import models
from .taxonomy import build_path


class UnknownParent(Exception):
    """A taxonomy node references a parent that does not (yet) exist."""

    def __init__(self, parent_id: str) -> None:
        super().__init__(f"unknown parent_id: {parent_id!r}")
        self.parent_id = parent_id


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


def _known(orm_cls: type, data: dict, *, with_id: bool) -> dict[str, Any]:
    cols = _columns(orm_cls)
    out = {k: v for k, v in data.items() if k in cols and v is not None}
    if with_id and not out.get("id"):
        out["id"] = new_id()
    return out


def persist(
    session: Session, orm_cls: type, data: dict, *, commit: bool = True
):
    """Insert one row of ``orm_cls`` from ``data`` (extra keys ignored)."""
    obj = orm_cls(**_known(orm_cls, data, with_id=True))
    session.add(obj)
    session.flush()
    if commit:
        session.commit()
    return obj


def persist_metrics(
    session: Session, orm_cls: type, data: dict, *, commit: bool = True
):
    """Insert a metrics row, folding unknown keys into the JSON ``extra``.

    ``orm_cls`` is always :class:`models.Metrics`; the signature matches
    :func:`persist` so both can be used interchangeably by the route factory
    and the bulk-ingest loop.
    """
    cols = _columns(orm_cls)
    known = {k: v for k, v in data.items() if k in cols and v is not None}
    unknown = {k: v for k, v in data.items() if k not in cols}
    extra = dict(known.get("extra") or {})
    extra.update(unknown)
    if extra:
        known["extra"] = extra
    if not known.get("id"):
        known["id"] = new_id()
    obj = orm_cls(**known)
    session.add(obj)
    session.flush()
    if commit:
        session.commit()
    return obj


def persist_taxonomy(
    session: Session, orm_cls: type, data: dict, *, commit: bool = True
):
    """Insert a taxonomy node, computing its materialized ``path``.

    The parent must already be persisted (bulk-ingest orders taxonomy first and
    flushes each row) so the path chain resolves.
    """
    data = dict(data)
    if not data.get("id"):
        data["id"] = new_id()
    if not data.get("path"):
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
    obj = orm_cls(**_known(orm_cls, data, with_id=False))
    session.add(obj)
    session.flush()
    if commit:
        session.commit()
    return obj

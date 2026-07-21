"""Pure helpers for the sample taxonomy tree.

Kept side-effect-free (no DB) so they are trivially testable. The materialized
``path`` is ``/root_id/.../node_id/`` — a leading and trailing slash around the
slash-joined chain of ancestor ids from the root down to (and including) the
node. That makes ancestor lookup and tree-distance a string/list operation.
"""

from __future__ import annotations

from typing import Any


def build_path(node_id: str, parent_path: str | None) -> str:
    """Materialized path for ``node_id`` given its parent's path.

    Root nodes (no parent) get ``/node_id/``.
    """
    base = parent_path if parent_path else "/"
    if not base.endswith("/"):
        base += "/"
    return f"{base}{node_id}/"


def path_ids(path: str | None) -> list[str]:
    """Ancestor ids from root to node (inclusive), in order."""
    return [p for p in (path or "").split("/") if p]


def tree_distance(path_a: str | None, path_b: str | None) -> int:
    """Steps between two nodes via their nearest common ancestor.

    Distance 0 is the same node; a parent/child pair is 1; siblings are 2.
    """
    a = path_ids(path_a)
    b = path_ids(path_b)
    common = 0
    for x, y in zip(a, b):
        if x != y:
            break
        common += 1
    return (len(a) - common) + (len(b) - common)


def deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge ``override`` onto ``base`` (override wins).

    Nested dicts merge key-by-key; scalars and lists are replaced wholesale.
    Neither input is mutated.
    """
    result: dict[str, Any] = dict(base or {})
    for key, value in (override or {}).items():
        current = result.get(key)
        if isinstance(value, dict) and isinstance(current, dict):
            result[key] = deep_merge(current, value)
        else:
            result[key] = value
    return result

"""Contract artifacts: committed OpenAPI stays in sync; taxonomy helper units."""

import json

from picasso_registry.export_openapi import build_spec, repo_root
from picasso_registry.taxonomy import (
    build_path,
    deep_merge,
    path_ids,
    tree_distance,
)


def test_openapi_committed_in_sync():
    committed_path = repo_root() / "openapi.json"
    assert (
        committed_path.exists()
    ), "run `python -m picasso_registry.export_openapi`"
    committed = json.loads(committed_path.read_text())
    assert committed == build_spec(), (
        "openapi.json is stale — regenerate with "
        "`python -m picasso_registry.export_openapi`"
    )


def test_openapi_covers_core_surface():
    spec = build_spec()
    for path in ("/cohort", "/node_defaults", "/bulk", "/metrics", "/health"):
        assert path in spec["paths"], path


def test_build_path_root_and_child():
    assert build_path("root", None) == "/root/"
    assert build_path("child", "/root/") == "/root/child/"


def test_path_ids_order():
    assert path_ids("/a/b/c/") == ["a", "b", "c"]
    assert path_ids(None) == []


def test_tree_distance():
    hela = "/any/cell/hela/"
    neuron = "/any/cell/neuron/"
    cell = "/any/cell/"
    assert tree_distance(hela, hela) == 0
    assert tree_distance(hela, cell) == 1  # parent
    assert tree_distance(hela, neuron) == 2  # siblings
    assert tree_distance(hela, "/any/") == 2  # grandparent


def test_deep_merge_override_and_nest():
    base = {"a": 1, "n": {"x": 1, "y": 2}}
    override = {"a": 9, "n": {"y": 3, "z": 4}}
    assert deep_merge(base, override) == {
        "a": 9,
        "n": {"x": 1, "y": 3, "z": 4},
    }
    # inputs untouched
    assert base == {"a": 1, "n": {"x": 1, "y": 2}}

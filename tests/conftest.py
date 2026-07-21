"""Shared fixtures: an isolated in-memory registry per test."""

import pytest
from fastapi.testclient import TestClient

from picasso_registry.testing import make_memory_app


@pytest.fixture
def app():
    a = make_memory_app()
    yield a
    a.dependency_overrides.clear()


@pytest.fixture
def client(app):
    return TestClient(app)

"""In-memory registry for tests — importable by any dependent repo.

``mock_registry()`` yields a client with the full ``RegistryClient`` method
surface, backed by a fresh in-memory SQLite database and the real FastAPI app
(via ``TestClient``) — no network, no running service, no shared state between
uses. Dependent repos use it to test their registry interactions without
standing up the service::

    from picasso_registry.testing import mock_registry

    with mock_registry() as reg:
        reg.log_acquisition(id="run1", status="done")
        assert reg.get("acquisition_run", "run1")["status"] == "done"

Importing this module pulls in the test dependencies (Starlette's TestClient /
httpx); it is intended for test code, not the runtime import path.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterator

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from .app import create_app
from .client import _BaseRegistry
from .db import Base, get_session


def _memory_engine():
    # StaticPool keeps every connection pointed at the same in-memory DB.
    return create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
        future=True,
    )


def make_memory_app():
    """A fresh app bound to its own shared in-memory SQLite database."""
    engine = _memory_engine()
    session_factory = sessionmaker(
        bind=engine, autoflush=False, expire_on_commit=False, future=True
    )
    from . import models  # noqa: F401  (register tables on Base)

    Base.metadata.create_all(engine)

    app = create_app()

    def _override() -> Iterator[Any]:
        session = session_factory()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_session] = _override
    return app


class MockRegistryClient(_BaseRegistry):
    """``RegistryClient`` surface backed by an in-memory ``TestClient``."""

    def __init__(self, app=None) -> None:
        from fastapi.testclient import TestClient

        self.app = app or make_memory_app()
        self.client = TestClient(self.app)

    def _get(self, path: str, params: dict | None = None) -> Any:
        r = self.client.get(path, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict | None = None) -> Any:
        r = self.client.post(path, json=json)
        r.raise_for_status()
        return r.json()

    def close(self) -> None:
        self.client.close()


@contextmanager
def mock_registry() -> Iterator[MockRegistryClient]:
    """Context manager yielding an isolated in-memory registry client.

    The client is closed on exit, so using it after the ``with`` block raises
    rather than silently falling through to a real database.
    """
    reg = MockRegistryClient()
    try:
        yield reg
    finally:
        reg.close()

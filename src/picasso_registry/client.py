"""Thin HTTP client other packages import. Keep in lockstep with app.py.

``_BaseRegistry`` holds the method surface (generic create/get/list plus the
common ``log_*`` and query shortcuts) in terms of ``_get`` / ``_post``, so the
real ``RegistryClient`` (over ``requests``) and the in-memory test mock in
``picasso_registry.testing`` share one implementation.
"""

from __future__ import annotations

from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class _BaseRegistry:
    """Endpoint method surface; subclasses supply ``_get`` / ``_post``."""

    def _get(
        self, path: str, params: dict | None = None
    ) -> Any:  # pragma: no cover
        raise NotImplementedError

    def _post(
        self, path: str, json: dict | None = None
    ) -> Any:  # pragma: no cover
        raise NotImplementedError

    # generic CRUD ----------------------------------------------------------
    def create(self, resource: str, **fields: Any) -> dict[str, Any]:
        return self._post(f"/{resource}", fields)

    def get(self, resource: str, item_id: str) -> dict[str, Any]:
        return self._get(f"/{resource}/{item_id}")

    def list(self, resource: str, **params: Any) -> list[dict[str, Any]]:
        return self._get(f"/{resource}", params or None)

    # convenience shortcuts -------------------------------------------------
    def health(self) -> dict[str, Any]:
        return self._get("/health")

    def add_taxon(self, **fields: Any) -> dict[str, Any]:
        return self._post("/sample_taxonomy", fields)

    def log_experiment(self, **fields: Any) -> dict[str, Any]:
        return self._post("/experiment", fields)

    def log_acquisition(self, **fields: Any) -> dict[str, Any]:
        return self._post("/acquisition_run", fields)

    def log_fov(self, **fields: Any) -> dict[str, Any]:
        return self._post("/fov", fields)

    def log_analysis(self, **fields: Any) -> dict[str, Any]:
        return self._post("/analysis_run", fields)

    def log_metrics(self, **fields: Any) -> dict[str, Any]:
        return self._post("/metrics", fields)

    def cohort(self, taxon_id: str, **params: Any) -> list[dict[str, Any]]:
        return self._get("/cohort", {"taxon_id": taxon_id, **params})

    def node_defaults(self, taxon_id: str) -> dict[str, Any]:
        return self._get("/node_defaults", {"taxon_id": taxon_id})

    def bulk_ingest(self, **tables: Any) -> dict[str, Any]:
        return self._post("/bulk", tables)


class RegistryClient(_BaseRegistry):
    """Talks to a running registry service over HTTP."""

    def __init__(
        self, base_url: str = "http://127.0.0.1:8000", timeout: float = 10
    ) -> None:
        if requests is None:
            raise RuntimeError("install picasso-registry[client] (requests)")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: dict | None = None) -> Any:
        r = requests.get(
            f"{self.base_url}{path}", params=params, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict | None = None) -> Any:
        r = requests.post(
            f"{self.base_url}{path}", json=json, timeout=self.timeout
        )
        r.raise_for_status()
        return r.json()

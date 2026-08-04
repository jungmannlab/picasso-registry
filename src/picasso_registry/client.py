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

    def cohort(
        self,
        taxon_id: str,
        *,
        limit: int | None = None,
        max_distance: int | None = None,
        modality: str | None = None,
        dimensionality: str | None = None,
        buffer: str | None = None,
        target: str | None = None,
        target_set: list[str] | None = None,
        metric: str | None = None,
        match_depth: str | None = None,
        **params: Any,
    ) -> list[dict[str, Any]]:
        """Ranked cohort for ``taxon_id`` (A2 three-axis descriptor).

        All args past ``taxon_id`` are optional and keyword-only, so the
        S0B-1 ``cohort(taxon_id)`` / ``cohort(taxon_id, max_distance=...)``
        calls keep working unchanged. ``metric`` (or ``match_depth``) selects
        which axes must match; ``modality`` is required once a metric depth is
        in play, and ``target``/``target_set`` additionally for structure/
        biology/kinetics metrics. ``None`` args are dropped so the server sees
        only what was supplied.
        """
        query: dict[str, Any] = {"taxon_id": taxon_id}
        optional = {
            "limit": limit,
            "max_distance": max_distance,
            "modality": modality,
            "dimensionality": dimensionality,
            "buffer": buffer,
            "target": target,
            "target_set": target_set,
            "metric": metric,
            "match_depth": match_depth,
        }
        query.update({k: v for k, v in optional.items() if v is not None})
        query.update(params)
        return self._get("/cohort", query)

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

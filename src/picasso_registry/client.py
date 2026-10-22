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

    def log_cohort_range(self, **fields: Any) -> dict[str, Any]:
        """Append one learned (taxon, metric) expected-range snapshot.

        Thin wrapper over ``create("cohort_metric_range", ...)`` — WP-7's
        range write-back. Append-only: a re-derived range POSTs a new row;
        consumers read the latest per (taxon_id, metric).
        """
        return self._post("/cohort_metric_range", fields)

    # WP-9 hand-off state machine ------------------------------------------
    def mark_ready(
        self,
        acquisition_run_id: str,
        *,
        analysis_run_id: str | None = None,
        detail: dict | None = None,
        attempt: int | None = None,
    ) -> dict[str, Any]:
        """QC gate: mark a dataset ready for cluster hand-off.

        A second ready for the same dataset is a 409 (``_post`` raises) — the
        dataset is readied once per attempt.
        """
        body: dict[str, Any] = {"acquisition_run_id": acquisition_run_id}
        if analysis_run_id is not None:
            body["analysis_run_id"] = analysis_run_id
        if detail is not None:
            body["detail"] = detail
        if attempt is not None:
            body["attempt"] = attempt
        return self._post("/handoff/ready", body)

    def claim_handoff(
        self,
        worker: str,
        *,
        acquisition_run_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Atomically claim a ready dataset (exactly-once).

        Returns the claimed hand-off row on a **win**; returns ``None`` on a
        **loss** (the server 409s — lost the race, or nothing is ready). Other
        HTTP errors propagate. With ``acquisition_run_id`` omitted, claims any
        one ready dataset ("claim next").
        """
        body: dict[str, Any] = {"worker": worker}
        if acquisition_run_id is not None:
            body["acquisition_run_id"] = acquisition_run_id
        return self._post_or_none_on_conflict("/handoff/claim", body)

    def transition_handoff(
        self,
        acquisition_run_id: str,
        *,
        expected: str,
        to: str,
        worker: str | None = None,
        analysis_run_id: str | None = None,
        detail: dict | None = None,
    ) -> dict[str, Any] | None:
        """Guarded state transition ``expected -> to``.

        Returns the updated row on success; returns ``None`` when the server
        409s (illegal / racey transition — the row was not in ``expected``).
        """
        body: dict[str, Any] = {
            "acquisition_run_id": acquisition_run_id,
            "expected": expected,
            "to": to,
        }
        if worker is not None:
            body["worker"] = worker
        if analysis_run_id is not None:
            body["analysis_run_id"] = analysis_run_id
        if detail is not None:
            body["detail"] = detail
        return self._post_or_none_on_conflict("/handoff/transition", body)

    def list_handoffs(
        self, *, state: str | None = None, **params: Any
    ) -> list[dict[str, Any]]:
        """List hand-offs, optionally filtered by ``state`` (find work)."""
        if state is not None:
            params["state"] = state
        return self._get("/handoff", params or None)

    def _post_or_none_on_conflict(
        self, path: str, body: dict
    ) -> dict[str, Any] | None:  # pragma: no cover
        """``_post`` but a 409 becomes ``None`` (claim/transition lose).

        Overridden per transport so a lost claim is a clean ``None`` instead of
        a raised HTTP error. The base raises (subclasses must implement).
        """
        raise NotImplementedError

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
        target_class: str | None = None,
    ) -> list[dict[str, Any]]:
        """Ranked cohort for ``taxon_id`` (A2 descriptor axes, register C12).

        All args past ``taxon_id`` are optional and keyword-only, so the
        S0B-1 ``cohort(taxon_id)`` / ``cohort(taxon_id, max_distance=...)``
        calls keep working unchanged. ``modality`` / ``dimensionality`` /
        ``buffer`` (axis 3) and ``target`` / ``target_set`` / ``target_class``
        (axis 2) are independent filters — pass whichever axes the comparison
        requires. ``None`` args are dropped so the server sees only what was
        supplied. The params are an explicit allow-list (no ``**kwargs``) so a
        misspelled filter name raises ``TypeError`` instead of silently
        returning an unfiltered cohort.

        An explicitly empty ``target_set`` (and no ``target``/``target_class``)
        overlaps no run, so it short-circuits to ``[]`` rather than letting the
        empty list evaporate into "no filter" over the query string.
        """
        if target_set == [] and target is None and target_class is None:
            return []
        query: dict[str, Any] = {"taxon_id": taxon_id}
        optional = {
            "limit": limit,
            "max_distance": max_distance,
            "modality": modality,
            "dimensionality": dimensionality,
            "buffer": buffer,
            "target": target,
            "target_set": target_set,
            "target_class": target_class,
        }
        query.update({k: v for k, v in optional.items() if v is not None})
        return self._get("/cohort", query)

    def node_defaults(self, taxon_id: str) -> dict[str, Any]:
        return self._get("/node_defaults", {"taxon_id": taxon_id})

    def bulk_ingest(self, **tables: Any) -> dict[str, Any]:
        return self._post("/bulk", tables)


class RegistryClient(_BaseRegistry):
    """Talks to a running registry service over HTTP."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        timeout: float = 10,
        token: str | None = None,
    ) -> None:
        if requests is None:
            raise RuntimeError("install picasso-registry[client] (requests)")
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        # An optional static bearer token (ADR 001 / C18). Present -> every
        # request carries ``Authorization: Bearer <token>``; absent -> no header
        # at all, so a loopback dev instance and the in-memory mock still work.
        self.token = token

    def _auth_headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"} if self.token else {}

    def _get(self, path: str, params: dict | None = None) -> Any:
        r = requests.get(
            f"{self.base_url}{path}",
            params=params,
            timeout=self.timeout,
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

    def _post(self, path: str, json: dict | None = None) -> Any:
        r = requests.post(
            f"{self.base_url}{path}",
            json=json,
            timeout=self.timeout,
            headers=self._auth_headers(),
        )
        r.raise_for_status()
        return r.json()

    def _post_or_none_on_conflict(
        self, path: str, body: dict
    ) -> dict[str, Any] | None:
        r = requests.post(
            f"{self.base_url}{path}",
            json=body,
            timeout=self.timeout,
            headers=self._auth_headers(),
        )
        if r.status_code == 409:
            return None
        r.raise_for_status()
        return r.json()

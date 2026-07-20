"""Thin HTTP client other packages import. Keep in lockstep with app.py."""

from __future__ import annotations

from typing import Any

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None


class RegistryClient:
    def __init__(self, base_url: str = "http://127.0.0.1:8000") -> None:
        if requests is None:
            raise RuntimeError("install picasso-registry[client] (requests)")
        self.base_url = base_url.rstrip("/")

    def health(self) -> dict[str, Any]:
        return requests.get(f"{self.base_url}/health", timeout=10).json()

    def log_acquisition(self, **fields: Any) -> dict[str, Any]:
        r = requests.post(
            f"{self.base_url}/acquisition_run", json=fields, timeout=10
        )
        r.raise_for_status()
        return r.json()

    def log_metrics(self, **fields: Any) -> dict[str, Any]:
        r = requests.post(f"{self.base_url}/metrics", json=fields, timeout=10)
        r.raise_for_status()
        return r.json()

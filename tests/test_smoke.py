from fastapi.testclient import TestClient

from picasso_registry.app import app
from picasso_registry.db import init_db


def test_health():
    init_db()
    client = TestClient(app)
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_log_metrics_accepts_extra_keys():
    client = TestClient(app)
    r = client.post(
        "/metrics",
        json={"analysis_run_id": "run1", "nena_nm": 3.1, "novel_metric": 42},
    )
    assert r.status_code == 200

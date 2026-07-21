"""Smoke tests: health + the metrics ``extra`` contract."""


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_metrics_accepts_and_stores_extra_keys(client):
    r = client.post(
        "/metrics",
        json={"analysis_run_id": "run1", "nena_nm": 3.1, "novel_metric": 42},
    )
    assert r.status_code == 200
    metric_id = r.json()["id"]

    got = client.get(f"/metrics/{metric_id}").json()
    assert got["nena_nm"] == 3.1  # typed column
    assert got["extra"] == {"novel_metric": 42}  # unknown key -> JSON extra

"""WP-7 range write-back: the append-only ``cohort_metric_range`` table.

Learned per-(taxon, metric) expected ranges land here as append-only snapshots
(p05/p50/p95 + n + source). A re-derivation inserts a NEW row rather than
mutating the prior one; consumers read the LATEST per (taxon_id, metric) by
``created_at`` / insertion order.
"""

from picasso_registry.testing import mock_registry


def test_create_and_list_round_trips():
    with mock_registry() as reg:
        reg.add_taxon(id="t-origami", name="DNA-Origami")
        created = reg.log_cohort_range(
            taxon_id="t-origami",
            metric="nena_nm",
            p05=2.0,
            p50=3.1,
            p95=4.5,
            n=42,
            source="wp7-backfill-ranges",
        )
        assert created["id"]  # server minted a ULID
        got = reg.get("cohort_metric_range", created["id"])
        assert got["metric"] == "nena_nm"
        assert got["p50"] == 3.1
        assert got["n"] == 42
        assert got["source"] == "wp7-backfill-ranges"
        # created_at is auto-set so read-latest ordering is well-defined.
        assert got["created_at"] is not None

        listed = reg.list("cohort_metric_range")
        assert [r["id"] for r in listed] == [created["id"]]


def test_bulk_ingest_accepts_cohort_metric_range():
    with mock_registry() as reg:
        result = reg.bulk_ingest(
            sample_taxonomy=[{"id": "t-cell", "name": "Cell"}],
            cohort_metric_range=[
                {
                    "taxon_id": "t-cell",
                    "metric": "photons_median",
                    "p05": 900.0,
                    "p50": 2500.0,
                    "p95": 6000.0,
                    "n": 11,
                    "source": "wp7-backfill-ranges",
                }
            ],
        )
        assert result["counts"]["cohort_metric_range"] == 1
        rows = reg.list("cohort_metric_range")
        assert len(rows) == 1
        assert rows[0]["taxon_id"] == "t-cell"


def test_fk_to_taxon_resolves():
    """A range row's taxon_id resolves to a real taxonomy node (the cohort
    key the recommender will join on)."""
    with mock_registry() as reg:
        reg.add_taxon(id="t-root", name="root")
        row = reg.log_cohort_range(
            taxon_id="t-root", metric="nena_nm", p50=3.0, n=5
        )
        assert reg.get("cohort_metric_range", row["id"])["taxon_id"] == (
            "t-root"
        )
        # the referenced taxon is a real, fetchable node
        assert reg.get("sample_taxonomy", "t-root")["name"] == "root"


def test_append_only_two_snapshots_keep_both_latest_identifiable():
    """Re-deriving a range for the same (taxon, metric) appends a new row;
    both persist and the latest is identifiable by created_at / insert order
    (ULID id is time-sortable)."""
    with mock_registry() as reg:
        reg.add_taxon(id="t-x", name="x")
        first = reg.log_cohort_range(
            taxon_id="t-x",
            metric="nena_nm",
            p50=3.0,
            n=5,
            source="wp7-backfill-ranges",
        )
        second = reg.log_cohort_range(
            taxon_id="t-x",
            metric="nena_nm",
            p50=2.7,
            n=20,
            source="wp7-backfill-ranges",
        )
        # Both snapshots persisted (append-only, no overwrite).
        rows = [
            r
            for r in reg.list("cohort_metric_range")
            if r["taxon_id"] == "t-x" and r["metric"] == "nena_nm"
        ]
        assert len(rows) == 2
        assert {r["id"] for r in rows} == {first["id"], second["id"]}

        # read-latest: the most recent snapshot wins (by created_at, with the
        # time-sortable ULID id as the deterministic tiebreak).
        latest = max(rows, key=lambda r: (r["created_at"], r["id"]))
        assert latest["id"] == second["id"]
        assert latest["p50"] == 2.7
        assert latest["n"] == 20

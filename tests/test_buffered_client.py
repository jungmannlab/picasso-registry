"""BufferedRegistryClient (WP-3): non-blocking writes, replay-on-failure,
idempotent replay, and concurrent writers — all hermetic (in-memory mock)."""

import threading
import time

from picasso_registry.buffered_client import BufferedRegistryClient
from picasso_registry.client import _BaseRegistry
from picasso_registry.testing import MockRegistryClient


class FlakyTransport(_BaseRegistry):
    """A registry transport backed by the in-memory mock, with an on/off
    switch. While ``down`` it raises a bare (response-less) error — the same
    shape as a connection refusal — so the buffer must retry, not drop."""

    def __init__(self):
        self.inner = MockRegistryClient()
        self.down = False
        self.post_calls = 0

    def _get(self, path, params=None):
        if self.down:
            raise ConnectionError("registry unreachable")
        return self.inner._get(path, params)

    def _post(self, path, json=None):
        self.post_calls += 1
        if self.down:
            raise ConnectionError("registry unreachable")
        return self.inner._post(path, json)


class _Resp:
    def __init__(self, status_code):
        self.status_code = status_code


class _HttpError(Exception):
    """requests/httpx-shaped error carrying an HTTP status (``.response``)."""

    def __init__(self, status_code):
        super().__init__(f"HTTP {status_code}")
        self.response = _Resp(status_code)


class StatusTransport(_BaseRegistry):
    """POSTs fail with a fixed HTTP status until ``status`` is cleared, so the
    buffer's retriable-vs-permanent classification can be exercised."""

    def __init__(self, status):
        self.inner = MockRegistryClient()
        self.status = status  # None => healthy

    def _get(self, path, params=None):
        return self.inner._get(path, params)

    def _post(self, path, json=None):
        if self.status is not None:
            raise _HttpError(self.status)
        return self.inner._post(path, json)


class LostResponseTransport(_BaseRegistry):
    """Commits the first POST server-side but raises as if the response was
    lost, so the buffer replays the *same* POST — exercising client-id dedup
    for tables without a server-side natural key."""

    def __init__(self):
        self.inner = MockRegistryClient()
        self.fail_next = True

    def _get(self, path, params=None):
        return self.inner._get(path, params)

    def _post(self, path, json=None):
        result = self.inner._post(path, json)  # commits before we "lose" it
        if self.fail_next:
            self.fail_next = False
            raise ConnectionError("response lost after commit")
        return result


class _StuckThread:
    """Stands in for a flusher that won't stop within the join timeout."""

    def is_alive(self):
        return True

    def join(self, timeout=None):
        return None


def _buffered(tmp_path, transport, **kw):
    return BufferedRegistryClient(
        buffer_path=str(tmp_path / "buf.sqlite"),
        inner=transport,
        flush_interval=0.02,
        max_backoff=0.1,
        start=True,
        **kw,
    )


def test_write_while_down_is_buffered_then_replayed(tmp_path):
    transport = FlakyTransport()
    transport.down = True
    reg = _buffered(tmp_path, transport)
    try:
        # Caller keeps running with the registry down: no raise, no block.
        for i in range(5):
            ack = reg.log_acquisition(id=f"run{i}", status="running")
            assert ack == {"buffered": True}
        # Nothing reached the server yet (it's down), but nothing was lost.
        time.sleep(0.1)
        assert reg.pending() == 5

        # Server comes back: the background flusher replays everything.
        transport.down = False
        assert reg.flush(timeout=5) is True
        assert reg.pending() == 0

        stored = {r["id"] for r in transport.inner.list("acquisition_run")}
        assert stored == {f"run{i}" for i in range(5)}
    finally:
        reg.close()


def test_caller_never_raises_or_blocks_when_down(tmp_path):
    transport = FlakyTransport()
    transport.down = True
    reg = _buffered(tmp_path, transport)
    try:
        start = time.monotonic()
        for i in range(50):
            reg.log_metrics(analysis_run_id=f"a{i}", nena_nm=3.0)
        # 50 best-effort writes against a dead registry return effectively
        # instantly — they only touch the local buffer.
        assert time.monotonic() - start < 1.0
        assert reg.pending() == 50
    finally:
        reg.close()


def test_reads_pass_through_synchronously(tmp_path):
    transport = FlakyTransport()
    reg = _buffered(tmp_path, transport)
    try:
        transport.inner.log_acquisition(id="r1", status="done")
        # Reads are synchronous and see server state immediately.
        assert reg.get("acquisition_run", "r1")["status"] == "done"
        assert reg.health()["status"] == "ok"
    finally:
        reg.close()


def test_replay_is_idempotent_on_duplicate(tmp_path):
    # A lost-response replay double-sends the same POST. The server's
    # (run_id, kind, attempt) key -> 409 -> flusher treats it as success and
    # drops it, so no duplicate row and the buffer still drains.
    transport = FlakyTransport()
    reg = _buffered(tmp_path, transport)
    try:
        # Pre-store the row directly, then buffer the *same* natural key: the
        # replay will collide (409) and must be absorbed, not retried forever.
        transport.inner.log_analysis(
            acquisition_run_id="run1", kind="cluster", attempt=1
        )
        reg.log_analysis(acquisition_run_id="run1", kind="cluster", attempt=1)
        assert reg.flush(timeout=5) is True
        assert reg.pending() == 0

        rows = transport.inner.list("analysis_run")
        keyed = [
            r
            for r in rows
            if r["acquisition_run_id"] == "run1"
            and r["kind"] == "cluster"
            and r["attempt"] == 1
        ]
        assert len(keyed) == 1  # exactly one row despite the duplicate POST
    finally:
        reg.close()


def test_permanent_client_error_does_not_wedge_buffer(tmp_path):
    # A malformed write (422) can never succeed on retry; it must be dropped so
    # it doesn't block every later write behind it in the ordered buffer.
    transport = FlakyTransport()
    reg = _buffered(tmp_path, transport)
    try:
        # metrics requires analysis_run_id -> 422 on the server.
        reg.create("metrics", nena_nm=3.1)  # bad: no analysis_run_id
        reg.log_acquisition(id="good", status="done")
        assert reg.flush(timeout=5) is True
        assert reg.pending() == 0
        # the good write got through; the bad one was dropped, not requeued.
        assert transport.inner.get("acquisition_run", "good")["status"] == (
            "done"
        )
    finally:
        reg.close()


def test_concurrent_writers_no_loss_no_corruption(tmp_path):
    transport = FlakyTransport()
    transport.down = True  # buffer everything first, replay after
    reg = _buffered(tmp_path, transport)
    try:
        # Kept under the server's default list page size (100) so the
        # assertion reads every stored row in one GET.
        n_threads, per_thread = 8, 10

        def writer(t):
            for i in range(per_thread):
                reg.log_acquisition(id=f"r{t}_{i}", status="x")

        threads = [
            threading.Thread(target=writer, args=(t,))
            for t in range(n_threads)
        ]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        assert reg.pending() == n_threads * per_thread

        transport.down = False
        assert reg.flush(timeout=10) is True
        stored = {
            r["id"]
            for r in transport.inner.list("acquisition_run", limit=1000)
        }
        expected = {
            f"r{t}_{i}" for t in range(n_threads) for i in range(per_thread)
        }
        assert stored == expected  # every write survived, none duplicated
    finally:
        reg.close()


def test_background_flusher_drains_without_explicit_flush(tmp_path):
    # No flush() call: the daemon thread should drain on its own once the
    # server is reachable.
    transport = FlakyTransport()
    reg = _buffered(tmp_path, transport)
    try:
        for i in range(3):
            reg.log_acquisition(id=f"bg{i}")
        deadline = time.monotonic() + 5
        while reg.pending() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert reg.pending() == 0
        stored = {r["id"] for r in transport.inner.list("acquisition_run")}
        assert stored == {"bg0", "bg1", "bg2"}
    finally:
        reg.close()


def test_buffer_is_durable_across_client_restart(tmp_path):
    # SQLite buffer survives a process/client restart: writes made while down
    # by one client are replayed by a fresh client pointed at the same file.
    path = str(tmp_path / "buf.sqlite")
    transport = FlakyTransport()
    transport.down = True
    reg1 = BufferedRegistryClient(
        buffer_path=path, inner=transport, flush_interval=0.02, start=False
    )
    reg1.log_acquisition(id="persisted", status="running")
    reg1.close()  # simulate crash/shutdown before the server came back

    transport.down = False
    reg2 = BufferedRegistryClient(
        buffer_path=path, inner=transport, flush_interval=0.02, start=True
    )
    try:
        assert reg2.flush(timeout=5) is True
        assert (
            transport.inner.get("acquisition_run", "persisted")["status"]
            == "running"
        )
    finally:
        reg2.close()


def test_retriable_4xx_stays_queued_until_recovered(tmp_path):
    # 401/403/408/429 are recoverable (e.g. an expired/missing bearer token
    # under the fail-closed auth invariant): the write must stay buffered and
    # replay once the server accepts it, never be silently dropped.
    transport = StatusTransport(status=401)
    reg = _buffered(tmp_path, transport)
    try:
        reg.log_acquisition(id="held", status="running")
        assert reg.flush(timeout=0.3) is False  # still 401 -> stays queued
        assert reg.pending() == 1
        transport.status = None  # token fixed / server healthy
        assert reg.flush(timeout=5) is True
        assert reg.pending() == 0
        assert transport.inner.get("acquisition_run", "held")["status"] == (
            "running"
        )
    finally:
        reg.close()


def test_permanent_4xx_is_dropped(tmp_path):
    # A 422/400/404 can never succeed on replay; it is dropped so it does not
    # wedge every later write behind it in the ordered buffer.
    transport = StatusTransport(status=422)
    reg = _buffered(tmp_path, transport)
    try:
        reg.log_acquisition(id="bad", status="x")
        assert reg.flush(timeout=5) is True
        assert reg.pending() == 0
    finally:
        reg.close()


def test_close_flushes_pending_when_server_recovers(tmp_path):
    # close()'s shutdown drain must actually deliver buffered writes when the
    # server is reachable at shutdown, not silently leave them for the next
    # client. Large intervals keep the background loop asleep in backoff so
    # this exercises the final drain, not the periodic one.
    transport = FlakyTransport()
    transport.down = True
    reg = BufferedRegistryClient(
        buffer_path=str(tmp_path / "buf.sqlite"),
        inner=transport,
        flush_interval=30,
        max_backoff=30,
        start=True,
    )
    for i in range(4):
        reg.log_acquisition(id=f"c{i}", status="running")
    assert reg.pending() == 4
    transport.down = False  # server back exactly as we shut down
    reg.close()  # final drain must flush
    stored = {r["id"] for r in transport.inner.list("acquisition_run")}
    assert stored == {f"c{i}" for i in range(4)}


def test_replay_dedups_server_minted_row_by_client_id(tmp_path):
    # metrics has no natural key (its id is server-minted). The buffered
    # client stamps a stable client-side id at enqueue, so a lost-response
    # replay of the same buffered write collides on the PK (409) instead of
    # inserting a second row.
    transport = LostResponseTransport()
    reg = _buffered(tmp_path, transport)
    try:
        reg.log_metrics(analysis_run_id="a1", nena_nm=3.0)
        assert reg.flush(timeout=5) is True
        assert reg.pending() == 0
        rows = transport.inner.list("metrics")
        assert len(rows) == 1  # committed once, replay deduped — no duplicate
    finally:
        reg.close()


def test_close_leaves_connection_open_when_flusher_lingers(tmp_path):
    # If the flusher can't stop in time (e.g. stuck in a slow POST), close()
    # must not close the connection out from under it — that would raise in
    # the daemon thread on its next buffer op. Leave it open instead.
    transport = FlakyTransport()
    reg = BufferedRegistryClient(
        buffer_path=str(tmp_path / "buf.sqlite"),
        inner=transport,
        start=False,
    )
    reg._thread = _StuckThread()  # simulate a flusher that won't stop
    reg.close()  # must not raise, must not close the connection
    # pending() would raise sqlite3.ProgrammingError on a closed connection.
    assert reg.pending() == 0
    reg._thread = None
    reg._conn.close()

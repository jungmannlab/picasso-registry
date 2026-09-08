"""Resilient, non-blocking registry client (WP-3).

The stack logs provenance from code that must never stall or crash because the
registry is momentarily down: live acquisition on the instrument PC and cluster
analysis both keep running regardless. ``BufferedRegistryClient`` gives that
guarantee — a **best-effort, fire-and-forget writer** wrapped around the plain
:class:`~picasso_registry.client.RegistryClient`:

* **Writes** (``log_*`` / ``create`` / ``add_taxon`` / ``bulk_ingest`` — every
  ``POST``) are appended to a small **on-disk SQLite buffer** and return
  immediately. A background thread drains the buffer, replaying each POST
  against the server; on failure the row stays queued and is retried (with
  backoff) until the server is reachable again. The calling thread never
  blocks on the network and never sees a network exception.
* **Reads** (``GET``: ``get`` / ``list`` / ``cohort`` / ``node_defaults`` /
  ``health``) stay **synchronous** — they pass straight through and raise
  normally, since a reader has nothing to buffer and wants a fresh answer.

**Why SQLite for the buffer** (not a plain file / ``queue.Queue`` / a second
dep): the registry is already a SQLite shop, so it adds no dependency; it is
ACID and survives a process crash or power loss on the instrument PC (an
in-memory queue would lose un-flushed writes), and concurrent enqueues from
multiple threads are safe. Each buffered POST carries a monotonic ``seq`` so
replay preserves submission order (parents before children).

**Idempotency interplay.** Replay can double-send a POST whose response was
lost. That is safe because every write path has a natural/PK idempotency key
server-side (``acquisition_run.id``; ``analysis_run``'s
``(acquisition_run_id, kind, attempt)``): a duplicate returns **409**, which the
flusher treats as *already-applied success* and drops from the buffer. So
at-least-once delivery + server-side dedup == effectively exactly-once.

Usage::

    from picasso_registry.buffered_client import BufferedRegistryClient

    reg = BufferedRegistryClient("http://registry:8000")
    reg.log_acquisition(id="run1", status="running")   # returns at once
    ...                                                  # registry down? fine
    reg.flush(timeout=5)          # optional: block until the buffer drains
    reg.close()                   # stop the flusher (flushes best-effort)

    # or scoped:
    with BufferedRegistryClient("http://registry:8000") as reg:
        reg.log_metrics(analysis_run_id="a1", nena_nm=3.0)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from typing import Any

from .client import RegistryClient, _BaseRegistry

logger = logging.getLogger(__name__)


class _RegistryUnreachable(Exception):
    """Transient failure — keep the row buffered and retry."""


def _is_permanent(status: int) -> bool:
    """A replayed POST that the server has definitively handled/rejected.

    409 == the row is already stored (idempotent replay succeeded). Other 4xx
    are client errors (bad payload / validation) that will never succeed on
    retry, so we must not wedge the buffer on them — drop and move on. 5xx and
    connection errors are transient and stay queued.
    """
    return status == 409 or 400 <= status < 500


class BufferedRegistryClient(_BaseRegistry):
    """Non-blocking, replay-on-failure wrapper over ``RegistryClient``.

    Reads pass through synchronously; writes are durably buffered on disk and
    flushed by a background thread. A registry outage never raises to or blocks
    the caller.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8000",
        *,
        buffer_path: str = "registry_buffer.sqlite",
        timeout: float = 10,
        flush_interval: float = 1.0,
        max_backoff: float = 30.0,
        inner: _BaseRegistry | None = None,
        start: bool = True,
    ) -> None:
        # ``inner`` lets tests inject a mock/TestClient-backed transport; in
        # production it defaults to the real HTTP client.
        self._inner = inner or RegistryClient(base_url, timeout=timeout)
        self._buffer_path = buffer_path
        self._flush_interval = flush_interval
        self._max_backoff = max_backoff

        # One connection per client, guarded by a lock. check_same_thread is
        # off so the enqueueing caller thread and the flusher thread can share
        # it; the lock serializes access (SQLite itself is the durable store).
        self._conn = sqlite3.connect(
            buffer_path, check_same_thread=False, isolation_level=None
        )
        self._lock = threading.Lock()
        # Serializes *draining*: at most one thread replays the buffer at a
        # time. Without it the background flusher and an explicit flush() (or
        # two flushers) could pop the same row and double-send through the
        # single, not-thread-safe HTTP transport, corrupting it and losing
        # writes. Enqueue (_post) is not on this lock, so writers never wait
        # on a drain.
        self._drain_lock = threading.Lock()
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS outbox ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " path TEXT NOT NULL,"
            " body TEXT NOT NULL)"
        )

        self._wake = threading.Event()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start:
            self.start()

    # ── lifecycle ─────────────────────────────────────────────────────────
    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="registry-flush", daemon=True
        )
        self._thread.start()

    def close(self) -> None:
        """Stop the flusher (best-effort final drain) and close the buffer."""
        self._stop.set()
        self._wake.set()
        if self._thread:
            self._thread.join(timeout=self._flush_interval + 5)
        with self._lock:
            self._conn.close()

    def __enter__(self) -> "BufferedRegistryClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # ── read: straight through, synchronous ───────────────────────────────
    def _get(self, path: str, params: dict | None = None) -> Any:
        return self._inner._get(path, params)

    # ── write: enqueue, return immediately ─────────────────────────────────
    def _post(self, path: str, json_body: dict | None = None) -> Any:
        """Enqueue a POST to the durable buffer; never blocks on the network.

        Returns a small acknowledgement dict (not the server row — the write is
        asynchronous). If even the local buffer write fails, we log and swallow
        so the caller still proceeds; provenance is best-effort by design.
        """
        try:
            with self._lock:
                self._conn.execute(
                    "INSERT INTO outbox(path, body) VALUES (?, ?)",
                    (path, json.dumps(json_body or {})),
                )
        except Exception:  # pragma: no cover - defensive
            logger.exception("registry buffer write failed; dropping %s", path)
            return {"buffered": False}
        self._wake.set()
        return {"buffered": True}

    # ``_BaseRegistry.create`` calls ``self._post(path, fields)`` positionally,
    # so the second positional arg maps onto ``json_body`` above regardless of
    # the name; no override of the method surface is needed.

    # ── flushing ───────────────────────────────────────────────────────────
    def pending(self) -> int:
        """Number of un-replayed writes still in the buffer."""
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM outbox").fetchone()
        return int(row[0])

    def _peek(self) -> tuple[int, str, dict] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT seq, path, body FROM outbox ORDER BY seq LIMIT 1"
            ).fetchone()
        if row is None:
            return None
        return row[0], row[1], json.loads(row[2])

    def _delete(self, seq: int) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM outbox WHERE seq = ?", (seq,))

    def _send_one(self, path: str, body: dict) -> None:
        """POST one buffered row; raise ``_RegistryUnreachable`` if transient.

        A 409 (or any other permanent 4xx) is *not* raised — it means the row
        is done (already stored, or unsalvageable) so the caller drops it.
        """
        try:
            self._inner._post(path, body)
        except Exception as exc:  # requests/httpx HTTPError or ConnectionError
            status = _status_of(exc)
            if status is not None and _is_permanent(status):
                if status != 409:
                    logger.warning(
                        "registry rejected buffered POST %s (%s); dropping",
                        path,
                        status,
                    )
                return  # permanent -> treat as handled, drop from buffer
            raise _RegistryUnreachable(str(exc)) from exc

    def flush(self, timeout: float | None = None) -> bool:
        """Drain the buffer now, blocking up to ``timeout`` seconds.

        Returns ``True`` if the buffer is empty when it returns. Intended for
        tests and for a clean shutdown; normal operation relies on the
        background flusher. Never raises on a registry outage — it just returns
        ``False`` if it couldn't fully drain in time.
        """
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._drain_lock:
            while True:
                item = self._peek()
                if item is None:
                    return True
                seq, path, body = item
                try:
                    self._send_one(path, body)
                except _RegistryUnreachable:
                    if deadline is not None and time.monotonic() >= deadline:
                        return False
                    time.sleep(min(self._flush_interval, 0.05))
                    continue
                self._delete(seq)

    def _run(self) -> None:
        """Background loop: replay buffered writes with exponential backoff."""
        backoff = self._flush_interval
        while not self._stop.is_set():
            drained = self._drain_once()
            if drained:
                backoff = self._flush_interval
                # Sleep until woken by a new write or asked to stop.
                self._wake.wait(timeout=self._flush_interval)
                self._wake.clear()
            else:
                # Server unreachable: back off, but stay responsive to stop.
                self._wake.wait(timeout=backoff)
                self._wake.clear()
                backoff = min(backoff * 2, self._max_backoff)
        # Best-effort final drain on shutdown (bounded, never blocks forever).
        self._drain_once()

    def _drain_once(self) -> bool:
        """Replay buffered rows until empty or the server goes unreachable.

        Returns ``True`` if the buffer emptied, ``False`` on a transient
        failure (so the caller backs off).
        """
        with self._drain_lock:
            while not self._stop.is_set():
                item = self._peek()
                if item is None:
                    return True
                seq, path, body = item
                try:
                    self._send_one(path, body)
                except _RegistryUnreachable:
                    return False
                self._delete(seq)
            return True


def _status_of(exc: Exception) -> int | None:
    """Best-effort HTTP status extraction across requests/httpx errors.

    Both libraries hang the response off ``exc.response``; a bare
    connection/timeout error has none (``None`` -> treated as transient).
    """
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return int(status) if status is not None else None

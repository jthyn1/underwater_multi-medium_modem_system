"""forwarder.py — surface Pi. Drain the spool over the HaLow link.

One job, stated as an invariant it must never break:

    A row leaves PENDING only when the server has confirmed a durable write.

Everything else here — batching, backoff, adaptive sizing, catch-up — is
throughput. That one line is correctness, and it is the reason `mark_delivered`
is called after the HTTP response and never before it.

Run:  python3 forwarder.py
Stop: SIGINT / SIGTERM. Rows left in-flight revert to pending on the next
      start, which is safe because a duplicate write to InfluxDB overwrites
      rather than duplicates (Textbook §19).
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import os
import random
import signal
import threading
import time
import uuid

import requests

import telemetry_wire as wire
from spool import Spool

log = logging.getLogger("forwarder")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SPOOL_PATH = os.environ.get("SPOOL_PATH", "/var/lib/telemetry/spool.db")
GATEWAY_URL = os.environ.get("GATEWAY_URL", "http://192.168.50.10:8086/ingest")
GATEWAY_TOKEN = os.environ.get("GATEWAY_TOKEN", "")
NODE = os.environ.get("NODE", "surface")

BATCH_MIN = int(os.environ.get("BATCH_MIN", "50"))
BATCH_MAX = int(os.environ.get("BATCH_MAX", "1000"))
BATCH_START = int(os.environ.get("BATCH_START", "250"))

IDLE_S = float(os.environ.get("IDLE_S", "2.0"))
MIN_BACKOFF_S = float(os.environ.get("MIN_BACKOFF_S", "1.0"))
MAX_BACKOFF_S = float(os.environ.get("MAX_BACKOFF_S", "60.0"))
HTTP_TIMEOUT_S = float(os.environ.get("HTTP_TIMEOUT_S", "30"))

MAX_ATTEMPTS = int(os.environ.get("MAX_ATTEMPTS", "5"))
TRIM_EVERY_S = float(os.environ.get("TRIM_EVERY_S", "30"))
HEALTH_EVERY_S = float(os.environ.get("HEALTH_EVERY_S", "30"))
DELIVERED_RETENTION_S = float(os.environ.get("DELIVERED_RETENTION_S", "300"))


# ---------------------------------------------------------------------------
# Error classification
# ---------------------------------------------------------------------------
#
# The distinction drives everything: transient means "retry the same bytes
# later", permanent means "these bytes will never work". Getting it wrong
# toward transient gives a loop that blocks the queue — slow and visible.
# Getting it wrong toward permanent discards good data during a blip — fast
# and silent. When genuinely unsure, be transient and let `attempts` catch it.


class TransientError(Exception):
    """Retry the same batch later."""


class BatchTooLarge(Exception):
    """Not an error about the data — an error about how much of it we sent."""


# ---------------------------------------------------------------------------
# Forwarder
# ---------------------------------------------------------------------------


class Forwarder:
    def __init__(self, spool: Spool, url: str, token: str, node: str):
        self.spool = spool
        self.url = url
        self.node = node
        self.batch_size = BATCH_START
        self._stop = threading.Event()

        # requests.Session keeps the TCP connection (and its congestion
        # window) alive between batches. On a link with 500 ms of latency a
        # fresh handshake per batch is three round trips of pure overhead,
        # and during catch-up you pay it hundreds of times.
        self.http = requests.Session()
        self.http.headers.update(
            {
                "Content-Type": "application/json",
                "Content-Encoding": "gzip",
                "X-Node-Id": node,
            }
        )
        if token:
            self.http.headers["Authorization"] = f"Bearer {token}"

        # Counters for §35.2. Monotonic within a process; the server side
        # handles resets.
        self.batches_sent_total = 0
        self.batches_failed_total = 0
        self.bytes_sent_total = 0
        self.points_acked_total = 0
        self.points_failed_total = 0
        self._last_ack_mono = time.monotonic()
        self._next_trim = time.monotonic()
        self._next_health = time.monotonic()

    def stop(self) -> None:
        self._stop.set()

    # -- the loop -----------------------------------------------------------

    def run(self) -> None:
        recovered = self.spool.requeue_inflight()
        if recovered:
            log.info("recovered %d in-flight rows from a previous run", recovered)

        backoff = MIN_BACKOFF_S
        while not self._stop.is_set():
            self._housekeeping()

            batch = self.spool.claim_batch(limit=self.batch_size, max_attempts=MAX_ATTEMPTS)
            if not batch:
                self._stop.wait(IDLE_S)
                continue

            ids = [r["id"] for r in batch]
            try:
                acked, failed = self._send(batch)
            except BatchTooLarge:
                self.spool.release(ids)
                self.batch_size = max(BATCH_MIN, self.batch_size // 2)
                log.warning("batch too large; shrinking to %d", self.batch_size)
                continue  # not a link problem — retry immediately, smaller
            except TransientError as e:
                self.spool.release(ids)
                self.batches_failed_total += 1
                # Jitter matters if you ever run more than one node: without
                # it, every node that lost the AP retries in lockstep and
                # recreates the outage on reconnection.
                wait = min(backoff, MAX_BACKOFF_S) * (0.5 + random.random())
                log.warning("send failed (%s); retrying %d rows in %.1fs", e, len(ids), wait)
                self._stop.wait(wait)
                backoff = min(backoff * 2, MAX_BACKOFF_S)
                continue

            self.batches_sent_total += 1
            backoff = MIN_BACKOFF_S

            if acked:
                self.spool.mark_delivered(acked)
                self.points_acked_total += len(acked)
                self._last_ack_mono = time.monotonic()
            if failed:
                # The gateway has told us these bytes are unusable, with a
                # reason. Believing it is the whole point of having a `failed`
                # list — an unparseable payload retried forever is how a
                # pipeline stops without anything appearing to be broken.
                self.spool.quarantine([f["id"] for f in failed])
                self.points_failed_total += len(failed)
                for f in failed[:5]:
                    log.error("row %s permanently rejected: %s", f["id"], f.get("reason"))

            # Anything neither acked nor failed was not accounted for. Put it
            # back rather than assuming: the gateway may have been restarted
            # mid-response, and a row we forget is a row nobody retries.
            unresolved = set(ids) - set(acked) - {f["id"] for f in failed}
            if unresolved:
                self.spool.release(sorted(unresolved))
                log.warning("%d rows returned unresolved; released", len(unresolved))

            # Grow slowly on success, up to the cap. Additive increase against
            # the multiplicative decrease above — the same control loop TCP
            # uses, for the same reason.
            if not failed and self.batch_size < BATCH_MAX:
                self.batch_size = min(BATCH_MAX, self.batch_size + BATCH_MIN)

            # No sleep. THIS is catch-up mode: after an outage the drain rate
            # is bounded by the link, not by a timer. A forwarder that sleeps
            # here drains a two-hour backlog in a day, during which it is
            # still accumulating.
            continue

        log.info("stopping; %d rows in flight will revert on restart", self.spool.stats()["spool_inflight"])

    # -- one batch ----------------------------------------------------------

    def _send(self, batch) -> tuple[list[int], list[dict]]:
        batch_id = uuid.uuid4().hex
        envelope = {
            "batch_id": batch_id,
            "node": self.node,
            "sent_ns": time.time_ns(),
            "readings": [
                {
                    "id": r["id"],
                    "acq_ns": r["acq_ns"],
                    "src": r["src"],
                    "link": r["link"],
                    # base64 because the payloads are raw structs, and JSON
                    # cannot carry arbitrary bytes. The ~33% inflation is
                    # almost entirely undone by gzip, which is why it is not
                    # worth a binary envelope and a hand-rolled parser.
                    "payload_b64": base64.b64encode(r["payload"]).decode("ascii"),
                }
                for r in batch
            ],
        }
        body = gzip.compress(json.dumps(envelope).encode("utf-8"), compresslevel=6)
        self.bytes_sent_total += len(body)

        try:
            resp = self.http.post(self.url, data=body, timeout=HTTP_TIMEOUT_S)
        except requests.RequestException as e:
            raise TransientError(f"transport: {e}") from e

        if resp.status_code == 413:
            raise BatchTooLarge()
        if resp.status_code == 429:
            retry_after = resp.headers.get("Retry-After")
            if retry_after:
                try:
                    self._stop.wait(min(float(retry_after), MAX_BACKOFF_S))
                except ValueError:
                    pass
            raise TransientError("rate limited")
        if resp.status_code in (401, 403):
            # Ambiguous on purpose: a bad token is permanent, an expired one
            # is transient after a refresh. Treated as transient so a token
            # rotation does not eat a dive's worth of data, but logged at
            # error so it does not hide.
            log.error("gateway rejected our credentials (%s)", resp.status_code)
            raise TransientError(f"auth {resp.status_code}")
        if resp.status_code >= 500:
            raise TransientError(f"server {resp.status_code}")
        if resp.status_code == 400:
            # The envelope itself was rejected. That is either a genuinely
            # malformed batch or a bug in this file, and we cannot tell which
            # from here — so it is released, retried, and eventually
            # quarantined by the attempts counter. Deliberately the slow,
            # visible failure rather than the fast, silent one: quarantined
            # rows are set aside, not deleted, and `UPDATE spool SET state=0`
            # brings them all back once the bug is fixed.
            log.error("gateway rejected the batch: %s", resp.text[:400])
            raise TransientError("bad request (see log; check for a client bug)")
        if resp.status_code != 200:
            raise TransientError(f"unexpected status {resp.status_code}")

        try:
            data = resp.json()
        except ValueError as e:
            raise TransientError(f"unparseable ack: {e}") from e

        # An ack for a batch we did not send is a delayed response from a
        # previous attempt. Acting on it would mark the WRONG rows delivered,
        # because ids are per-row and this batch's rows are different rows.
        if data.get("batch_id") != batch_id:
            raise TransientError("ack batch_id mismatch; discarding stale response")

        acked = [int(i) for i in data.get("acked", [])]
        failed = [f for f in data.get("failed", []) if "id" in f]
        return acked, failed

    # -- periodic work ------------------------------------------------------

    def _housekeeping(self) -> None:
        now = time.monotonic()

        if now >= self._next_trim:
            self._next_trim = now + TRIM_EVERY_S
            # Retention first: on a healthy link this is what actually keeps
            # the file small, and it is pure history so it costs nothing.
            # A few minutes of delivered rows is nearly free and occasionally
            # tells you whether the Pi sent bad data or the server mangled
            # good data.
            cutoff = time.time_ns() - int(DELIVERED_RETENTION_S * 1e9)
            purged = self.spool.purge_delivered(older_than_ns=cutoff)
            quarantined = self.spool.quarantine_over(MAX_ATTEMPTS)
            dropped = self.spool.trim()
            if quarantined:
                log.error("quarantined %d rows that exhausted %d attempts", quarantined, MAX_ATTEMPTS)
            if dropped:
                log.warning("trim deleted %d rows (%d were undelivered)", dropped,
                            self.spool.dropped_pending_total)
            log.debug("housekeeping: purged=%d quarantined=%d trimmed=%d", purged, quarantined, dropped)

        if now >= self._next_health:
            self._next_health = now + HEALTH_EVERY_S
            self._emit_health()

    def _emit_health(self) -> None:
        """Push the pipeline's own metrics through the pipeline.

        Health goes into the spool like everything else, which means after an
        outage you receive the HISTORY of the outage rather than just its end.
        That history is how you diagnose it. It also means these metrics
        cannot be delivered while the link is down — which is fine, because
        the alert that matters is on the server and fires on ABSENCE. When
        the link dies, silence is the signal; everything here is diagnosis.
        """
        s = self.spool.stats()
        metrics = {
            "spool_pending": s["spool_pending"],
            "spool_inflight": s["spool_inflight"],
            "spool_delivered": s["spool_delivered"],
            "spool_quarantined": s["spool_quarantined"],
            "spool_total": s["spool_total"],
            "spool_bytes": s["spool_bytes"],
            "spool_capacity_frac": s["spool_capacity_frac"],
            "rows_dropped_total": s["rows_dropped_total"],
            "batches_sent_total": self.batches_sent_total,
            "batches_failed_total": self.batches_failed_total,
            "bytes_sent_total": self.bytes_sent_total,
            "points_acked_total": self.points_acked_total,
            "points_failed_total": self.points_failed_total,
            "batch_size": self.batch_size,
            "last_ack_age_s": time.monotonic() - self._last_ack_mono,
        }
        if s["oldest_pending_age_s"] is not None:
            metrics["oldest_pending_age_s"] = s["oldest_pending_age_s"]
        try:
            self.spool.append(
                time.time_ns(),
                wire.SRC_SURFACE_PIPELINE,
                "local",
                json.dumps(metrics).encode("utf-8"),
            )
        except Exception:
            log.exception("could not spool health metrics")


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    wire.wait_for_clock(timeout_s=float(os.environ.get("CLOCK_TIMEOUT_S", "300")), log=log)

    spool = Spool(SPOOL_PATH, max_rows=int(os.environ.get("SPOOL_MAX_ROWS", "500000")))
    fwd = Forwarder(spool, GATEWAY_URL, GATEWAY_TOKEN, NODE)

    def _shutdown(signum, _frame):
        log.info("signal %s; finishing current batch", signum)
        fwd.stop()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    log.info("forwarding %s -> %s", SPOOL_PATH, GATEWAY_URL)
    try:
        fwd.run()
    finally:
        spool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

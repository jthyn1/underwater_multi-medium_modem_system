"""gateway.py — server. Receive batches, write to InfluxDB, then acknowledge.

The whole service exists to enforce one ordering:

    write to InfluxDB  ->  confirm durability  ->  acknowledge

Never the other way round. Swap those two lines and every crash between them
becomes silent permanent loss instead of a harmless duplicate. That is the
entire reliability argument, and it is two statements long.

Durability is confirmed by writing line protocol to InfluxDB 3 Core's HTTP
endpoint directly and requiring a 204, rather than by calling a client
library's `write()` and hoping. Client libraries commonly buffer
asynchronously: `write()` returns having only queued the points, and a gateway
that acks at that moment is acking its own RAM. Going over HTTP removes the
question — with `no_sync=false` (the default), the 204 means InfluxDB has the
data in its WAL.

Run:  python3 gateway.py
Test: DRY_RUN=1 python3 gateway.py     # prints line protocol, writes nothing
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telemetry_wire as wire

log = logging.getLogger("gateway")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = int(os.environ.get("LISTEN_PORT", "8086"))
INGEST_TOKEN = os.environ.get("INGEST_TOKEN", "")

INFLUX_URL = os.environ.get("INFLUX_URL", "http://localhost:8181")
INFLUX_DB = os.environ.get("INFLUX_DB", "telemetry")
INFLUX_TOKEN = os.environ.get("INFLUX_TOKEN", "")
INFLUX_TIMEOUT_S = float(os.environ.get("INFLUX_TIMEOUT_S", "20"))

MAX_BODY_BYTES = int(os.environ.get("MAX_BODY_BYTES", str(16 * 1024 * 1024)))
MAX_READINGS = int(os.environ.get("MAX_READINGS", "5000"))
DRY_RUN = os.environ.get("DRY_RUN", "") not in ("", "0", "false")

# How many "drop the one bad line and retry" rounds before giving up and
# writing the batch a line at a time. Bad lines are rare; this bounds the
# worst case without making the common case slow.
MAX_ISOLATION_ROUNDS = 20


class InfluxTransient(Exception):
    """InfluxDB is unreachable or unwell. The Pi must retry the whole batch."""


class InfluxRejected(Exception):
    """InfluxDB refused specific line protocol. Permanent for those lines."""

    def __init__(self, message: str, line_number: int | None = None):
        super().__init__(message)
        self.line_number = line_number


# ---------------------------------------------------------------------------
# The InfluxDB write path
# ---------------------------------------------------------------------------


def write_lp(lines: list[str]) -> None:
    """POST line protocol. Returns on confirmed durability, raises otherwise.

    Parameters chosen deliberately:

    * `precision=nanosecond` — the default is `auto`, which infers precision
      from the magnitude of the number. Inference is fine until the day it
      guesses wrong and a point lands in 1970 or 2262. State it.
    * `accept_partial=false` — the default accepts the good lines and rejects
      the bad ones, which sounds helpful and is not: a 400 would then mean
      "some unknown subset landed", and there is no honest ack to send back.
      All-or-nothing makes the response unambiguous, and the isolation loop
      below finds the offender.
    * `no_sync` left at its default of false — the response is not sent until
      the write is in the WAL. Setting it true makes writes faster and makes
      this gateway a liar.
    """
    if DRY_RUN:
        for line in lines:
            print(line)
        return

    query = urllib.parse.urlencode(
        {
            "db": INFLUX_DB,
            "precision": "nanosecond",
            "accept_partial": "false",
            "no_sync": "false",
        }
    )
    url = f"{INFLUX_URL.rstrip('/')}/api/v3/write_lp?{query}"
    body = "\n".join(lines).encode("utf-8")
    headers = {"Content-Type": "text/plain; charset=utf-8"}
    if INFLUX_TOKEN:
        headers["Authorization"] = f"Bearer {INFLUX_TOKEN}"
    if len(body) > 4096:
        body = gzip.compress(body, compresslevel=6)
        headers["Content-Encoding"] = "gzip"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=INFLUX_TIMEOUT_S) as resp:
            if resp.status in (200, 204):
                return
            raise InfluxTransient(f"unexpected status {resp.status}")
    except urllib.error.HTTPError as e:
        detail = e.read(8192).decode("utf-8", "replace")
        if e.code in (400, 422):
            raise InfluxRejected(detail, _line_number_from(detail)) from e
        # 401/403 here are a server misconfiguration, not bad data. Transient
        # is the right class: the Pi keeps its rows while somebody fixes the
        # token, rather than quarantining a dive's worth of readings.
        raise InfluxTransient(f"http {e.code}: {detail[:200]}") from e
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        raise InfluxTransient(f"transport: {e}") from e


def _line_number_from(detail: str) -> int | None:
    """Pull `data.line_number` out of the v3 error body.

    Shape (InfluxDB 3 Core, accept_partial=false):
        {"error": "parsing failed for write_lp endpoint",
         "data": {"original_line": "...", "line_number": 3,
                  "error_message": "..."}}

    Treated as best-effort: if the shape changes, isolation falls back to
    writing one line at a time, which is slower and equally correct.
    """
    try:
        obj = json.loads(detail)
    except ValueError:
        return None
    data = obj.get("data")
    if isinstance(data, list):
        data = data[0] if data else None
    if isinstance(data, dict):
        n = data.get("line_number")
        if isinstance(n, int):
            return n
    return None


def write_with_isolation(items: list[tuple[int, str]]) -> tuple[list[int], list[dict]]:
    """Write (row_id, line) pairs; return (written_ids, rejected).

    On the happy path this is one HTTP request. When InfluxDB rejects the
    batch, the bad line is removed and the rest is retried, so one poison
    reading costs one extra round trip instead of costing the batch.

    This is the partial-failure handling the whole design needs: blanket
    retrying a batch that contains one permanently bad point re-sends the good
    points forever (harmless, per the overwrite semantics) while never
    resolving the bad one (not harmless — it blocks the queue).
    """
    remaining = list(items)
    rejected: list[dict] = []

    for _ in range(MAX_ISOLATION_ROUNDS):
        if not remaining:
            return [], rejected  # every line was isolated as bad
        try:
            write_lp([lp for _, lp in remaining])
            written = [i for i, _ in remaining]
            return written, rejected
        except InfluxRejected as e:
            n = e.line_number
            if n is None or not (1 <= n <= len(remaining)):
                break  # unusable hint — fall through to one-at-a-time
            bad_id, bad_line = remaining.pop(n - 1)
            rejected.append({"id": bad_id, "reason": f"influx rejected: {str(e)[:200]}"})
            log.warning("isolated bad line for row %s: %s", bad_id, bad_line[:160])

    # Fallback: write them individually. Slow, and only reached when a batch
    # is mostly bad or the error shape changed — both of which are situations
    # where being certain beats being quick.
    written = []
    for rid, lp in remaining:
        try:
            write_lp([lp])
            written.append(rid)
        except InfluxRejected as e:
            rejected.append({"id": rid, "reason": f"influx rejected: {str(e)[:200]}"})
    return written, rejected


# ---------------------------------------------------------------------------
# Batch handling
# ---------------------------------------------------------------------------


class Stats:
    def __init__(self):
        self.lock = threading.Lock()
        self.points_written_total = 0
        self.points_rejected_total = 0
        self.batches_total = 0
        self.write_latency_ms = 0.0
        self.end_to_end_latency_s = 0.0
        self.last_batch_mono = 0.0

    def snapshot(self) -> dict:
        with self.lock:
            return dict(
                points_written_total=float(self.points_written_total),
                points_rejected_total=float(self.points_rejected_total),
                batches_total=float(self.batches_total),
                write_latency_ms=float(self.write_latency_ms),
                end_to_end_latency_s=float(self.end_to_end_latency_s),
            )


STATS = Stats()


def build_lines(readings: list[dict]) -> tuple[list[tuple[int, str]], list[dict]]:
    """Decode and validate. Returns (writable, rejected).

    Everything rejected here is permanent by construction — the same bytes
    would fail the same way forever — which is what makes it safe to tell the
    Pi to stop retrying them.
    """
    import base64

    writable: list[tuple[int, str]] = []
    rejected: list[dict] = []
    now_ns = time.time_ns()

    for r in readings:
        rid = r.get("id")
        try:
            if not isinstance(rid, int):
                raise wire.DecodeError("missing or non-integer id")
            acq_ns = int(r["acq_ns"])

            # The unset-clock guard, and it must be PERMANENT. A reading
            # stamped 1970 will be stamped 1970 on every retry; treating it as
            # transient is an infinite loop, and writing it puts the point in
            # a shard nothing queries — invisible rather than wrong, which is
            # worse.
            if not wire.clock_is_plausible(acq_ns):
                raise wire.DecodeError(f"timestamp out of range: {acq_ns}")

            payload = base64.b64decode(r["payload_b64"], validate=True)
            measurement, tags, fields = wire.decode_row(r["src"], r["link"], payload)

            # The subsea clock packet is only interesting relative to ours.
            # Skew is the number that says whether a subsea timestamp would
            # have been worth trusting — and it is computed here because this
            # is the first place both clocks are in the same expression.
            if "tx_wall_clock_s" in fields:
                fields["clock_skew_s"] = acq_ns / 1e9 - fields["tx_wall_clock_s"]

            writable.append((rid, wire.to_line(measurement, tags, fields, acq_ns)))

        except (KeyError, TypeError, ValueError, wire.DecodeError) as e:
            rejected.append({"id": rid, "reason": f"{type(e).__name__}: {e}"[:200]})

    if writable:
        newest = max(int(r["acq_ns"]) for r in readings if isinstance(r.get("acq_ns"), int))
        with STATS.lock:
            STATS.end_to_end_latency_s = (now_ns - newest) / 1e9
    return writable, rejected


def handle_batch(envelope: dict) -> dict:
    node = envelope.get("node")
    if node not in wire.VALID_NODE:
        raise ValueError(f"unknown node: {node!r}")
    readings = envelope.get("readings")
    if not isinstance(readings, list):
        raise ValueError("readings must be a list")
    if len(readings) > MAX_READINGS:
        raise MemoryError(f"{len(readings)} readings exceeds {MAX_READINGS}")

    writable, rejected = build_lines(readings)

    started = time.monotonic()
    written_ids, write_rejected = write_with_isolation(writable)
    elapsed_ms = (time.monotonic() - started) * 1000

    rejected.extend(write_rejected)
    with STATS.lock:
        STATS.points_written_total += len(written_ids)
        STATS.points_rejected_total += len(rejected)
        STATS.batches_total += 1
        STATS.write_latency_ms = elapsed_ms
        STATS.last_batch_mono = time.monotonic()

    # Rejected rows are acked in the sense that the Pi should stop sending
    # them — but they are reported separately, because "we stored this" and
    # "we will never store this" must not look the same to the sender.
    return {
        "batch_id": envelope.get("batch_id"),
        "acked": written_ids,
        "failed": rejected,
    }


# ---------------------------------------------------------------------------
# HTTP
# ---------------------------------------------------------------------------


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"  # keep-alive; the Pi reuses one connection
    server_version = "telemetry-gateway/1.0"

    def _reply(self, code: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not INGEST_TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {INGEST_TOKEN}"

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            snap = STATS.snapshot()
            snap["dry_run"] = DRY_RUN
            self._reply(200, snap)
        else:
            self._reply(404, {"error": "not found"})

    def do_POST(self):
        if self.path.split("?")[0] != "/ingest":
            self._reply(404, {"error": "not found"})
            return
        if not self._authorized():
            self._reply(401, {"error": "unauthorized"})
            return

        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._reply(400, {"error": "bad Content-Length"})
            return
        if length <= 0:
            self._reply(400, {"error": "empty body"})
            return
        if length > MAX_BODY_BYTES:
            # 413 is a distinct signal to the forwarder: not "your data is
            # bad" and not "I am broken", but "send less at a time". It
            # responds by halving the batch and retrying immediately.
            self._reply(413, {"error": "body too large"})
            return

        raw = self.rfile.read(length)
        try:
            if self.headers.get("Content-Encoding", "").lower() == "gzip":
                raw = gzip.decompress(raw)
            envelope = json.loads(raw.decode("utf-8"))
        except (OSError, ValueError) as e:
            self._reply(400, {"error": f"unreadable body: {e}"})
            return

        try:
            result = handle_batch(envelope)
        except MemoryError as e:
            self._reply(413, {"error": str(e)})
            return
        except (ValueError, TypeError, KeyError) as e:
            self._reply(400, {"error": f"bad envelope: {e}"})
            return
        except InfluxTransient as e:
            # 503, not 500: the Pi's classifier reads any 5xx as transient and
            # keeps the rows. Nothing has been acked, so nothing is lost.
            log.warning("influx unavailable: %s", e)
            self._reply(503, {"error": f"influx unavailable: {e}"})
            return
        except Exception:
            log.exception("unhandled error")
            self._reply(500, {"error": "internal"})
            return

        self._reply(200, result)

    def log_message(self, fmt, *args):
        log.debug("http: " + fmt, *args)


class Server(ThreadingHTTPServer):
    # One thread per connection. The single-threaded HTTPServer blocks on one
    # request at a time, so a stalled Pi would stall every other node too.
    daemon_threads = True
    allow_reuse_address = True


def health_reporter(stop: threading.Event) -> None:
    """The gateway's own metrics, into the same database as the science.

    Written directly rather than spooled: if this process cannot reach
    InfluxDB, the metric saying so cannot be delivered either — which is why
    the primary alert lives in Grafana and fires on ABSENCE of points. This is
    diagnosis; absence is detection.
    """
    while not stop.wait(30.0):
        snap = STATS.snapshot()
        try:
            line = wire.to_line(
                "pipeline_health",
                {"node": "surface", "link": "local", "component": "gateway"},
                snap,
                time.time_ns(),
            )
            write_lp([line])
        except (InfluxTransient, InfluxRejected, wire.DecodeError) as e:
            log.warning("could not write gateway health: %s", e)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    if not wire.clock_is_plausible():
        log.error("server clock is implausible; refusing to start")
        return 1
    if DRY_RUN:
        log.warning("DRY_RUN: line protocol goes to stdout, nothing is stored")

    stop = threading.Event()
    threading.Thread(target=health_reporter, args=(stop,), daemon=True).start()

    srv = Server((LISTEN_HOST, LISTEN_PORT), Handler)
    log.info("listening on %s:%d -> %s/%s", LISTEN_HOST, LISTEN_PORT, INFLUX_URL, INFLUX_DB)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        srv.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

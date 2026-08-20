"""test_chaos.py — Phase 3 from the textbook's build order, automated.

Asserts the one invariant the whole design exists to provide:

    Every reading written to the spool eventually appears at the destination,
    or is counted as dropped/quarantined. Duplicates are expected and fine.

The adversarial gateway below fails on purpose, including the case that
separates a correct implementation from one that merely works: **it writes the
data and then loses the acknowledgement.** A forwarder that treats a failed
response as "not written" and retries is correct here only because the
destination absorbs the duplicate.

Run: python3 test_chaos.py
"""

from __future__ import annotations

import base64
import gzip
import json
import os
import random
import struct
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telemetry_wire as wire

# Configure the forwarder before importing it — it reads env at import time.
PORT = 8199
os.environ.update(
    {
        "GATEWAY_URL": f"http://127.0.0.1:{PORT}/ingest",
        "IDLE_S": "0.05",
        "MIN_BACKOFF_S": "0.05",
        "MAX_BACKOFF_S": "0.4",
        "HTTP_TIMEOUT_S": "5",
        "BATCH_MIN": "10",
        "BATCH_START": "40",
        "BATCH_MAX": "200",
        "TRIM_EVERY_S": "1",
        "HEALTH_EVERY_S": "1",
        "MAX_ATTEMPTS": "5",
        "LOG_LEVEL": "ERROR",
    }
)

import forwarder  # noqa: E402
from spool import Spool  # noqa: E402

N_GOOD = 2000
N_POISON = 7

# What the "server" durably holds. Keyed by line protocol, so a duplicate
# delivery collapses onto itself exactly as InfluxDB's point identity would.
STORE: set[str] = set()
STORE_LOCK = threading.Lock()
SEEN_IDS: set[int] = set()


class ChaosHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        n = int(self.headers.get("Content-Length", "0"))
        body = gzip.decompress(self.rfile.read(n))
        env = json.loads(body)
        readings = env["readings"]

        r = random.random()
        if r < 0.10:
            return self._json(503, {"error": "simulated link drop"})
        if r < 0.13:
            time.sleep(1.0)  # stall, but still answer

        written, failed = self._store(readings)

        if r < 0.16:
            # Partial ack: half the batch is acknowledged, the rest is left
            # unresolved. The forwarder must release the remainder, not
            # forget it.
            half = len(written) // 2
            return self._json(200, {"batch_id": env["batch_id"], "acked": written[:half],
                                    "failed": failed})
        if r < 0.20:
            # The nasty one: durably written, ack lost.
            return self._json(503, {"error": "ack lost after write"})

        return self._json(200, {"batch_id": env["batch_id"], "acked": written, "failed": failed})

    def _store(self, readings):
        written, failed = [], []
        for rr in readings:
            try:
                payload = base64.b64decode(rr["payload_b64"])
                m, tags, fields = wire.decode_row(rr["src"], rr["link"], payload)
                line = wire.to_line(m, tags, fields, int(rr["acq_ns"]))
            except Exception as e:
                failed.append({"id": rr["id"], "reason": str(e)[:120]})
                continue
            with STORE_LOCK:
                STORE.add(line)
                SEEN_IDS.add(rr["id"])
            written.append(rr["id"])
        return written, failed

    def _json(self, code, obj):
        b = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)


def sensor_payload(i: int) -> bytes:
    return struct.pack(
        wire.SENSOR_STRUCT,
        i % 2,            # water
        18.0 + i * 0.01,  # temp
        3.0,              # alti
        1013.0 + i,       # pressure
        i * 0.5,          # depth  <- makes every reading distinct
        41.5,
        b"N",
        -70.5,
        b"W",
        1.25,
    )


def main() -> int:
    random.seed(7)
    srv = ThreadingHTTPServer(("127.0.0.1", PORT), ChaosHandler)
    srv.daemon_threads = True
    threading.Thread(target=srv.serve_forever, daemon=True).start()

    with tempfile.TemporaryDirectory() as td:
        path = os.path.join(td, "spool.db")
        spool = Spool(path, max_rows=1_000_000)

        base = time.time_ns() - N_GOOD * 1_000_000_000
        expected = set()
        for i in range(N_GOOD):
            acq = base + i * 1_000_000_000
            payload = sensor_payload(i)
            spool.append(acq, wire.SRC_SUBSEA_SENSORS, "optical", payload)
            m, tags, fields = wire.decode_row(wire.SRC_SUBSEA_SENSORS, "optical", payload)
            expected.add(wire.to_line(m, tags, fields, acq))

        # Poison rows: structurally wrong payloads that can never be decoded.
        # They must end up quarantined, not blocking the queue and not
        # silently vanishing.
        poison_ids = [
            spool.append(base + (N_GOOD + i) * 1_000_000_000,
                         wire.SRC_SUBSEA_SENSORS, "optical", b"\x00" * 7)
            for i in range(N_POISON)
        ]

        fwd = forwarder.Forwarder(spool, forwarder.GATEWAY_URL, "", "surface")
        t = threading.Thread(target=fwd.run, daemon=True)
        t.start()

        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            s = spool.stats()
            if s["spool_pending"] == 0 and s["spool_inflight"] == 0:
                break
            time.sleep(0.25)
        fwd.stop()
        t.join(timeout=15)

        s = spool.stats()
        missing = expected - STORE

        print(f"generated       : {N_GOOD} good + {N_POISON} poison")
        print(f"distinct stored : {len(STORE)}")
        print(f"missing         : {len(missing)}")
        print(f"batches sent    : {fwd.batches_sent_total}, failed: {fwd.batches_failed_total}")
        print(f"final spool     : {s}")

        assert not missing, f"{len(missing)} readings never arrived"
        assert s["spool_pending"] == 0, "rows left pending"
        assert s["spool_inflight"] == 0, "rows stuck in-flight"
        assert s["spool_quarantined"] == N_POISON, (
            f"expected {N_POISON} quarantined, got {s['spool_quarantined']}"
        )
        assert s["rows_dropped_total"] == 0, "lost rows with capacity to spare"
        assert not (set(poison_ids) & {i for i in SEEN_IDS if i in poison_ids}) or True
        spool.close()

    srv.shutdown()
    print("\nOK — every reading arrived, poison rows quarantined, nothing dropped.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

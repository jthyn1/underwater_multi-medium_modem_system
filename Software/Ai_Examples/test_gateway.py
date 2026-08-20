"""test_gateway.py — the real gateway against a fake InfluxDB.

Covers the parts test_chaos.py cannot, because there the "server" was a stub:

  * the actual HTTP contract between forwarder.py and gateway.py
  * line protocol generated from real payloads
  * the isolation loop — one line InfluxDB refuses must not cost the batch
  * the ack ordering: nothing is marked delivered that InfluxDB did not take

The fake InfluxDB speaks the v3 write API: 204 on success, and a 400 with the
documented `{"data": {"line_number": n}}` body when it dislikes a line. Here it
dislikes exactly one, standing in for the real-world case — a field whose type
conflicts with what the database already stores.

Run: python3 test_gateway.py
"""

from __future__ import annotations

import gzip
import json
import os
import struct
import tempfile
import threading
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import telemetry_wire as wire

INFLUX_PORT = 8188
GATEWAY_PORT = 8189

os.environ.update(
    {
        "INFLUX_URL": f"http://127.0.0.1:{INFLUX_PORT}",
        "INFLUX_DB": "telemetry",
        "LISTEN_PORT": str(GATEWAY_PORT),
        "INGEST_TOKEN": "test-token",
        "GATEWAY_URL": f"http://127.0.0.1:{GATEWAY_PORT}/ingest",
        "GATEWAY_TOKEN": "test-token",
        "IDLE_S": "0.05",
        "MIN_BACKOFF_S": "0.05",
        "MAX_BACKOFF_S": "0.3",
        "BATCH_START": "64",
        "TRIM_EVERY_S": "1",
        "HEALTH_EVERY_S": "3600",   # keep health rows out of the arithmetic
        "LOG_LEVEL": "ERROR",
    }
)

import forwarder  # noqa: E402
import gateway  # noqa: E402
from spool import Spool  # noqa: E402

POISON_DEPTH = 999.0          # the one reading the fake InfluxDB will refuse (no good row uses it)
ACCEPTED: list[str] = []
LOCK = threading.Lock()
QUERIES: list[dict] = []


class FakeInflux(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def do_POST(self):
        path, _, qs = self.path.partition("?")
        assert path == "/api/v3/write_lp", path
        QUERIES.append(dict(urllib.parse.parse_qsl(qs)))

        raw = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.headers.get("Content-Encoding") == "gzip":
            raw = gzip.decompress(raw)
        lines = raw.decode().split("\n")

        for i, line in enumerate(lines, start=1):
            if f"depth_m={POISON_DEPTH!r}" in line:
                body = json.dumps(
                    {
                        "error": "parsing failed for write_lp endpoint",
                        "data": {
                            "original_line": line,
                            "line_number": i,
                            "error_message": "field type conflict: depth_m",
                        },
                    }
                ).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

        with LOCK:
            ACCEPTED.extend(lines)
        self.send_response(204)
        self.send_header("Content-Length", "0")
        self.end_headers()


def sensor_payload(depth: float, i: int) -> bytes:
    return struct.pack(
        wire.SENSOR_STRUCT, 1, 18.0 + i * 0.01, 3.0, 1013.0, depth,
        41.5, b"N", 70.5, b"W", 1.25,   # NMEA convention: magnitude + hemisphere char
    )


def main() -> int:
    influx = ThreadingHTTPServer(("127.0.0.1", INFLUX_PORT), FakeInflux)
    influx.daemon_threads = True
    threading.Thread(target=influx.serve_forever, daemon=True).start()

    gw = gateway.Server(("127.0.0.1", GATEWAY_PORT), gateway.Handler)
    threading.Thread(target=gw.serve_forever, daemon=True).start()

    n_good, poison_id = 300, None
    with tempfile.TemporaryDirectory() as td:
        spool = Spool(os.path.join(td, "spool.db"))
        base = time.time_ns() - n_good * 1_000_000_000

        for i in range(n_good):
            spool.append(base + i * 1_000_000_000, wire.SRC_SUBSEA_SENSORS, "optical",
                         sensor_payload(i * 0.5, i))
        poison_id = spool.append(
            base + n_good * 1_000_000_000, wire.SRC_SUBSEA_SENSORS, "optical",
            sensor_payload(POISON_DEPTH, 0),
        )
        # A payload that fails at the gateway's own decoder rather than at
        # InfluxDB — the other permanent-failure route.
        undecodable_id = spool.append(
            base + (n_good + 1) * 1_000_000_000, wire.SRC_SUBSEA_SENSORS, "optical", b"short"
        )
        # An optical status blob, to prove the second decoder path works too.
        spool.append(
            base + (n_good + 2) * 1_000_000_000, wire.SRC_SURFACE_OPTICAL, "local",
            json.dumps({"snr": 42, "temperature": 31.5, "packets_received": 100,
                        "packets_lost": 4, "nested": {"gain": 900}}).encode(),
        )

        fwd = forwarder.Forwarder(spool, forwarder.GATEWAY_URL, "test-token", "surface")
        t = threading.Thread(target=fwd.run, daemon=True)
        t.start()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            s = spool.stats()
            if s["spool_pending"] == 0 and s["spool_inflight"] == 0:
                break
            time.sleep(0.1)
        fwd.stop()
        t.join(timeout=10)

        s = spool.stats()
        lines = [l for l in ACCEPTED if l]

        env_lines = [l for l in lines if l.startswith("environment,")]
        opt_lines = [l for l in lines if l.startswith("optical_status,")]

        print(f"accepted by influx : {len(lines)} lines "
              f"({len(env_lines)} environment, {len(opt_lines)} optical_status)")
        print(f"write query params : {QUERIES[0]}")
        print(f"final spool        : "
              f"pending={s['spool_pending']} inflight={s['spool_inflight']} "
              f"delivered={s['spool_delivered']} quarantined={s['spool_quarantined']}")
        print(f"sample line        : {env_lines[0]}")
        print(f"optical line       : {opt_lines[0]}")

        assert s["spool_pending"] == 0 and s["spool_inflight"] == 0
        assert len(env_lines) == n_good, f"expected {n_good} environment points, got {len(env_lines)}"
        assert len(opt_lines) == 1
        assert s["spool_quarantined"] == 2, f"expected 2 quarantined, got {s['spool_quarantined']}"
        assert not any(f"depth_m={POISON_DEPTH!r}" in l for l in lines), "poison line was stored"

        # The write parameters are load-bearing, not decoration.
        q = QUERIES[0]
        assert q["precision"] == "nanosecond", q
        assert q["accept_partial"] == "false", q
        assert q["no_sync"] == "false", q

        # Tag hygiene: node and link only (plus the static variant on optical
        # status). No sequence numbers, no ids, no timestamps in tags.
        tagset = env_lines[0].split(" ")[0]
        assert tagset == "environment,link=optical,node=subsea", tagset
        assert "longitude_deg=-70.5" in env_lines[0], "W hemisphere must become a negative longitude"

        spool.close()

    gw.shutdown()
    influx.shutdown()
    print("\nOK — isolation works, params correct, nothing acked that Influx refused.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

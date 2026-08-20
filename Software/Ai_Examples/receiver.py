"""receiver.py — surface Pi. Sniff the optical link, put readings in the spool.

This replaces the print-only receiver. The trailing comment on the original
described exactly this job:

    Receives the information passed through from pass.py
    Depacketizes the information
    passes it to Spool.py
    Additionally reads .json parameters from modem 2, organizes it,
    and sends it to spool.py

What it does NOT do is decode payloads. The bytes that came off the link go
into the spool unchanged, and the gateway decodes them on the server. Three
reasons, in order of how much they matter:

1. If the decoder has a bug, the raw bytes are still in the spool and on the
   server, so the fix is a redeploy of the gateway and a replay. Decode at
   ingest and the bug is baked into the only copy you have.
2. The Pi is a courier, not a warehouse (Integration Guide §3.3). Every cycle
   spent parsing here is a cycle not available to the video path, which on a
   CM5 has no hardware encoder to fall back on.
3. Raw is smaller than JSON, and gzip closes most of the remaining gap anyway.

This is the open "packed BLOB vs. JSON" question resolved toward BLOB. If you
want the other answer, it is a two-line change here — decode and re-encode as
JSON before append() — and the gateway already handles both, because
`src` is what selects the decoder either way.

Run:  python3 receiver.py
Stop: SIGINT / SIGTERM — the staging buffer is flushed on the way out.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import sys
import threading
import time

from scapy.all import sniff

import telemetry_wire as wire
from spool import Spool
from telemetry_packet import PORT, ACTIVE as TelemetryPacket

log = logging.getLogger("receiver")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

SPOOL_PATH = os.environ.get("SPOOL_PATH", "/var/lib/telemetry/spool.db")
IFACE = os.environ.get("RX_IFACE", "eth0")
LUMA_STATUS_URL = os.environ.get("LUMA_STATUS_URL", "http://192.168.102.101/api/status.json")
LUMA_POLL_S = float(os.environ.get("LUMA_POLL_S", "5"))
MAX_ROWS = int(os.environ.get("SPOOL_MAX_ROWS", "500000"))

# The staging window, stated as one sentence so the trade is explicit:
# "we may lose up to one second, or fifty readings, whichever comes first."
FLUSH_N = int(os.environ.get("FLUSH_N", "50"))
FLUSH_S = float(os.environ.get("FLUSH_S", "1.0"))


# ---------------------------------------------------------------------------
# Staging buffer
# ---------------------------------------------------------------------------


class Collector:
    """Batches readings so the capture path never waits on the SD card.

    The sniff callback runs on the thread libpcap hands packets to. A stalled
    write there is a dropped packet, and on a Pi the card can stall for tens
    of milliseconds under load. So on_reading() only appends to a list.

    The batching also buys something less obvious: one fsync per second
    instead of one per reading. That is what makes `synchronous=NORMAL` a
    choice rather than a necessity — at this write rate FULL would also be
    affordable, and you can raise it if you decide the last few seconds
    before a power cut are worth the extra card wear.
    """

    def __init__(self, spool: Spool, flush_n: int = FLUSH_N, flush_s: float = FLUSH_S):
        self._spool = spool
        self._pending: list[tuple[int, str, str, bytes]] = []
        self._lock = threading.Lock()
        self._last_flush = time.monotonic()
        self.flush_n, self.flush_s = flush_n, flush_s
        self.received_total = 0
        self.dropped_unknown_total = 0

    def on_reading(self, acq_ns: int, src: str, link: str, payload: bytes) -> None:
        with self._lock:
            self._pending.append((acq_ns, src, link, payload))
            self.received_total += 1
            due = (
                len(self._pending) >= self.flush_n
                or time.monotonic() - self._last_flush >= self.flush_s
            )
        if due:
            self.flush()

    def flush(self) -> None:
        with self._lock:
            if not self._pending:
                self._last_flush = time.monotonic()
                return
            batch, self._pending = self._pending, []
            self._last_flush = time.monotonic()
        try:
            self._spool.append_many(batch)
        except Exception:
            # Losing the batch is bad; losing the process is worse, because
            # then we lose every batch after it too. Log loudly and continue.
            log.exception("spool append failed, %d readings lost", len(batch))


# ---------------------------------------------------------------------------
# Packet handling
# ---------------------------------------------------------------------------


def _capture_ns(pkt) -> int:
    """The acquisition timestamp, taken ONCE, here.

    Preference order:

    1. `t_acq_ns` from the packet header, if the V2 format is enabled — the
       subsea node's own time, which excludes link transit.
    2. libpcap's capture timestamp. This is applied in the kernel when the
       frame arrives, so it excludes Python scheduling jitter — which can be
       tens of milliseconds on a loaded Pi and would otherwise show up as
       noise in your sample spacing.
    3. `time.time_ns()`, if scapy gave us nothing usable.

    Whatever it returns is written to the spool and never recomputed. A retry
    must produce the same timestamp or it produces a different InfluxDB point,
    and at-least-once delivery quietly becomes duplicated data.
    """
    t_hdr = getattr(pkt, "t_acq_ns", 0) or 0
    if t_hdr and wire.clock_is_plausible(t_hdr):
        return int(t_hdr)
    try:
        return int(pkt.time * 1_000_000_000)
    except (TypeError, ValueError, AttributeError):
        return time.time_ns()


def make_handler(collector: Collector):
    def handle_packet(pkt):
        if TelemetryPacket not in pkt:
            return
        tp = pkt[TelemetryPacket]
        payload = bytes(tp.data_payload)

        src = wire.SRC_FOR_REMOTE_TYPE.get(tp.data_type)
        if src is None:
            # Not spooled: there is no decoder for it on the server either, so
            # it would occupy a row only to be quarantined. Counted, because a
            # pipeline that discards data must say so out loud.
            collector.dropped_unknown_total += 1
            log.warning("seq=%s unknown data_type %#04x", tp.seq_num, tp.data_type)
            return

        # payload_len is attacker-free here but not bug-free: a truncated
        # frame gives a short payload, and it is cheaper to notice now than to
        # quarantine it on the server an hour later.
        if tp.payload_len != len(payload):
            log.warning(
                "seq=%s payload_len=%d but %d bytes present; spooling anyway",
                tp.seq_num,
                tp.payload_len,
                len(payload),
            )

        collector.on_reading(_capture_ns(pkt), src, "optical", payload)

    return handle_packet


# ---------------------------------------------------------------------------
# Surface-side LUMA status
# ---------------------------------------------------------------------------


def luma_poller(collector: Collector, stop: threading.Event) -> None:
    """Poll the surface modem's own status JSON into the spool.

    Tagged `surface.luma`, which is what stops it being written to InfluxDB
    as though it came from the subsea unit — Integration Guide defect 7.4,
    made structurally impossible rather than merely fixed.

    `link` is "local" because this reading did not traverse a modem link. It
    is measured AT the surface node, not delivered TO it, and conflating the
    two makes "how much data arrived over optical" unanswerable.
    """
    import urllib.error
    import urllib.request

    while not stop.is_set():
        started = time.monotonic()
        try:
            with urllib.request.urlopen(LUMA_STATUS_URL, timeout=3) as r:
                if r.status == 200:
                    body = r.read()
                    json.loads(body)  # fail here, not on the server
                    collector.on_reading(time.time_ns(), wire.SRC_SURFACE_OPTICAL, "local", body)
                else:
                    log.warning("luma status HTTP %s", r.status)
        except (urllib.error.URLError, OSError, ValueError) as e:
            log.warning("luma status poll failed: %s", e)
        stop.wait(max(0.0, LUMA_POLL_S - (time.monotonic() - started)))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO"),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    # Refuse to stamp data until the clock is believable. Everything
    # downstream treats the timestamp as the point's identity, so a Pi that
    # boots at the epoch and starts recording is not producing slightly-wrong
    # data — it is producing data that will overwrite good records when the
    # clock jumps forward and the same series is written again.
    wire.wait_for_clock(timeout_s=float(os.environ.get("CLOCK_TIMEOUT_S", "300")), log=log)

    spool = Spool(SPOOL_PATH, max_rows=MAX_ROWS)
    collector = Collector(spool)
    stop = threading.Event()

    def _shutdown(signum, _frame):
        log.info("signal %s; flushing", signum)
        stop.set()
        collector.flush()
        spool.close()
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)

    threading.Thread(target=luma_poller, args=(collector, stop), daemon=True).start()

    # A timer flush, so a quiet link still commits what it has. Without it the
    # size trigger alone can hold the last few readings indefinitely.
    def _ticker():
        while not stop.wait(collector.flush_s):
            collector.flush()

    threading.Thread(target=_ticker, daemon=True).start()

    log.info("sniffing %s udp port %d -> %s", IFACE, PORT, SPOOL_PATH)
    try:
        sniff(iface=IFACE, prn=make_handler(collector), filter=f"udp port {PORT}", store=0)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        collector.flush()
        spool.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

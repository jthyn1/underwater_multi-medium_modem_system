# The delivery path: surface Pi → HaLow → InfluxDB

This covers everything from *"a packet arrived at the surface Pi"* to *"that
reading is durable in InfluxDB."* It slots underneath the existing
`scapyWrapper.py` / `Pollers/` code without changing the wire format between
the two Pis.

## Files

| File | Runs on | Job |
|---|---|---|
| `telemetry_wire.py` | **both** | The codec. Payload bytes → InfluxDB fields → line protocol. Deploy identical copies. |
| `telemetry_packet.py` | surface Pi (and subsea, optionally) | The Scapy layer, defined once instead of twice. |
| `spool.py` | surface Pi | SQLite store-and-forward. Now with `release()` and quarantine. |
| `receiver.py` | surface Pi | Sniff → spool. Replaces the print-only version. |
| `forwarder.py` | surface Pi | Spool → HTTP → gateway. Claim, send, await ack, mark delivered. |
| `gateway.py` | server | HTTP → validate → InfluxDB → ack. |
| `test_chaos.py` | anywhere | Adversarial-link soak. Phase 3 of the textbook's build order. |
| `test_gateway.py` | anywhere | Real gateway against a fake InfluxDB. Phase 1. |

Nothing here imports anything the node it runs on doesn't already need.
`telemetry_wire.py` in particular is standard-library only, which is what lets
the same file live on a Pi with pyserial and a server with an InfluxDB client.

## Data flow

```
  Teensy ─UART─▶ scapyWrapper.py ─optical/UDP:5555─▶ receiver.py ──▶ [ spool.db ]
                                                     surface LUMA ──▶     │
                                                                          │ claim
                                                                          ▼
                                                              forwarder.py ──HaLow──▶
                                                                                    │
   InfluxDB ◀──204──  gateway.py  ◀── POST /ingest (gzip JSON) ──────────────────────┘
                          │
                          └─── 200 {acked, failed} ──▶ forwarder marks delivered
```

The ordering is never reversed: **write → confirm durability → ack → mark
delivered → trim.** Every crash point is either "no loss" or "harmless
duplicate," and the duplicate is harmless only because the acquisition
timestamp is carried through unchanged and never re-stamped.

## Running it

```bash
# --- surface Pi ---
export SPOOL_PATH=/var/lib/telemetry/spool.db
export RX_IFACE=eth0
export LUMA_STATUS_URL=http://192.168.102.101/api/status.json
sudo -E python3 receiver.py          # needs CAP_NET_RAW for sniff()

export GATEWAY_URL=http://<server>:8086/ingest
export GATEWAY_TOKEN=<shared secret>
python3 forwarder.py

# --- server ---
export INFLUX_URL=http://localhost:8181
export INFLUX_DB=telemetry
export INFLUX_TOKEN=<influx token>
export INGEST_TOKEN=<same shared secret>
python3 gateway.py
```

Every knob is an environment variable with a working default; there is no
config file to get out of sync. `HEALTH_EVERY_S`, `TRIM_EVERY_S`,
`MAX_ATTEMPTS`, `BATCH_MIN/START/MAX`, `DELIVERED_RETENTION_S` are the ones
worth tuning after the first soak.

Pin the InfluxDB image tag explicitly. `latest` starts pointing at v3 Core on
15 September 2026, and a database that changes major version because a
container restarted is not a fun morning.

## Testing, in the order the textbook's §39 asks for

```bash
python3 spool.py         # Phase 0 — spool alone, including crash recovery
python3 test_gateway.py  # Phase 1 — gateway + fake InfluxDB, isolation, ack ordering
python3 test_chaos.py    # Phase 3 — 10% drops, stalls, partial acks, lost acks
```

`test_chaos.py` asserts the invariant that matters: *every reading written to
the spool appears at the destination, or is counted as dropped.* The case that
separates a correct implementation from one that merely works is in there —
the server writes the data and then loses the acknowledgement.

Phase 1's manual gate, by hand:

```bash
DRY_RUN=1 python3 gateway.py &        # prints line protocol, stores nothing
curl -s -X POST localhost:8086/ingest -H 'Content-Type: application/json' -d '{
  "batch_id":"t1","node":"surface",
  "readings":[{"id":1,"acq_ns":1786000000000000000,
               "src":"surface.luma","link":"local",
               "payload_b64":"eyJzbnIiOiA0Mn0="}]}'
# {"batch_id": "t1", "acked": [1], "failed": []}
```

Then the important one: send the identical batch to a **real** InfluxDB twice
and confirm the point count does not change. That is the idempotency check the
entire retry strategy rests on.

## Changes to your existing files

**`Pollers/decoder.py` — one line.** `TIME` is defined and `time_decoder` is
written, but `DECODERS[TIME]` is never assigned, so every TIME packet prints
"unknown data type" at the receiver:

```python
DECODERS[TIME] = time_decoder      # currently missing
```

**`scapyWrapper.py` — optional, two lines.** Replace the `class
TelemetryPacket` block and the `bind_layers` call with:

```python
from telemetry_packet import TelemetryPacket, PORT
```

so both nodes cannot drift. Also note `main()` starts `qSensor` and `qJSON`
but never `qTime`, and `txProtocol()` never returns, so the `while True`
around it in `main()` is dead code.

**`Pollers/JSONparsing.py` — worth a look.** `readJson()` wraps its request in
`while True` and only returns on HTTP 200. If the LUMA answers 503, that loop
spins at full CPU with no sleep and no exit. A `time.sleep(1)` and a retry
budget would fix it.

Nothing else changes. `serialparsing.py` and the transmit path are untouched.

## Decisions I made, and how to reverse them

**Spool payloads stay as raw bytes; the gateway decodes.** Your open question
was BLOB vs. JSON, leaning JSON. I went BLOB, for one reason above the others:
if the decoder has a bug, the original bytes are still in the spool and on the
server, so the fix is a gateway redeploy and a replay. Decode at ingest and
the bug is baked into the only copy you have. To reverse: decode in
`receiver.py` before `append()` and emit JSON — `src` selects the decoder
either way, so the gateway needs no change.

**Push, not pull.** Both your documents lean this way and the reasoning holds:
the Pi controls its own drain rate, which is what makes catch-up mode work,
and it works through an AP you don't control.

**`src` is `"<node>.<component>"`.** So the node is baked into the row at
capture time. This is Integration Guide defect 7.4 — subsea LUMA status
written as though it were the surface unit's — made structurally impossible
rather than merely fixed.

**The gateway writes line protocol over raw HTTP, not through a client
library.** Client libraries commonly buffer asynchronously, and a gateway that
acks when `write()` returns is acking its own RAM. A 204 from
`/api/v3/write_lp` with `no_sync=false` means the data is in InfluxDB's WAL.
It also means you can reproduce any write with `curl`.

**Poison rows are quarantined, not dropped.** State 3. They stop blocking the
queue but stay for post-mortem, and `trim()` sacrifices them right after
delivered rows. `UPDATE spool SET state=0 WHERE state=3` puts them all back
once you've fixed whatever rejected them.

## Open questions I could not resolve from the code

**1. The timestamp. This is the important one.**

`dataFormat` has ten fields and no time field — the `<hfffffcfcfh` variant with
a trailing time appears in your notes but not in `serialparsing.py`. The `TIME`
packet type sends `time.time()` as a separate packet, which cannot be
associated with the sample next to it.

So right now `receiver.py` stamps `acq_ns` at the surface, once, from libpcap's
kernel capture timestamp. That is the fallback §34.5 sanctions, and its cost is
that link transit delay is folded into your data as jitter.

The fix is in `telemetry_packet.py`: `TelemetryPacketV2` adds an 8-byte
`t_acq_ns` to the header, so it applies to every data type rather than being
buried in one payload's struct. `receiver.py` already prefers it when present
and plausible. Flip `CARRY_ACQ_TIME = True` **on both nodes together** — it is
a wire format change — and have `scapyWrapper.py` fill it from the moment the
Teensy line was read.

**2. Are lat/lon magnitudes or signed values?** `telemetry_wire.py` assumes the
NMEA convention: unsigned magnitude plus a hemisphere character, so `S` and `W`
make the value negative. If the Teensy already emits a signed value *and* a
direction character, every southern/western position gets double-negated. And
if it emits raw NMEA `ddmm.mmmm` rather than decimal degrees, every position
you record is wrong in a way that looks entirely plausible. Check one fix
against a known location before you trust any of it.

**3. Units for two fields.** `altitude_raw` and `speed_raw` are named without
units on purpose, because I don't know them and your own §14.2 rule says the
name is the documentation. Confirm against the Teensy sketch and rename in the
`FIELD_TYPES` table before the first production write — after data exists,
renaming gives you two series meaning the same thing.

**4. `/dev/ttyACM0` or `/dev/ttyAMA0`?** `serialparsing.py` opens `ttyACM0`
(USB CDC); your notes say `ttyAMA0` (the GPIO UART, which is what the Teensy
pin 34/35 → Pi pin 8/10 wiring implies). Both can be true if the Teensy is also
plugged in over USB, but only one is the path you're deploying.

**5. Token order in `parse()`.** The code reads `tokens[2]` as pressure and
`tokens[3]` as altitude while the dataclass declares altitude first. That is
consistent as written — but it means the Teensy must emit
`water,temp,pressure,alti,depth,...`, and it's worth confirming against the
sketch, because getting it wrong swaps two fields silently.

**6. The acoustic path isn't wired in.** The spool takes `link='acoustic'` and
the gateway validates it, but nothing produces those rows yet — `scapyWrapper`
is optical-only today. When the Popoto path lands, it appends to the same
spool with the same `src` vocabulary and nothing downstream changes.

## What to watch once it's running

Set the Grafana alert on **absence** first: "no `environment` points from
node=subsea for 5 minutes." When the link dies, silence is the only signal
that survives — everything in `pipeline_health` is diagnosis, not detection,
because it travels through the same link that just failed.

After that: `spool_pending` above 60% of `max_rows`, any increase in
`rows_dropped_total`, and `spool_quarantined` above zero.

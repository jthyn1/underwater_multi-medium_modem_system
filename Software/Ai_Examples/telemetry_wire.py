"""telemetry_wire.py — the codec both ends agree on.

Deploy this file VERBATIM on the surface Pi and on the server. It is the
`common/` principle from the Integration Guide §9.5: the transmitter and the
receiver cannot drift on field names if there is only one file that names
them. Defect 7.8 in that guide is exactly the bug this file exists to make
impossible.

It deliberately imports nothing outside the standard library. `serial` and
`requests` are not available on the server, and `influxdb` is not available
on the Pi, so anything either end needs must live here and depend on neither.

Three things live here:

1. The `src` vocabulary — who produced a row, and therefore how to decode it.
2. The decoders — bytes off the wire to a dict of InfluxDB fields.
3. The line-protocol builder — that dict to the text InfluxDB ingests.
"""

from __future__ import annotations

import json
import math
import struct
from dataclasses import dataclass

# ---------------------------------------------------------------------------
# 1. Data types and the `src` vocabulary
# ---------------------------------------------------------------------------
#
# The numeric tags mirror Pollers/decoder.py and travel in TelemetryPacket's
# data_type byte. They exist on the wire.

SENSORS = 0x01
OPTICALSTATUS = 0x02
TIME = 0x03
HEALTH = 0x04          # new: the pipeline reporting on itself (Textbook §35)

# The `src` string is what goes in the spool and what the gateway dispatches
# on. It is "<node>.<component>", and that shape is load-bearing: the node is
# baked into the row at the moment of capture, so a status blob relayed from
# the subsea LUMA can never be written to InfluxDB tagged as the surface one.
# That mislabelling is Integration Guide defect 7.4, and it was a data-
# correctness bug, not a cosmetic one. Encoding the node in `src` makes it a
# property of the row rather than a property of whoever writes the point.

SRC_SUBSEA_SENSORS = "subsea.teensy"
SRC_SUBSEA_OPTICAL = "subsea.luma"
SRC_SUBSEA_CLOCK = "subsea.clock"
SRC_SURFACE_OPTICAL = "surface.luma"
SRC_SURFACE_PIPELINE = "surface.pipeline"

# Which src a sniffed packet becomes, by its data_type byte. Packets arriving
# over the optical link came from the subsea node by definition.
SRC_FOR_REMOTE_TYPE = {
    SENSORS: SRC_SUBSEA_SENSORS,
    OPTICALSTATUS: SRC_SUBSEA_OPTICAL,
    TIME: SRC_SUBSEA_CLOCK,
}

# Tag allowlists. Chapter 31.4: "the allowlist is your cardinality bound,
# expressed in code." Anything not on these lists is a permanent failure at
# the gateway, not a new series.
VALID_SRC = frozenset(
    {
        SRC_SUBSEA_SENSORS,
        SRC_SUBSEA_OPTICAL,
        SRC_SUBSEA_CLOCK,
        SRC_SURFACE_OPTICAL,
        SRC_SURFACE_PIPELINE,
    }
)
VALID_LINK = frozenset({"acoustic", "optical", "local"})
VALID_NODE = frozenset({"subsea", "surface"})

# Which measurement each src writes into. Integration Guide §14.2.
MEASUREMENT_FOR_SRC = {
    SRC_SUBSEA_SENSORS: "environment",
    SRC_SUBSEA_OPTICAL: "optical_status",
    SRC_SURFACE_OPTICAL: "optical_status",
    SRC_SUBSEA_CLOCK: "node_health",
    SRC_SURFACE_PIPELINE: "pipeline_health",
}


# ---------------------------------------------------------------------------
# 2. The sensor struct
# ---------------------------------------------------------------------------
#
# This MUST stay identical to Pollers/serialparsing.py. The safest way to keep
# it that way is to delete the copy there and have serialparsing import from
# here — see the note in the README. Until then, test_wire.py asserts they
# agree.

SENSOR_STRUCT = "<hfffffcfcf"
SENSOR_SIZE = struct.calcsize(SENSOR_STRUCT)  # 32 bytes


@dataclass
class dataFormat:
    waterVal: int
    tempVal: float
    altiVal: float
    pressureVal: float
    depthVal: float
    latVal: float
    latDirVal: bytes
    lonVal: float
    lonDirVal: bytes
    speedVal: float


# ---------------------------------------------------------------------------
# The field map — the one place a field's name and type are decided
# ---------------------------------------------------------------------------
#
# Chapter 31.5 asks for exactly this: one table, one function, one place.
#
# READ THIS BEFORE THE FIRST PRODUCTION WRITE. Renaming a field after data
# exists gives you two series that mean the same thing and no clean way to
# query across the boundary, so the names below are cheap to change today and
# expensive to change in a month.
#
# Three names are deliberately unit-free because the unit is not yet
# confirmed from the Teensy firmware:
#
#   altitude_raw  — altimeter reading. Metres? Centimetres? Range counts?
#   speed_raw     — GPS speed. Knots is the NMEA default; m/s is what you
#                   probably want to plot.
#   latitude_deg  — the SIGN is applied from latDir below, but the MAGNITUDE
#                   is passed through untouched. If the Teensy emits raw NMEA
#                   (ddmm.mmmm) rather than decimal degrees, every position
#                   you record is wrong in a way that looks plausible. Check
#                   one fix against a known location before you trust it.
#
# The Integration Guide's own §14.2 rule applies: the name is the
# documentation, so do not put a unit in a name you have not verified.

FIELD_TYPES = {
    # environment
    "water_detect": int,
    "temperature_c": float,
    "altitude_raw": float,
    "pressure_mbar": float,
    "depth_m": float,
    "latitude_deg": float,
    "longitude_deg": float,
    "speed_raw": float,
    "gps_valid": int,
    "seq": int,
    "transit_latency_s": float,
    # node_health (clock)
    "tx_wall_clock_s": float,
    "clock_skew_s": float,
    # everything else (optical_status, pipeline_health) is coerced to float
    # by _coerce_number below, because those payloads are open-ended.
}

# A value that is not finite cannot be represented in line protocol and will
# be rejected by InfluxDB. Catch it here rather than there.
_MAX_TAG_LEN = 64


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


# ---------------------------------------------------------------------------
# 3. Decoders: payload bytes -> dict of InfluxDB fields
# ---------------------------------------------------------------------------


class DecodeError(ValueError):
    """The payload is malformed. Permanent — retrying identical bytes cannot
    help. The gateway turns this into a `failed` entry, not a 5xx."""


def decode_sensors(payload: bytes) -> dict:
    if len(payload) != SENSOR_SIZE:
        raise DecodeError(f"sensor payload is {len(payload)} bytes, expected {SENSOR_SIZE}")
    try:
        d = dataFormat(*struct.unpack(SENSOR_STRUCT, payload))
    except struct.error as e:
        raise DecodeError(f"struct.unpack: {e}") from e

    lat_dir = d.latDirVal.decode("ascii", "replace").upper()
    lon_dir = d.lonDirVal.decode("ascii", "replace").upper()

    # Sign convention: south and west are negative. A direction character that
    # is neither of the expected pair means the GPS had no fix and the Teensy
    # emitted a placeholder, so the position is recorded as unusable rather
    # than as a confident (0, 0) off the coast of Ghana.
    valid = lat_dir in ("N", "S") and lon_dir in ("E", "W")
    lat = -d.latVal if lat_dir == "S" else d.latVal
    lon = -d.lonVal if lon_dir == "W" else d.lonVal

    fields = {
        "water_detect": int(d.waterVal),
        "temperature_c": float(d.tempVal),
        "altitude_raw": float(d.altiVal),
        "pressure_mbar": float(d.pressureVal),
        "depth_m": float(d.depthVal),
        "speed_raw": float(d.speedVal),
        "gps_valid": 1 if valid else 0,
    }
    if valid:
        fields["latitude_deg"] = float(lat)
        fields["longitude_deg"] = float(lon)

    # Drop non-finite values rather than shipping them. A NaN in a struct
    # usually means the Teensy sent "nan" for a sensor that isn't connected.
    return {k: v for k, v in fields.items() if _finite(v)}


def decode_optical(payload: bytes) -> dict:
    """The LUMA status JSON.

    The exact key set is NOT confirmed — the Integration Guide's Phase 0 says
    in as many words not to trust the manual here. So this is permissive: every
    numeric leaf becomes a float field, with a rename applied where the guide's
    §14.2 schema names a field. Unknown numeric keys pass through under a
    sanitized name rather than being dropped, because a field costs nothing and
    a silently missing metric costs an afternoon.

    `fields_ok` counts what made it through, so a firmware change that renames
    half the payload shows up as a step change on a graph instead of as
    nothing at all (Integration Guide defect 6.11).
    """
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DecodeError(f"optical status is not JSON: {e}") from e
    if not isinstance(obj, dict):
        raise DecodeError("optical status JSON is not an object")

    out = {}
    for k, v in _flatten(obj).items():
        name = OPTICAL_RENAME.get(k, _sanitize_key(k))
        if name is None:  # explicitly suppressed
            continue
        f = _coerce_number(v)
        if f is not None and math.isfinite(f):
            out[name] = f

    # §14.2: loss_ratio is nullable ON PURPOSE. Writing 0.0 when no packets
    # arrived makes a dead link and a perfect link look identical on every
    # panel you will ever build. A gap is the honest representation.
    recv = out.get("packets_received_per_sec")
    lost = out.get("packets_lost_per_sec")
    if recv is not None and lost is not None and (recv + lost) > 0:
        out["loss_ratio"] = lost / (recv + lost)

    out["fields_ok"] = float(len(out))
    return out


# Best-guess rename map. Left-hand side is whatever the LUMA actually emits
# (dotted for nested keys); right-hand side is the §14.2 schema name. Add to
# this once Phase 0 has captured a real status.json — the pass-through path
# means an unmapped key still lands, it just lands under its own name.
OPTICAL_RENAME = {
    "snr": "snr_metric",              # 0-255 dimensionless, NOT dB (§11.1)
    "signal_strength": "signal_strength",
    "signal_amplitude": "signal_amplitude",
    "noise_amplitude": "noise_amplitude",
    "gain": "gain",
    "nb_additional_rcv": "receivers_extra",
    "speed": "optical_speed_mbits",
    "temperature": "temperature_c",
    "volt_board": "volt_board",
    "volt_board_min": "volt_board_min",
    "throughput": "throughput_kbits_sec",
    "crc_errors": "crc_errors_per_sec",
    "packets_received": "packets_received_per_sec",
    "packets_lost": "packets_lost_per_sec",
    "status_electronics": "electronics_ok",
}


def decode_time(payload: bytes) -> dict:
    """The TIME packet: the subsea node's wall clock as an ASCII float.

    On its own this cannot timestamp anything — it arrives as a separate
    packet with no way to associate it with the sample next to it. What it
    IS good for is measuring the skew between the two clocks, which is the
    number that tells you whether the subsea timestamp would have been worth
    trusting. See the README for the header-field change that would let the
    sample carry its own time.
    """
    try:
        tx_s = float(payload.decode("ascii").strip())
    except (UnicodeDecodeError, ValueError) as e:
        raise DecodeError(f"time payload is not a float: {e}") from e
    if not math.isfinite(tx_s):
        raise DecodeError("time payload is not finite")
    return {"tx_wall_clock_s": tx_s}


def decode_health(payload: bytes) -> dict:
    """Pipeline self-metrics. JSON object of name -> number."""
    try:
        obj = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        raise DecodeError(f"health payload is not JSON: {e}") from e
    if not isinstance(obj, dict):
        raise DecodeError("health payload is not an object")
    out = {}
    for k, v in obj.items():
        f = _coerce_number(v)
        if f is not None and math.isfinite(f):
            out[_sanitize_key(k)] = f
    if not out:
        raise DecodeError("health payload has no numeric fields")
    return out


DECODERS = {
    SRC_SUBSEA_SENSORS: decode_sensors,
    SRC_SUBSEA_OPTICAL: decode_optical,
    SRC_SURFACE_OPTICAL: decode_optical,
    SRC_SUBSEA_CLOCK: decode_time,
    SRC_SURFACE_PIPELINE: decode_health,
}


def _flatten(obj: dict, prefix: str = "") -> dict:
    """One level of dotted flattening, so {"radio": {"snr": 12}} becomes
    {"radio.snr": 12}. InfluxDB fields are flat; JSON status blobs usually
    are not."""
    out = {}
    for k, v in obj.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(_flatten(v, key + "."))
        else:
            out[key] = v
    return out


def _sanitize_key(k: str) -> str:
    """Field keys must survive line protocol and be typable in SQL."""
    safe = "".join(c if (c.isalnum() or c == "_") else "_" for c in str(k).lower())
    return safe.strip("_")[:64] or "unnamed"


def _coerce_number(v):
    """Everything open-ended becomes a float. Integration Guide §14.3: pick a
    type per field and enforce it at the writer, because InfluxDB 1.x/2.x fix
    a field's type on first write and silently reject conflicting ones later.
    v3 is more forgiving, but a pipeline that only works on v3 is a pipeline
    with one fewer place to run."""
    if isinstance(v, bool):
        return 1.0 if v else 0.0
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        try:
            return float(v.strip())
        except ValueError:
            return None
    return None


# ---------------------------------------------------------------------------
# 4. Clock plausibility (Textbook §34)
# ---------------------------------------------------------------------------
#
# A wrong clock does not produce wrong metadata, it produces wrong DATA:
# same tags plus same timestamp equals same point, so a backward clock step
# overwrites good records with bad ones. Both ends check.

MIN_PLAUSIBLE_NS = 1_700_000_000_000_000_000  # ~Nov 2023
MAX_PLAUSIBLE_NS = 4_102_444_800_000_000_000  # ~Jan 2100


def clock_is_plausible(ns: int | None = None) -> bool:
    import time as _time

    ns = _time.time_ns() if ns is None else ns
    return MIN_PLAUSIBLE_NS < ns < MAX_PLAUSIBLE_NS


def wait_for_clock(timeout_s: float = 120.0, log=None) -> None:
    """Block until the wall clock is plausible, or give up loudly.

    systemd's `After=time-sync.target` is the better first defence and should
    also be set; this is the belt to those braces, because that target is
    satisfied by a time source being *configured*, not by the time being
    *right*.
    """
    import time as _time

    deadline = _time.monotonic() + timeout_s
    while not clock_is_plausible():
        if _time.monotonic() > deadline:
            raise RuntimeError("clock never became plausible; refusing to stamp data")
        if log:
            log.warning("waiting for clock sync before accepting readings")
        _time.sleep(5)


# ---------------------------------------------------------------------------
# 5. Line protocol
# ---------------------------------------------------------------------------
#
# Building the text ourselves rather than reaching for a client library is a
# deliberate choice: it is about twenty lines, it makes the bytes on the wire
# inspectable with `curl`, and it removes any question about whether a client
# has buffered a write it told us it performed (Textbook §31.2). Appendix C
# of the textbook is the reference for the syntax below.


def _esc_tag(s: str) -> str:
    """Tag keys, tag values, field keys: escape comma, equals, space."""
    return str(s).replace("\\", "\\\\").replace(",", r"\,").replace("=", r"\=").replace(" ", r"\ ")


def _esc_measurement(s: str) -> str:
    """Measurements: escape comma and space, but NOT equals."""
    return str(s).replace("\\", "\\\\").replace(",", r"\,").replace(" ", r"\ ")


def _esc_str_field(s: str) -> str:
    return str(s).replace("\\", "\\\\").replace('"', r"\"")


def format_field(key: str, value) -> str:
    """One `key=value` pair, typed per FIELD_TYPES.

    The `i` suffix marks an integer. Without it InfluxDB stores a float, and
    a field that is sometimes int and sometimes float is the §18.4 type
    conflict that silently drops points on v1/v2.
    """
    want = FIELD_TYPES.get(key)
    if want is int or (want is None and isinstance(value, int) and not isinstance(value, bool)):
        return f"{_esc_tag(key)}={int(value)}i"
    if isinstance(value, bool):
        return f"{_esc_tag(key)}={'t' if value else 'f'}"
    if isinstance(value, str):
        return f'{_esc_tag(key)}="{_esc_str_field(value)}"'
    f = float(value)
    if not math.isfinite(f):
        raise DecodeError(f"field {key} is not finite: {value}")
    return f"{_esc_tag(key)}={f!r}"


def to_line(measurement: str, tags: dict, fields: dict, ts_ns: int) -> str:
    """One point, one line. Timestamp in nanoseconds — always explicit.

    Never let InfluxDB apply its own arrival time. That is the §19.3 rule:
    the timestamp is a property of the reading, and a retry that produces a
    different timestamp produces a different point, which is how at-least-once
    delivery turns into duplicated data instead of an overwrite.
    """
    if not fields:
        raise DecodeError(f"{measurement}: no fields to write")
    tag_part = "".join(
        f",{_esc_tag(k)}={_esc_tag(v)}" for k, v in sorted(tags.items()) if v not in (None, "")
    )
    field_part = ",".join(format_field(k, v) for k, v in sorted(fields.items()))
    return f"{_esc_measurement(measurement)}{tag_part} {field_part} {int(ts_ns)}"


def validate_tags(tags: dict) -> None:
    for k, v in tags.items():
        if not isinstance(v, str) or not v or len(v) > _MAX_TAG_LEN:
            raise DecodeError(f"tag {k} has an unusable value: {v!r}")
        if any(c in v for c in "\n\r"):
            raise DecodeError(f"tag {k} contains a newline")


def decode_row(src: str, link: str, payload: bytes) -> tuple[str, dict, dict]:
    """The gateway's single entry point: a spool row to (measurement, tags, fields).

    Raises DecodeError for anything the row can never recover from. Everything
    it raises on is a PERMANENT failure by construction — same bytes, same
    outcome, forever — which is what lets the gateway answer `failed` instead
    of `5xx` and keep the queue moving.
    """
    if src not in VALID_SRC:
        raise DecodeError(f"unknown src: {src!r}")
    if link not in VALID_LINK:
        raise DecodeError(f"unknown link: {link!r}")
    node, _, component = src.partition(".")
    if node not in VALID_NODE:
        raise DecodeError(f"unknown node in src: {src!r}")

    fields = DECODERS[src](payload)
    if not fields:
        raise DecodeError(f"{src}: decoded to no usable fields")

    tags = {"node": node, "link": link}
    if MEASUREMENT_FOR_SRC[src] == "optical_status":
        tags["variant"] = "luma_x"  # static per §14.2; make it config if you mix units
    validate_tags(tags)
    return MEASUREMENT_FOR_SRC[src], tags, fields

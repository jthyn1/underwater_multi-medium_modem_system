"""telemetry_packet.py — the Scapy layer, defined once.

`scapyWrapper.py` and `receiver.py` both need this class, and they must agree
on it byte for byte. Today receiver.py imports it from scapyWrapper, which
works but drags pyserial and requests onto the surface Pi as a side effect of
an import that only wanted a struct definition. Pulling it into its own module
costs one line at each end and removes the coupling.

To adopt: delete the `class TelemetryPacket` block from scapyWrapper.py and
replace it with

    from telemetry_packet import TelemetryPacket, PORT

The `bind_layers` call is here too, so it happens exactly once and identically
on both nodes.
"""

from scapy.all import (
    IntField,
    LongField,
    Packet,
    ShortField,
    StrLenField,
    UDP,
    XByteField,
    bind_layers,
)

PORT = 5555

# Set to True on BOTH nodes together, never one at a time — it changes the
# wire format. See README-delivery.md, "The timestamp question".
CARRY_ACQ_TIME = False


class TelemetryPacket(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]


class TelemetryPacketV2(Packet):
    """The version that carries its own acquisition time.

    Eight bytes, placed in the header so it applies uniformly to every
    data_type rather than being buried in one payload's struct. `t_acq_ns` is
    nanoseconds since the epoch, taken on the subsea node at the moment the
    Teensy line was read — not when the packet was built, and never restamped
    on a resend.

    Why this matters more than eight bytes suggests: the timestamp IS the
    point's identity in InfluxDB (Textbook §19), so whichever clock stamps it
    decides what your dataset means. Stamping at the surface folds the
    acoustic link's variable transit delay into the data as jitter. Stamping
    subsea removes that, at the cost of trusting a Pi with no RTC — which is
    why §34.5 recommends doing both: stamp the point with the surface time and
    keep the subsea time as a field, so the loss is reversible.
    """

    name = "TelemetryPacketV2"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        LongField("t_acq_ns", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]


ACTIVE = TelemetryPacketV2 if CARRY_ACQ_TIME else TelemetryPacket
bind_layers(UDP, ACTIVE, dport=PORT)

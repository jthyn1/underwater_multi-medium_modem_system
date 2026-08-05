#scapyWrapper.py
import serial
import time
import struct
from scapy.all import Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField
from scapy.all import bind_layers, sendp
from collections import namedtuple
from dataclasses import dataclass
import Pollers.JSONparsing as JSONparsing
import Pollers.serialparsing as serialparsing

# Defines telemetry packet
class TelemetryPacket(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]

def packetAppend(JSON_data, serial_data):
    # Combine the JSON and serial data into a single payload
    combined_payload = JSON_data + serial_data
    return combined_payload


# Defines port # and transmission protocol
PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

# Sends packed data to other pi ip through ethernet
def sensorPacket(payloadData, seq):
    data = b"sensor reading payload"
    pkt = (
        Ether(dst="AA:BB:CC:DD:EE:FF") #Rx MAC address
        / IP(src="192.168.102.100", dst="192.168.102.103")
        / UDP(sport=5000, dport=PORT)
        / TelemetryPacket(
            seq_num=seq,
            data_type=0x01,
            payload_len=len(payloadData),
            data_payload=payloadData,
        )
    )
    pkt.show()

    sendp(pkt, iface="eth0")

def main():
    seq = 0
    try:
        while True:
            seq = (seq + 1) & 0xFFFF
            JSON_data = JSONparsing.JSONParse()
            serial_data = serialparsing.serialParse()
            combined_payload = packetAppend(JSON_data, serial_data)
            sensorPacket(combined_payload, seq)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return

if __name__ == "__main__":
   main()

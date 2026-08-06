#scapyWrapper.py
import time
from scapy.all import Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField
from scapy.all import bind_layers, sendp
import Pollers.JSONparsing as JSONparsing
import Pollers.serialparsing as serialparsing
from Pollers.decoder import SENSORS, OPTICALSTATUS
import queue
import threading

# Defines telemetry packet
class TelemetryPacket(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]

tx_queue = queue.Queue()

# Queue sensor data
def qSensor(q):
    while True:
        q.put(serialparsing.serialMain())
        time.sleep(1)

# Queue JSON data
def qJSON(q):
    while True:
        q.put(JSONparsing.JSONmain())
        time.sleep(1)

# Defines port # and transmission protocol
PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

# Sends packed data to other pi ip through ethernet
def txProtocol():

    seq = 0

    while True:
        seq = (seq + 1) & 0xFFFF
        tag, payloadData = tx_queue.get()
        dataLabel = b"data payload"
        pkt = (
            Ether(dst="AA:BB:CC:DD:EE:FF") #Rx MAC address
            / IP(src="192.168.102.100", dst="192.168.102.103")
            / UDP(sport=5000, dport=PORT)
            / TelemetryPacket(
                seq_num=seq,
                data_type=tag,
                payload_len=len(payloadData),
                data_payload=payloadData,
            )
        )
        pkt.show()
        sendp(pkt, iface="eth0")
        tx_queue.task_done()

def main():

    threading.Thread(target=qSensor, args=(tx_queue,), daemon=True).start()
    threading.Thread(target=qJSON, args=(tx_queue,), daemon=True).start()

    try:
        while True:
            txProtocol()
    except KeyboardInterrupt:
        return

if __name__ == "__main__":
   main()

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

def qSensor(q, id_q):
    while True:
        q.put(serialparsing.serialParse())
        time.sleep(1)

def qJSON(q, id_q):
    while True:
        q.put(JSONparsing.JSONParse())
        time.sleep(1)



# Defines port # and transmission protocol
PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

# Sends packed data to other pi ip through ethernet

def dataType():
    while True:
        data = tx_queue.get()
        if isinstance(IDdata, serialparsing.dataFormat):
            return SENSORS
        elif isinstance(data, dict):
            return OPTICALSTATUS
        else:
            print("Unknown data type")
            return None

def txPacket(payloadData, seq):
    data = b"data payload"
    pkt = (
        Ether(dst="AA:BB:CC:DD:EE:FF") #Rx MAC address
        / IP(src="192.168.102.100", dst="192.168.102.103")
        / UDP(sport=5000, dport=PORT)
        / TelemetryPacket(
            seq_num=seq,
            data_type=dataType(),
            payload_len=len(payloadData),
            data_payload=payloadData,
        )
    )
    pkt.show()

    while True:
        sendp(pkt, iface="eth0")
        tx_queue.task_done()


def main():
    seq = 0
    threading.Thread(target=qSensor, args=(tx_queue,), daemon=True).start()
    threading.Thread(target=qJSON, args=(tx_queue,), daemon=True).start()
    try:
        while True:
            seq = (seq + 1) & 0xFFFF
            payload = tx_queue.get()
            txPacket(payload, seq)
    except KeyboardInterrupt:
        return

if __name__ == "__main__":
   main()

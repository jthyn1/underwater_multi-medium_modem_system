from scapy.all import sniff, Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField, bind_layers
from dataclasses import dataclass
import struct
import time 

class TelemetryPacket(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]
    
@dataclass
class dataFormat:
    waterVal: int
    tempVal: float
    altiVal: float
    pressureVal: float
    depthVal: float
    latVal: float
    latDirVal: chr
    lonVal: float
    lonDirVal: chr
    speedVal: float
    timeVal: int

PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)
Format = '<hfffffcfcfh'

def depacketize(data: bytes) -> dataFormat:
    return dataFormat(*struct.unpack(Format, data))

def handle_packet(pkt):
    if TelemetryPacket not in pkt:
        return
    tp = pkt[TelemetryPacket]
    data = bytes(tp.data_payload)
    expected_len = struct.calcsize(Format)
    if len(data) != expected_len:
        print(f"seq={tp.seq_num} bad length: received {len(data)}, expected {expected_len}")
        return
    print(f"seq= {tp.seq_num}, {depacketize(data)}")
       # print(f"seq={tp.seq_num} type={hex(tp.data_type)} data_payload={tp.data_payload}")
       
def main():
    sniff(iface="eth0", prn=handle_packet, filter=f"udp port {5555}")

if __name__ == "__main__":
   main()

    


#Receives the information passed through from pass.py
#Depacketizes the information
#passes it to Spool.py
<<<<<<< HEAD
#Additionally reads .json parameters from modem 2, organizes it, and sends it to spool.py
=======
#Additionally reads .json parameters from modem 2, organizes it, and sends it to spool.py
>>>>>>> 8089c604dd47e9a863bed194c99a1696f2ab5360

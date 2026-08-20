from scapy.all import sniff, Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField, bind_layers, AsyncSniffer, Raw
from queue import Queue, Full
from scapyWrapper import TelemetryPacket

PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

def handler(q: Queue, stats):
    def handle(pkt):
        if TelemetryPacket not in pkt:
            stats["non_telemetry"] += 1
            return
        tp = pkt[TelemetryPacket]
        if len(tp.data_payload) != tp.payload_len:
            stats["short_frame"] += 1
            return
        if Raw in tp:
            stats["long_frame"] += 1
            return
        try:
            q.put_nowait((tp.acq_ns, tp.mask, tp.link, tp.seq_num, bytes(tp.data_payload)))
        except Full:
            stats["backpressure"] += 1

    return handle

def startSniff(q, stats, port=5555):
    sniffer = AsyncSniffer(iface="eth0", filter=f"udp port {port}", prn=handler(q,stats),store=False)
    sniffer.start()
    return sniffer

    


#Receives the information passed through from pass.py
#Depacketizes the information
#passes it to Spool.py
#Additionally reads .json parameters from modem 2, organizes it, and sends it to spool.py

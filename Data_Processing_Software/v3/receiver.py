from scapy.all import sniff, Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField, bind_layers
from Pollers.decoder import DECODERS

class TelemetryPacket(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]

PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

def handle_packet(pkt):
    if TelemetryPacket not in pkt:
        return
    tp = pkt[TelemetryPacket]
    data = bytes(tp.data_payload)

    decoder = DECODERS.get(tp.data_type)
    if decoder is None:
        print(f"seq={tp.seq_num} unknown data type: {hex(tp.data_type)}")
        return
    
    if len(data) != decoder.size:
        print(f"seq={tp.seq_num} bad length: received {len(data)}, expected {decoder.size}")
        return
    print(f"seq= {tp.seq_num}, {decoder(data)}")
       # print(f"seq={tp.seq_num} type={hex(tp.data_type)} data_payload={tp.data_payload}")
       
def main():
    sniff(iface="eth0", prn=handle_packet, filter=f"udp port {5555}")

if __name__ == "__main__":
   main()

    


#Receives the information passed through from pass.py
#Depacketizes the information
#passes it to Spool.py
#Additionally reads .json parameters from modem 2, organizes it, and sends it to spool.py

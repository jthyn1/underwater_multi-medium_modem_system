from scapy.all import sniff, Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField, bind_layers
from Pollers.decoder import DECODERS
from scapyWrapper import TelemetryPacket
from dataclasses import asdict

PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)


def handle_packet(pkt):
    if TelemetryPacket not in pkt:
        return
    tp = pkt[TelemetryPacket]
    payload = bytes(tp.data_payload)

    decoder = DECODERS.get(tp.data_type)
    if decoder is None:
        print(f"seq={tp.seq_num} unknown data type: {hex(tp.data_type)}")
        return
    
    # print(f"seq= {tp.seq_num}, tag= {hex(tp.data_type)}: {decoder(payload)}")
    return tp.seq_num, tp.data_type, decoder(payload)



def main():
    sniff(iface="eth0", prn=handle_packet, filter=f"udp port {5555}")

if __name__ == "__main__":
   main()

    


#Receives the information passed through from pass.py
#Depacketizes the information
#passes it to Spool.py
#Additionally reads .json parameters from modem 2, organizes it, and sends it to spool.py

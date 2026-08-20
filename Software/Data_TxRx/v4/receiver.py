from scapy.all import sniff, Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField, bind_layers
from Pollers.decoder import DECODERS
from scapyWrapper import TelemetryPacket
from dataclasses import asdict
import struct

PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

"""
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
"""



def read_payload(pkt):
    if TelemetryPacket not in pkt:
        return

    tp = pkt[TelemetryPacket]

    if len(tp.data_payload) != tp.payload_len:
        print("Payload lengths don't match")

    if len(tp.data_payload) or tp.payload_len > 20:
        print("Payload length is larger than expected")

    # Possibly more error detection here 

    return tp
    
    '''
    payload = tp.data_payload


    slots = {}
    i = 0

    # Read the payload data
    while i < len(payload):
        tag, n = struct.unpack_from("<BH", payload, i)
        i += 3 # Read past the 3 byte header
        slots[tag] = payload[i:i + n]

    return slots
    '''

def main():
    sniff(iface="eth0", prn=read_payload, filter=f"udp port {5555}")

if __name__ == "__main__":
   main()

    


#Receives the information passed through from pass.py
#Depacketizes the information
#passes it to Spool.py
#Additionally reads .json parameters from modem 2, organizes it, and sends it to spool.py

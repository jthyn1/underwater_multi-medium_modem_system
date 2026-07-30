from scapy.all import sniff

def handle_packet(pkt):
    if TelemetryPacket in pkt:
        tp = pkt[TelemetryPacket]
        print(f"seq={tp.seq_num} type={hex(tp.data_type)} payload={tp.payload}")

sniff(iface="eth0", prn=handle_packet, filter=f"udp port {5555}")


#Receives the information passed through from pass.py
#Depacketizes the information
#passes it to Spool.py
#Additionally reads .json parameters from modem 2, organizes it, and sends it to spool.py
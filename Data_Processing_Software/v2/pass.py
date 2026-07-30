#Reads /dev/tty for sensor inputs
import serial
import sys
import time
import struct
from scapy.all import Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField
from scapy.all import bind_layers, sendp

class TelemetryPakcet(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        IntField("payload_len", 0),
        StrLenField("payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]

def readTeensy(): 
    ser = serial.Serial('/dev/ttyACM0', 115200, timeout=2)

    

    try:
        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').rstrip()
                print(line)

    except KeyboardInterrupt:
        print("stopping")
    finally:
        ser.close()

PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

def sensorPacket(sensorData):
    data = b"sensor reading payload"
    pkt (
        Ether(dst="AA:BB:CC:DD:EE:FF")
        / IP(src="192.168.102.100", dst="192.168.102.103")
        / UDP(sport=5000, dport=PORT)
        / TelemetryPakcet(
            seq_num=1
            data_type=0x01
            payload_len=len(sensorData)
            payload=sensorData
        )
    )
    pkt.show()

    sendp(pkt, iface="eth0")




def main():
   readTeensy()


if __name__ == "__main__":
   main()


#Reads .json for operation parameters
#Packetizes and queues inputs
#Outputs packets to optical modem through packet switching
#Error, flow, and congestion control
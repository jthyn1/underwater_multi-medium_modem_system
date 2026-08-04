#Reads /dev/tty for sensor inputs
import serial
import sys
import time
import struct
import string
from scapy.all import Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField
from scapy.all import bind_layers, sendp
from collections import namedtuple
from dataclasses import dataclass

# Defines telemetry packet
class TelemetryPacket(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("data_type", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
    ]

# Defines data parameters
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

# Reads from teensy for sensor data
def readTeensy(): 
    ser = serial.Serial('/dev/ttyAMA0', 115200, timeout=2)
    try:
        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').rstrip() # Returns a str
                yield(line)
    except serial.SerialException as e:
        print(f"serial error:", {e})
    finally:
        ser.close()

# Tokenizes data read from teensy and assigns it to dataFormat class
def parse(dataLine: str, time: int) -> dataFormat:
    tokens = dataLine.strip().split(',')
    waterVal = int(tokens[0])
    tempVal = float(tokens[1])
    pressureVal = float(tokens[2])
    altiVal = float(tokens[3])
    depthVal = float(tokens[4])
    latVal = float(tokens[5])
    latDirVal = tokens[6].encode('ascii')
    lonVal = float(tokens[7])
    lonDirVal = tokens[8].encode('ascii')
    speedVal = float(tokens[9])
    timeVal = time
    return dataFormat(waterVal, tempVal, pressureVal, altiVal, depthVal, latVal, latDirVal, lonVal, lonDirVal, speedVal, timeVal)

# Struct format
Format = '<hfffffcfcfh'

# Packs data from dataFormat class into a struct
def packetize(f: dataFormat) -> bytes:
    return struct.pack(Format, f.waterVal, f.tempVal, f.pressureVal, f.altiVal, f.depthVal, f.latVal, f.latDirVal, f.lonVal, f.lonDirVal, f.speedVal)

# Upacks data from dataFormat struct for debugging
# def depacketize(data: bytes) -> dataFormat:
#    return dataFormat(*struct.unpack(Format, data))

# Defines port # and transmission protocol
PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

# Sends packed data to other pi ip through ethernet
def sensorPacket(sensorData, seq):
    data = b"sensor reading payload"
    pkt = (
        Ether(dst="AA:BB:CC:DD:EE:FF")
        / IP(src="192.168.102.100", dst="192.168.102.103")
        / UDP(sport=5000, dport=PORT)
        / TelemetryPacket(
            seq_num=seq,
            data_type=0x01,
            payload_len=len(sensorData),
            data_payload=sensorData,
        )
    )
    pkt.show()

    sendp(pkt, iface="eth0")



def main():
    seq = 0
    value = readTeensy()
    timing = time.perf_counter()
    try:
        while True:
            seq = (seq + 1) & 0xFFFF
            parsed = parse(next(value), int(time.perf_counter() - timing))
            packetized = packetize(parsed)
            sensorPacket(packetized, seq)
            # unpacked = depacketize(packetized)
            # print(unpacked)
            time.sleep(0.5)
    except KeyboardInterrupt:
        return

if __name__ == "__main__":
   main()


#Reads .json for operation parameters
#Packetizes and queues inputs
#Outputs packets to optical modem through packet switching
#Error, flow, and congestion control

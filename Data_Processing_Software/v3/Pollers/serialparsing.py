#serialparsing.py
import serial
import struct
import time
from .decoder import SENSORS
from dataclasses import dataclass, astuple


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

# Reads from teensy for sensor data
def readTeensy(): 
    ser = serial.Serial('/dev/ttyACM0', 115200, timeout=2)
    try:
        while True:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').rstrip() # Returns a str
                yield line
    except serial.SerialException as e:
        print(f"serial error:", {e})
    finally:
        ser.close()


# Tokenizes data read from teensy and assigns it to dataFormat class
def parse(dataLine: str) -> dataFormat:
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
    return dataFormat(waterVal, tempVal, altiVal, pressureVal, depthVal, latVal, latDirVal, lonVal, lonDirVal, speedVal)

# Struct format
Format = '<hfffffcfcf'

# Packs data from dataFormat class into a struct
def packetize(f: dataFormat) -> bytes:
    return SENSORS, struct.pack(Format, *astuple(f))

def serialMain(line: str) -> tuple[int, bytes]:
    parsed = parse(line)
    sensorTag, packetized_sensor = packetize(parsed)
    return sensorTag, packetized_sensor

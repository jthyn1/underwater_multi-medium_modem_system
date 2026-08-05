#serialparsing.py
import serial
import time
import struct
from collections import namedtuple
from dataclasses import dataclass

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
    return struct.pack(Format, f.waterVal, f.tempVal, f.pressureVal, f.altiVal, f.depthVal, f.latVal, f.latDirVal, f.lonVal, f.lonDirVal, f.speedVal, f.timeVal)

def serialParse():
    value = readTeensy()
    timing = time.perf_counter()
    parsed = parse(next(value), int(time.perf_counter() - timing))
    packetized_sensor = packetize(parsed)
    yield(packetized_sensor)

#decoder.py
import json
import struct

DECODERS = {}

def serial_decoder(data):
    from .serialparsing import dataFormat, getFormat
    def serial_return(nested_data: bytes) -> dataFormat:
        return dataFormat(*struct.unpack(getFormat(), nested_data))
    return serial_return(data)
    
def json_decoder(data):
    return json.loads(data.decode('utf-8'))

SENSORS = 0X01
OPTICALSTATUS = 0X02

UWO = 0X10
UWA = 0X20

DECODERS[SENSORS] = serial_decoder
DECODERS[OPTICALSTATUS] = json_decoder

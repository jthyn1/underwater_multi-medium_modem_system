#decoder.py
import json
import struct

DECODERS = {}

def struct_decoder(format):
    def decoder(data):
        return struct.unpack(format, data)
    decoder.size = struct.calcsize(format)
    return decoder

def json_decoder(data):
    return json.loads(data.decode('utf-8'))

SENSORS = 0X01
OPTICALSTATUS = 0X02

DECODERS[SENSORS] = struct_decoder('<hfffffcfcfh')
DECODERS[OPTICALSTATUS] = json_decoder
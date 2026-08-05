import requests
import time 
from collections import namedtuple
from dataclasses import dataclass
import struct

@dataclass
class JSONFormat:
    amplitude: int
    ambient: int
    opticalTemp: float
    throughput: int
    crc: int
    pkt_recv: int
    pkt_loss: int
    thpt_tot: int
    crc_tot: int
    pkt_recv_tot: int
    pkt_loss_tot: int
    gain: int
    status: int
    voltage: float
    noise: int
    SigNR: float
    sig_strength: int
    sig_amp: int
    speed: int

def readJson():
    try:
        while True:
            response = requests.get("http://192.168.102.101/api/status.json")
            if response.status_code == 200:
                json_data = response.json()
                yield(json_data)
            else:
                print(f"Error: Received status code {response.status_code}")
    except requests.RequestException as e:
        print(f"Request error: {e}")
    


def parse_json(json_data: dict) -> JSONFormat:
    return JSONFormat(
        amplitude=json_data.get("amplitude", 0),
        ambient=json_data.get("ambient", 0),
        opticalTemp=json_data.get("temperature", 0.0),
        throughput=json_data.get("throughput_received_sec", 0),
        crc=json_data.get("crc_errors_sec", 0),
        pkt_recv=json_data.get("pkt_recv_sec", 0),
        pkt_loss=json_data.get("pkt_loss_sec", 0),
        thpt_tot=json_data.get("throughput_received_total", 0),
        crc_tot=json_data.get("crc_errors_total", 0),
        pkt_recv_tot=json_data.get("pkt_recv_total", 0),
        pkt_loss_tot=json_data.get("pkt_loss_total", 0),
        gain=json_data.get("gain", 0),
        status=json_data.get("status_electronics", 0),
        voltage=json_data.get("voltage_board", 0.0),
        noise=json_data.get("noise_amplitude", 0),
        SigNR=json_data.get("SNR", 0.0),
        sig_strength=json_data.get("signal_strength", 0),
        sig_amp=json_data.get("signal_amplitude", 0),
        speed=json_data.get("optical_speed", 0)
    )

Format_json = '<hhfhhhhhhhhhfhfhhh'

def packetize_json(f: JSONFormat) -> bytes:
    return struct.pack(Format_json, f.amplitude, f.ambient, f.opticalTemp, f.throughput, f.crc, 
                       f.pkt_recv, f.pkt_loss, f.thpt_tot, f.crc_tot, f.pkt_recv_tot, f.pkt_loss_tot, 
                       f.gain, f.status, f.voltage, f.noise, f.SigNR, f.sig_strength, f.sig_amp, f.speed)

def JSONParse():
    value = readJson()
    parsed = parse_json(next(value))
    packetized_JSON = packetize_json(parsed)
    yield packetized_JSON
import threading
import socket
import time
import json
import hashlib
import urllib.request
import re
from influxdb import InfluxDBClient
from popoto_serial_v3 import popoto as popoto_mod

#---------------------------
# This script it paired with the Multimodem_switch_V2_Status_Tx.py file 
#---------------------------

#---------------------------
# Run this in terminal at .ven root before running the Tx and Rx script. This is becuase some of the libraries can only be obtained in a virtual environment 
# python3 -m venv .venv --system-site-packages
# source .venv/bin/activate
#---------------------------



# -----------------------------
# CONFIG
# -----------------------------
OPTICAL_HOST = "0.0.0.0"
OPTICAL_PORT = 5000

SERIAL_PORT = "/dev/ttyUSB0"
SERIAL_BAUD = 115200

LOG_FILE = "A_RxSensor_log.json"

LUMA_STATUS_URL = "http://192.168.102.101/status.html"

influx_client = InfluxDBClient(host="localhost", port=8086)
influx_client.switch_database("telemetry")

# Popoto modem
p = popoto_mod(SERIAL_PORT, SERIAL_BAUD, transport="serial")
p.isRemoteCmd = False
p.remoteCommandAck = True


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def clean_number(value):
    if value is None:
        return None

    value = value.replace(",", "").strip()
    value = re.sub(r"[^0-9.\-]", "", value)

    try:
        return float(value)
    except:
        return None


def get_luma_status():
    try:
        url = "http://192.168.102.101/api/status.json"

        with urllib.request.urlopen(url, timeout=3) as response:
            data = json.loads(response.read().decode("utf-8"))

        status = {
            "optical_speed": data.get("optical_speed"),
            "temperature_c": data.get("temperature"),
            "gain": data.get("gain"),
            "receivers_active": data.get("nb_additional_rcv"),
            "luma_voltage": data.get("volt_board"),
            "noise_amplitude": data.get("noise_amplitude"),
            "signal_strength": data.get("signal_strength"),
            "signal_amplitude": data.get("signal_amplitude"),
            "signal_to_noise_ratio": data.get("SNR"),
            "throughput_kbits_sec": data.get("throughput_received_sec"),
            "crc_errors_per_sec": data.get("crc_errors_sec"),
            "packets_received_per_sec": data.get("pkt_recv_sec"),
            "packets_lost_per_sec": data.get("pkt_loss_sec"),
        }

        return status

    except Exception as e:
        print("[LUMA STATUS] API read failed:", e)
        return None


# def write_luma_status_to_influx(status):
#     if not status:
#         return

#     fields = {}

#     for key, value in status.items():
#         if isinstance(value, (int, float)):
#             fields[key] = value

#     if not fields:
#         print("[INFLUX] No numeric LUMA status values to write")
#         return

#     json_body = [
#         {
#             "measurement": "luma_optical_status",
#             "tags": {
#                 "device": "luma_x_rx"
#             },
#             "fields": fields
#         }
#     ]

#     try:
#         influx_client.write_points(json_body)
#         print("[INFLUX] Wrote LUMA optical status to database")
#     except Exception as e:
#         print("[INFLUX] Failed to write LUMA status:", e)

def write_luma_status_to_influx(status):
    if not status:
        return

    fields = {}

    for key, value in status.items():
        try:
            # FORCE everything to float
            fields[key] = float(value)
        except:
            continue

    if not fields:
        print("[INFLUX] No valid LUMA status values")
        return

    json_body = [
        {
            "measurement": "luma_optical_status",
            "tags": {
                "device": "luma_x_rx"
            },
            "fields": fields
        }
    ]

    try:
        influx_client.write_points(json_body)
        print("[INFLUX] Wrote LUMA optical status to database")
    except Exception as e:
        print("[INFLUX] Failed to write LUMA status:", e)


def calculate_checksum(data):
    data_string = json.dumps(data, sort_keys=True)
    return hashlib.sha256(data_string.encode()).hexdigest()


def verify_packet(packet):
    if "Payload" not in packet:
        print("[VERIFY FAILED] Missing Payload")
        return False

    if "Checksum" not in packet:
        print("[VERIFY FAILED] Missing Checksum")
        return False

    if "ByteLength" not in packet:
        print("[VERIFY FAILED] Missing ByteLength")
        return False

    payload_data = packet["Payload"]
    expected_checksum = packet["Checksum"]
    actual_checksum = calculate_checksum(payload_data)

    payload_string = json.dumps(payload_data)
    expected_length = packet["ByteLength"]
    actual_length = len(payload_string.encode())

    if expected_checksum != actual_checksum:
        print("[VERIFY FAILED] Checksum does not match")
        return False

    if expected_length != actual_length:
        print("[VERIFY FAILED] Byte length does not match")
        return False

    print(f"[VERIFY OK] Packet {packet.get('PacketID')} received correctly")
    return True


def print_sensor_data(data):
    if not data:
        return

    print("\n📡 SENSOR DATA RECEIVED")
    print(f"Pressure: {data.get('pressure_mbar')} mbar")
    print(f"Pressure: {data.get('pressure_psi')} psi")
    print(f"Temp: {data.get('temperature_c')} °C")
    print(f"Fix: {data.get('gps_fix')} ")
    print(f"Fix_Quality: {data.get('gps_fixquality')}")
    print("--------------------------------------------------")


def log_data(data):
    if not data:
        return

    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(data) + "\n")
    except Exception as e:
        print("Logging failed:", e)


def handle_message(msg, source="UNKNOWN"):
    print(f"[RAW RX - {source}] {msg}")

    if isinstance(msg, dict) and "Payload" in msg:
        if verify_packet(msg):
            data = msg["Payload"].get("Data")
            print_sensor_data(data)
            log_data(data)

            print("Modem Type:", msg.get("ModemType"))
            print("Signal Strength:", msg.get("SignalStrength"))

            if msg.get("LumaStatus"):
                write_luma_status_to_influx(msg.get("LumaStatus"))

        return

    if isinstance(msg, dict) and "Text" in msg:
        text = msg["Text"]

        try:
            parsed = json.loads(text)

            if "Payload" in parsed:
                if verify_packet(parsed):
                    data = parsed["Payload"].get("Data")
                    print_sensor_data(data)
                    log_data(data)

                    print("Modem Type:", parsed.get("ModemType"))
                    print("Signal Strength:", parsed.get("SignalStrength"))

                    if parsed.get("LumaStatus"):
                        write_luma_status_to_influx(parsed.get("LumaStatus"))

                return

        except Exception:
            pass

    if isinstance(msg, str):
        try:
            parsed = json.loads(msg)

            if "Payload" in parsed:
                if verify_packet(parsed):
                    data = parsed["Payload"].get("Data")
                    print_sensor_data(data)
                    log_data(data)

                    print("Modem Type:", parsed.get("ModemType"))
                    print("Signal Strength:", parsed.get("SignalStrength"))

                    if parsed.get("LumaStatus"):
                        write_luma_status_to_influx(parsed.get("LumaStatus"))

                return

        except Exception:
            pass


# -----------------------------
# ACOUSTIC RECEIVER THREAD
# -----------------------------
def monitor_acoustic():
    try:
        print("[ACOUSTIC] Starting acoustic receiver...")
        p.send("startrx")
        print("[ACOUSTIC] Listening for incoming acoustic data...")

        while True:
            reply = p.waitForReply(10)

            if reply:
                handle_message(reply, source="ACOUSTIC")

            time.sleep(0.1)

    except Exception as e:
        print(f"[ACOUSTIC] Error: {e}")

    finally:
        try:
            p.tearDownPopoto()
        except Exception:
            pass


# -----------------------------
# OPTICAL RECEIVER THREAD
# -----------------------------
def monitor_optical():
    server = None

    try:
        server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server.bind((OPTICAL_HOST, OPTICAL_PORT))
        server.listen(1)

        print(f"[OPTICAL] Listening on {OPTICAL_HOST}:{OPTICAL_PORT}...")

        while True:
            conn, addr = server.accept()
            print(f"[OPTICAL] Connected from {addr}")

            with conn:
                buffer = ""

                while True:
                    data = conn.recv(4096)

                    if not data:
                        break

                    decoded = data.decode("utf-8", errors="ignore")
                    buffer += decoded

                    try:
                        parsed = json.loads(buffer)
                        handle_message(parsed, source="OPTICAL")
                        buffer = ""

                    except json.JSONDecodeError:
                        pass

            print("[OPTICAL] Connection closed, waiting for next sender...")

    except Exception as e:
        print(f"[OPTICAL] Error: {e}")

    finally:
        if server:
            server.close()


# -----------------------------
# LUMA STATUS THREAD
# -----------------------------
def monitor_luma_status():
    print("[LUMA STATUS] Starting LUMA status monitor...")

    while True:
        status = get_luma_status()

        if status:
            print("[LUMA STATUS]", status)
            write_luma_status_to_influx(status)

        time.sleep(5)


# -----------------------------
# VIDEO TRANSMISSION
# -----------------------------
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from picamera2 import Picamera2


def video_transmit_optical():
    WIDTH = 320
    HEIGHT = 240
    FPS = 8
    RECEIVER_IP = "192.168.102.102"
    RECEIVER_PORT = 5000

    Gst.init(None)

    PIPELINE_STR = (
        f'appsrc name=source is-live=true block=true format=time '
        f'caps=video/x-raw,format=RGB,width={WIDTH},height={HEIGHT},framerate={FPS}/1 ! '
        f'videoconvert ! '
        f'x264enc tune=zerolatency speed-preset=ultrafast '
        f'bitrate=2000 byte-stream=true threads=4 key-int-max=10 ! '
        f'video/x-h264,profile=constrained-baseline ! '
        f'rtph264pay config-interval=1 pt=96 mtu=1000 ! '
        f'srtsink uri=srt://{RECEIVER_IP}:{RECEIVER_PORT}?mode=caller&latency=250'
    )

    pipeline = Gst.parse_launch(PIPELINE_STR)
    appsrc = pipeline.get_by_name("source")

    picam2 = Picamera2()
    video_config = picam2.create_video_configuration(
        main={"size": (WIDTH, HEIGHT), "format": "RGB888"},
        controls={"FrameRate": FPS}
    )

    picam2.configure(video_config)
    picam2.start()

    time.sleep(1)

    pipeline.set_state(Gst.State.PLAYING)

    print("Streaming started... Press Ctrl+C to stop.")

    frame_duration = Gst.util_uint64_scale_int(1, Gst.SECOND, FPS)
    timestamp = 0

    try:
        while True:
            frame = picam2.capture_array()
            data = frame.tobytes()

            buf = Gst.Buffer.new_allocate(None, len(data), None)
            buf.fill(0, data)
            buf.pts = timestamp
            buf.dts = timestamp
            buf.duration = frame_duration
            timestamp += frame_duration

            ret = appsrc.emit("push-buffer", buf)

            if ret != Gst.FlowReturn.OK:
                print("push-buffer failed:", ret)
                break

    except KeyboardInterrupt:
        print("\nStopping stream...")

    finally:
        appsrc.emit("end-of-stream")
        pipeline.set_state(Gst.State.NULL)
        picam2.stop()


# -----------------------------
# MAIN
# -----------------------------
def main():
    print("--------------------------------------------------")
    print("[SYSTEM] Starting hybrid receiver...")
    print("[SYSTEM] Monitoring acoustic + optical + LUMA status")
    print("--------------------------------------------------")

    t_acoustic = threading.Thread(target=monitor_acoustic, daemon=True)
    t_optical = threading.Thread(target=monitor_optical, daemon=True)
    t_luma = threading.Thread(target=monitor_luma_status, daemon=True)

    t_acoustic.start()
    t_optical.start()
    t_luma.start()

    print("--------------------------------------------------")
    print("Starting Video transmission")
    print("--------------------------------------------------")

    #video_transmit_optical()

    try:
        while True:
            time.sleep(1)

    except KeyboardInterrupt:
        print("\n[SYSTEM] Stopping receiver...")


if __name__ == "__main__":
    main()
import threading
import socket
import time
import json
from popoto_serial_v3 import popoto as popoto_mod

# -----------------------------
# CONFIG
# -----------------------------
OPTICAL_HOST = "0.0.0.0"
OPTICAL_PORT = 5000

SERIAL_PORT = "/dev/ttyUSB1"
SERIAL_BAUD = 115200

LOG_FILE = "A_RxSensor_log.json"

# Popoto modem
p = popoto_mod(SERIAL_PORT, SERIAL_BAUD, transport="serial")
p.isRemoteCmd = False
p.remoteCommandAck = True


# -----------------------------
# HELPER FUNCTIONS
# -----------------------------
def print_sensor_data(data):
    if not data:
        return

    print("\n📡 SENSOR DATA RECEIVED")
    print(f"Pressure: {data.get('pressure_mbar')} mbar")
    print(f"Pressure: {data.get('pressure_psi')} psi")
    print(f"Temp: {data.get('temperature_c')} °C")
    #print(f"Temp: {data.get('temperature_f')} °F")
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

    # Case 1: already a dict with Payload
    if isinstance(msg, dict) and "Payload" in msg:
        data = msg["Payload"].get("Data")
        print_sensor_data(data)
        log_data(data)
        return

    # Case 2: dict with JSON text inside "Text"
    if isinstance(msg, dict) and "Text" in msg:
        text = msg["Text"]
        try:
            parsed = json.loads(text)
            if "Payload" in parsed:
                data = parsed["Payload"].get("Data")
                print_sensor_data(data)
                log_data(data)
                return
        except Exception:
            pass

    # Case 3: plain JSON string from optical socket
    if isinstance(msg, str):
        try:
            parsed = json.loads(msg)
            if "Payload" in parsed:
                data = parsed["Payload"].get("Data")
                print_sensor_data(data)
                log_data(data)
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
        #server.connect(("192.168.102.10", 5000))
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

                    # Try to parse complete JSON payload
                    try:
                        parsed = json.loads(buffer)
                        handle_message(parsed, source="OPTICAL")
                        buffer = ""
                    except json.JSONDecodeError:
                        # keep collecting until full JSON arrives
                        pass

            print("[OPTICAL] Connection closed, waiting for next sender...")

    except Exception as e:
        print(f"[OPTICAL] Error: {e}")

    finally:
        if server:
            server.close()






# -----------------------------
# VIDEO TRANSMISSION
# -----------------------------


import time
import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst

from picamera2 import Picamera2

def video_transmit_optical():
    WIDTH = 320
    HEIGHT = 240
    FPS = 8
    RECEIVER_IP = "192.168.102.10"
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
    print("[SYSTEM] Monitoring acoustic + optical at the same time")
    print("--------------------------------------------------")

    t_acoustic = threading.Thread(target=monitor_acoustic, daemon=True)
    t_optical = threading.Thread(target=monitor_optical, daemon=True)

    t_acoustic.start()
    t_optical.start()
    
    print("--------------------------------------------------")
    print("Stating Video transmission")
    print("--------------------------------------------------")

    video_transmit_optical()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[SYSTEM] Stopping receiver...")


if __name__ == "__main__":
    main()
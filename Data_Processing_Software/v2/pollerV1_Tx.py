import socket
from popoto_serial_v3 import popoto as popoto_mod
from influxdb import InfluxDBClient
import time
import random
import ms5837
import json
import sys
from typing import Optional, Dict, Any
import serial
import threading
import hashlib
import urllib.request
import re
from flask import Flask, Response
import cv2


#---------------------------
# This script it paired with modeSwitchV3_Status_Rx.py
#---------------------------
#---------------------------
# Run this in terminal at .ven root before running the Tx and Rx script. This is becuase some of the libraries can only be obtained in a virtual environment 
# python3 -m venv .venv --system-site-packages
# source .venv/bin/activate
#---------------------------
tx_influx = InfluxDBClient(host='localhost', port=8086)
tx_influx.switch_database('telemetry')




client = InfluxDBClient(host='localhost', port=8086)
client.switch_database('telemetry')
MEASUREMENT = "pressure_sensor_teensy"


filename = f"image_{int(time.time())}.mp4"


p = popoto_mod("/dev/ttyUSB0", 115200, transport="serial")
p.isRemoteCmd = False


SERIAL_PORT = "/dev/ttyACM0"
BAUD_RATE = 115200


LUMA_STATUS_URL = "http://192.168.102.102/status.html"


packet_counter = 0


# Video receiver pipeline for optical modem
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst, GLib


Gst.init(None)


PIPELINE_STR = (
   "srtsrc uri=srt://:5000?mode=listener&latency=300 ! "
   "application/x-rtp, payload=96 ! "
   "rtph264depay ! h264parse ! avdec_h264 ! "
   "videoconvert ! waylandsink sync=false"
)




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
      url = "http://192.168.102.102/api/status.json"




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








def write_luma_status_to_influx_tx(status):
   if not status:
       return


   fields = {}


   for key, value in status.items():
       try:
           fields[key] = float(value)
       except:
           continue


   if not fields:
       print("[TX INFLUX] No valid values")
       return


   json_body = [
       {
           "measurement": "luma_optical_status_tx",
           "tags": {
               "device": "luma_x_tx"
           },
           "fields": fields
       }
   ]


   try:
       tx_influx.write_points(json_body)
       print("[TX INFLUX] Wrote LUMA status")
   except Exception as e:
       print("[TX INFLUX] Failed:", e)












def calculate_checksum(data):
   data_string = json.dumps(data, sort_keys=True)
   return hashlib.sha256(data_string.encode()).hexdigest()




def read_sensor():
   ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
   time.sleep(2)


   try:
       print(f"Reading from {SERIAL_PORT} and writing to InfluxDB...")


       line = ser.readline().decode("utf-8", errors="ignore").strip()


       if not line:
           return None


       print("RAW:", line)
       data = json.loads(line)


       if "pressure_mbar" not in data:
           return None


       json_body = [
           {
               "measurement": MEASUREMENT,
               "tags": {
                   "device": "teensy_pressure_sensor"
               },
               "fields": {
                   "pressure_mbar": float(data["pressure_mbar"]),
                   "temperature_c": float(data["temperature_c"]),
                   "depth_m": float(data["depth_m"]),
                   "gps_fix": float(data.get("gps_fix")),
                   "gps_quality": float(data.get("gps_fixquality")),
               }
           }
       ]


       client.write_points(json_body)
       print("Wrote point to InfluxDB")


   except json.JSONDecodeError:
       print("Skipping invalid JSON line")
       return None
   except Exception as e:
       print("Error:", e)
       time.sleep(1)
       return None


   print("[SENSOR]", data)
   return data




def build_payload(sensor_data, modem_type="UNKNOWN", signal_strength=None, luma_status=None):
   global packet_counter
   packet_counter += 1


   payload_data = {
       "Data": sensor_data
   }


   checksum = calculate_checksum(payload_data)
   payload_string = json.dumps(payload_data)


   packet = {
       "PacketID": packet_counter,
       "Timestamp": time.time(),
       "ModemType": modem_type,
       "SignalStrength": signal_strength,
       "LumaStatus": luma_status,
       "ByteLength": len(payload_string.encode()),
       "Checksum": checksum,
       "Payload": payload_data
   }


   return json.dumps(packet)




def send_sensor_data_Acoustic():
   data = read_sensor()
   if data is None:
       return False


   payload = build_payload(
       data,
       modem_type="ACOUSTIC",
       signal_strength=None,
       luma_status=None
   )


   try:
       cmd = f"transmitJSON {payload}"
       print("[ACOUSTIC] Sending:", cmd)


       p.send(cmd)


       success = False


       for _ in range(10):
           reply = p.waitForReply(5)
           if reply:
               print("[ACOUSTIC] Reply:", reply)
               success = True


       return success


   except Exception as e:
       print("Acoustic TX failed:", e)
       return False




def send_sensor_data_Optical():
   data = read_sensor()
   if data is None:
       return False


   luma_status = get_luma_status()


   write_luma_status_to_influx_tx(luma_status)


   signal_strength = None
   if luma_status:
       signal_strength = luma_status.get("signal_strength")


   payload = build_payload(
       data,
       modem_type="OPTICAL",
       signal_strength=signal_strength,
       luma_status=luma_status
   )


   try:
       s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       s.settimeout(3)
       s.connect(("192.168.102.11", 5000))
       s.sendall(payload.encode())
       s.close()


       print("[OPTICAL] Data sent successfully")
       return True


   except Exception as e:
       print("Optical TX failed:", e)
       return False




def test_transmit():
   print("Sending test message...")
   p.send('transmit "Hello world"')


   reply = p.waitForReply(5)


   if reply:
       print("Acoustic TX success:", reply)
       return True
   else:
       print("Acoustic TX failed")
       return False




def test_transmit_Optical():
   try:
       s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       s.settimeout(3)
       s.connect(("192.168.102.11", 5000))
       print("Connected to optical modem")
       s.close()
       return True


   except Exception as e:
       print("Optical connection failed:", e)
       return False




def start_receiver():
   pipeline = Gst.parse_launch(PIPELINE_STR)


   bus = pipeline.get_bus()
   bus.add_signal_watch()


   def on_message(bus, message):
       t = message.type
       if t == Gst.MessageType.ERROR:
           err, debug = message.parse_error()
           print(f"Error: {err}, {debug}")
       elif t == Gst.MessageType.EOS:
           print("End of stream reached.")


   bus.connect("message", on_message)
   pipeline.set_state(Gst.State.PLAYING)


   print("Receiver active. Waiting for stream...")


   loop = GLib.MainLoop()


   try:
       loop.run()
   except KeyboardInterrupt:
       pipeline.set_state(Gst.State.NULL)




def monitor_luma_status_tx():
   print("[TX] Monitoring LUMA status...")


   while True:
       status = get_luma_status()


       if status:
           write_luma_status_to_influx_tx(status)


       time.sleep(5)




def start_grafana_video_stream():
   app = Flask(__name__)


   GSTREAMER_PIPELINE = (
       "srtsrc uri=srt://:5000?mode=listener&latency=300 ! "
       "application/x-rtp, payload=96 ! "
       "rtph264depay ! "
       "h264parse ! "
       "avdec_h264 ! "
       "videoconvert ! "
       "appsink drop=true sync=false"
   )


   cap = cv2.VideoCapture(GSTREAMER_PIPELINE, cv2.CAP_GSTREAMER)


   if not cap.isOpened():
       print("[VIDEO ERROR] Could not open SRT stream for Grafana")
       return


   def generate_frames():
       while True:
           success, frame = cap.read()


           if not success:
               time.sleep(0.1)
               continue


           ret, buffer = cv2.imencode(".jpg", frame)


           if not ret:
               continue


           frame_bytes = buffer.tobytes()


           yield (
               b"--frame\r\n"
               b"Content-Type: image/jpeg\r\n\r\n" + frame_bytes + b"\r\n"
           )


   @app.route("/video")
   def video():
       return Response(
           generate_frames(),
           mimetype="multipart/x-mixed-replace; boundary=frame"
       )


   print("[VIDEO] Grafana stream running at http://0.0.0.0:8000/video")
   app.run(host="0.0.0.0", port=8000, threaded=True)








def main():
   print("--------------------------------------------------")


   time.sleep(2)


   #threading.Thread(target=start_receiver, daemon=True).start()
   #threading.Thread(target=start_grafana_video_stream, daemon=True).start()
   threading.Thread(target=monitor_luma_status_tx, daemon=True).start()


   while True:
       print("\nChecking modem status...")


       optical_ok = test_transmit_Optical()


       if optical_ok:
           print("Using OPTICAL modem")
           sent = send_sensor_data_Optical()


           if not sent:
               print("Optical TX failed, falling back to ACOUSTIC modem")
               acoustic_ok = test_transmit()


               if acoustic_ok:
                   send_sensor_data_Acoustic()
               else:
                   print("No modem available — check connections")


       else:
           print("Optical modem failed, trying ACOUSTIC modem")
           acoustic_ok = test_transmit()


           if acoustic_ok:
               send_sensor_data_Acoustic()
           else:
               print("No modem available — check connections")


       time.sleep(5)




if __name__ == "__main__":
   main()

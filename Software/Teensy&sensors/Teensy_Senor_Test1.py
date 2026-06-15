import json
import time
import serial
from influxdb import InfluxDBClient


SERIAL_PORT = "/dev/ttyUSB3"
BAUD_RATE = 115200


client = InfluxDBClient(host='localhost', port=8086)
client.switch_database('telemetry')


MEASUREMENT = "pressure_sensor_teensy"


def main():
   ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2)
   time.sleep(2)


   print(f"Reading from {SERIAL_PORT} and writing to InfluxDB...")


   while True:
       try:
           line = ser.readline().decode("utf-8", errors="ignore").strip()


           if not line:
               continue


           print("RAW:", line)
           data = json.loads(line)


           if "pressure_mbar" not in data:
               continue


           json_body = [
               {
                   "measurement": MEASUREMENT,
                   "tags": {
                       "device": "teensy_pressure_sensor"
                   },
                   "fields": {
                       "pressure_mbar": float(data["pressure_mbar"]),
                       "temperature_c": float(data["temperature_c"]),
                       "depth_m": float(data["depth_m"])
                   }
               }
           ]


           client.write_points(json_body)
           print("Wrote point to InfluxDB")


       except json.JSONDecodeError:
           print("Skipping invalid JSON line")
       except Exception as e:
           print("Error:", e)
           time.sleep(1)


if __name__ == "__main__":
   main()




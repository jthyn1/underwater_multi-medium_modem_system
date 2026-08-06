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


client = InfluxDBClient(host='localhost', port=8086)
client.switch_database('telemetry')
MEASUREMENT = "pressure_sensor_teensy"

#LOG_FILE = "A_RxSensor_log.json"


filename = f"image_{int(time.time())}.mp4"

#sensor_data = f"payload.json"

p = popoto_mod("/dev/ttyUSB1", 115200, transport="serial")
p.isRemoteCmd = False
#p.remoteCommandAck = True

SERIAL_PORT = "/dev/ttyUSB3"
BAUD_RATE = 115200




#sensor = ms5837.MS5837_30BA()



# tempFault = 0
# #< 0 or sensor.temperature() > 40
# tempFault = sensor.temperature()
# while tempFault < 0 or tempFault > 79:
#     print("Temperature out of range! Transmitting through optical modem...")
#     Optical_Modem_Tx()
#     time.sleep(10)  # wait before checking again
#     tempFault = sensor.temperature()






# def init_sensor():
#    if not sensor.init():
#        print("Sensor init failed")
#        return False
#    print("Sensor initialized")
#    return True




# =========================
# READ SENSOR DATA
# =========================
#def read_sensor():
#    if not sensor.read():
#        print("Sensor read failed")
#        return None


#    data = {
#        "pressure_mbar": sensor.pressure(),
#        "pressure_psi": sensor.pressure(ms5837.UNITS_psi),
#        "temperature_c": sensor.temperature(),
#        "temperature_f": sensor.temperature(ms5837.UNITS_Farenheit)
#    }


def read_sensor():
    try:
        with serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=2) as ser:
            time.sleep(2)
            print(f"Reading from {SERIAL_PORT} and writing to InfluxDB...")

            for _ in range(10):  # try up to 10 lines
                line = ser.readline().decode("utf-8", errors="ignore").strip()

                if not line:
                    continue

                print("RAW:", line)

                # Ignore partial/broken lines
                if not line.startswith("{") or not line.endswith("}"):
                    print("Skipping incomplete JSON line")
                    continue

                try:
                    data = json.loads(line)
                except json.JSONDecodeError:
                    print("Skipping invalid JSON line")
                    continue

                if "pressure_mbar" not in data:
                    print("Skipping non-sensor JSON")
                    continue

                json_body = [
                    {
                        "measurement": MEASUREMENT,
                        "tags": {
                            "device": "esp32_pressure_sensor"
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
                print("[SENSOR]", data)

                return data

            print("No valid sensor data received")
            return None

    except Exception as e:
        print("Sensor read error:", e)
        return None



# =========================
# BUILD JSON PAYLOAD
# =========================
def build_payload(sensor_data):
   return json.dumps({
       "Payload": {
           "Data": sensor_data
       }
   })




# =========================
# SEND DATA ACOUSTICALLY
# =========================
# def send_sensor_data_Acoustic():
#    data = read_sensor()
#    if data is None:
#        return


#    payload = build_payload(data)


#    # CLI command (NOT JSON wrapper)
#    cmd = f"transmitJSON {payload}"
#    print("[ACOUSTIC] Sending:", cmd)


#    p.send(cmd)


#    # Listen for modem responses
#    for _ in range(10):
#        reply = p.waitForReply(5)
#        if reply:
#            print("[ACOUSTIC] Reply:", reply)


#    p.tearDownPopoto()

def send_sensor_data_Acoustic():
   data = read_sensor()
   if data is None:
       return False


   payload = build_payload(data)


   try:
       # CLI command (NOT JSON wrapper)
       cmd = f"transmitJSON {payload}"
       print("[ACOUSTIC] Sending:", cmd)


       p.send(cmd)


       success = False


       # Listen for modem responses
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
       return


   payload = build_payload(data)


   try:
       s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
       s.settimeout(3)  # don't hang forever
       s.connect(("192.168.102.10", 5000))
       s.sendall(payload.encode())  # send directly
       s.close()
       print("[OPTICAL] Data sent successfully")
       return True


   except Exception as e:
       print("Optical TX failed:", e)
       return False
   # payload = build_payload(data)
   # print("Waiting to transmit optical data...")
   # #s = socket.socket()
   # #s.connect(("192.168.102.11", 5000))
   # with open("payload.json", "rb") as f:
   #     s.sendall(f.read())
   # s.close()
   # Listen for modem responses


# =========================
# OPTIONAL: SIMPLE TEST TX
# =========================
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
       s.connect(("192.168.102.10", 5000))
       s.settimeout(3)  # don't hang forever
       # s.connect(("192.168.102.11", 5000))
       print("Connected to optical modem")
       s.close()
       return True


   except Exception as e:
       print("Optical connection failed:", e)
       return False

# =========================
# OPTIONAL: SIMPLE TEST RX
# =========================
def test_receive_Acoustic():
   print("Receiving test message...")

   reply = p.waitForReply(5)


   if reply:
       print("Acoustic RX success:", reply)
       return True
   else:
       print("Acoustic RX failed")
       return False




def test_receive_Optical():
   try:
       s = socket.socket()
       s.settimeout(3)  # don't hang forever
       s.bind(("0.0.0.0", 5000))
       s.listen(1)
       print("Waitng for connection...")
       # s.connect(("192.168.102.11", 5000))
       print("Connected to optical modem")
       s.close()
       return True


   except Exception as e:
       print("Optical connection failed:", e)
       return False
  
def Optical_Modem_Rx():
   s = socket.socket()
   s.bind(("0.0.0.0", 5000))
   s.listen(1)
   print("Waiting for connection...")
   conn, addr = s.accept()
   print("Connected from", addr)


   with conn:
      
       with open(filename, "wb") as f:
           total = 0
           while True:
               data = conn.recv(4096)
               if not data:
                   break
               total += len(data)
               print("Received", len(data), "bytes | Total:", total)
               f.write(data)


   s.close()
   print("Done receiving optical data.")




def Optical_Modem_Tx():
  
   print("Waiting to transmit optical data...")
   s = socket.socket()
   s.connect(("192.168.102.11", 5000))
   with open("cham.mp4", "rb") as f:
       s.sendall(f.read())
   s.close()






#def do_nranges(self,line):
   #for x in range(1,5):
   #self.dol.range(.1)
   #time.sleep(30.)


# check modem
def Acoustic_Modem_Tx():
   time.sleep(3)  # wait for receiver to be ready
 
   #p.sendRange(0.5)
   p.send("version")
   print(p.waitForReply(5))
   #p.send("help")
   #p.send("get")
   print("Sending acoustic message...")
   




  
   #p.send('transmitJSONFiles "telemery.json"')
   #p.send('transmit "Hello world"')
   #p.range(0.5)
   #p.send('transmitJSON { "Payload": {"Data":[72,101,108,108,111]} }')
   #p.send('transmitJSON { "range": 0.5 }')
   print(p.waitForReply(10))


   #print("TX Reply:", p.waitForReply(10))
   print("Done transmitting acoustic data.")
   time.sleep(30)


   p.tearDownPopoto()






 


def Acoustic_Modem_Rx():


   print("Acoustic receiver listening....")


#while True:
   reply = p.waitForReply(10)
   if reply:
       print("Received:", reply)
   #finally:
   time.sleep(5)
   p.tearDownPopoto()












#Protocall
#
# 1. Make the Acoustic Modem be the defualt modem then send through optical modem if an error is detected in the acoustic modem. If the acoustic modem is working fine, then send through it and use the optical modem as a backup.
# 2.




    




def main():
   #print("----------------------------------------------------------------")
   #Optical_Modem_Tx()
   #print("----------------------------------------------------------------")
   #Acoustic_Modem_Rx()
   #print("----------------------------------------------------------------")


   #p.tearDownPopoto()
   #Optical_Modem_Rx()
   # print("----------------------------------------------------------------")
   # Acoustic_Modem_Tx()
   # print("----------------------------------------------------------------")
  




   print("--------------------------------------------------")


#    if not init_sensor():
#        return


   time.sleep(2)
  
  
   while True:
       print("\nChecking modem status...")


       optical_ok = test_transmit_Optical()


       # Decide which modem to use
       if optical_ok:
           print("Using OPTICAL modem")
           sent = send_sensor_data_Optical()


           if not sent:
               print ("Optical TX failed, falling back to ACOUSTIC modem")
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
   
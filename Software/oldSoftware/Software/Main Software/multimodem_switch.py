
import socket
import csv
import time
import json
from popoto_serial_v3 import popoto as popoto_mod

p = popoto_mod("/dev/ttyUSB0", 115200, transport="serial")
p.isRemoteCmd = False
p.remoteCommandAck = True


#filename = f"image_{int(time.time())}.mp4"
filename = f"image_1773158199.mp4"

sensor = f"payload.json"

# =========================
# OPTIONAL: LOG FILE
# =========================
LOG_FILE = "RxSensor_log.json"


def handle_message(msg):
    print("[RAW RX]", msg)

    # -------------------------
    # Case 1: Direct dictionary payload
    # -------------------------
    if isinstance(msg, dict):
        if "Payload" in msg:
            data = msg["Payload"].get("Data")
            print_sensor_data(data)
            log_data(data)
            return

    # -------------------------
    # Case 2: JSON string inside Text
    # -------------------------
    if isinstance(msg, dict) and "Text" in msg:
        text = msg["Text"]

        try:
            parsed = json.loads(text)

            if "Payload" in parsed:
                data = parsed["Payload"].get("Data")
                print_sensor_data(data)
                log_data(data)

        except Exception:
            pass  # ignore non-JSON text

# =========================
# PRINT FORMATTED DATA
# =========================
def print_sensor_data(data):
    if not data:
        return

    print("\n📡 SENSOR DATA RECEIVED")
    print(f"Pressure: {data.get('pressure_mbar')} mbar")
    print(f"Pressure: {data.get('pressure_psi')} psi")
    print(f"Temp: {data.get('temperature_c')} °C")
    print(f"Temp: {data.get('temperature_f')} °F")
    print("--------------------------------------------------")


# =========================
# LOG TO FILE (OPTIONAL)
# =========================
def log_data(data):
    if not data:
        return

    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(data) + "\n")
    except Exception as e:
        print("Logging failed:", e)


def Optical_Modem_Rx():
    s = socket.socket()
    s.bind(("0.0.0.0", 5000))
    s.listen(1)
    print("Waiting for connection...")
    conn, addr = s.accept()
    print("Connected from", addr)

    with conn:
        
        with open(sensor, "wb") as f:
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
    time.sleep(10)
    print("Waiting to transmit optical data...")
    s = socket.socket()
    s.connect(("192.168.102.10", 5000))
    with open(filename, "rb") as f:
        s.sendall(f.read())
    s.close()





# check modem
def Acoustic_Modem_Tx():
    time.sleep(3)
    

    p.send("version")
    print(p.waitForReply(5))

    print("Sending acoustic message...")
    p.send('transmit "Hello world"')
    #p.send('transmitJSON { "Payload": {"Data":[72,101,108,108,111]} }')
    print(p.waitForReply(15))

    #print("TX Reply:", p.waitForReply(10))
    print("Done transmittinging acoustic data.")
    time.sleep(30)

    
    p.tearDownPopoto()




#def do_nranges(self, line):
    

            

def Acoustic_Modem_Rx():
    #p.send("help")
    print("Acoustic receiver listening....")
    p.send("startrx")
    #p.send("getvaluei EnableRangeResponse")
    #p.send("getvaluei LocalID")
    #p.send("getvaluei RemoteID")
    #p.send("getvaluei RangeTimeout_mS")
    #try:
    # while True:
    #     reply = p.waitForReply(30)

    #     if reply:
    #         print("Received:", reply)



    print(" Acoustic receiver listening...")
    print("Press CTRL+C to stop\n")

    try:
        while True:
            reply = p.waitForReply(10)

            if reply:
                handle_message(reply)

            time.sleep(0.1)

    except KeyboardInterrupt:
        print("\n Stopping receiver...")

    finally:
        p.tearDownPopoto()
        print("Modem connection closed")

        
        # def do_nranges(self, line):
        #     with open("range_log.csv","a",newline="") as f:
        #         writer = csv.writer(f)

        #     for x in range(4):
        #         result = self.dol.range(.1)

        #         writer.writerow([time.time(), result])
        #         print("Range:", result)
        #         time.sleep(30)

        # Example: adjust these keys based on what your modem actually returns
        
        """if "Range" in reply and "DopplerVelocity" in reply:
            current_distance = reply["Range"]
            current_doppler = reply["DopplerVelocity"]

            msg = {
                "Distance": current_distance,
                "DopplerVelocity": current_doppler
            }

            p.transmitJSON({
                "Payload": {
                    "Data": list(json.dumps(msg).encode())
                }
            })

            print("Sent real-time values:", msg)
        else:
            print("Did not get range/doppler data")"""
    #finally:
    #p.tearDownPopoto()


def main():
    #print("------------------------------------------------------------------------------------")
    Optical_Modem_Rx()
    #print("------------------------------------------------------------------------------------")
    #Acoustic_Modem_Tx()
    #print("------------------------------------------------------------------------------------")
    
    #Optical_Modem_Tx()
    #print("------------------------------------------------------------------------------------")
    #Acoustic_Modem_Rx()
    #p.tearDownPopoto()



if __name__ == "__main__":
    main()

"""import popoto_serial as popoto_mod
import time

p = popoto_mod.popoto("/dev/ttyUSB0", 115200, transport="serial")

p.isRemoteCmd = True
p.remoteCommandAck = True

print("Receiver listening...")

while True:
    reply = p.waitForReply(30)
    if reply:
        print("Received:", reply)
"""
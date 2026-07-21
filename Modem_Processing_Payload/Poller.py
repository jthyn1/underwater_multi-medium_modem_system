import requests as rq
import json
import time
import influxdb_client as influx
from influxdb_client.client.write_api import SYNCHRONOUS
import subprocess 
from collections import deque

# Changes the user ip addr to 192.168.102.100
"""
def ipAssignment(interface, ip):
    try:
        subprocess.run(["sudo", "ip", "addr", "add", f"{ip}/24", "dev", interface], check=True)
        print("Ip successfully assigned")
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}, script must be run as a sudo user")
"""

# Pings and confirms the modem is reachable, otherwise returns an error
"""
def pingTest(modemIP):
    try:
        subprocess.run(["ping", "-c", "3", f"{modemIP}"])
    except subprocess.CalledProcessError as e:
        print(f"Error: {e}")
"""

# Polls JSON file for the response.json
"""
def getJSON ():
    for i in range(4):
        try:
            response = rq.get("http://192.168.102.101/api/status.json")
            response.raise_for_status()
        except rq.exceptions.RequestException as err:
            print(f"Error: {err}")
        except rq.exceptions.Timeout as err:
            print(f"Error: {err}")
        except rq.exceptions.HTTPError as err:
            print(f"Error: {err}")
        except rq.exceptions.ConnectionError as err:
            print(f"Error: {err}")
        else: 
            print(response.json())

        time.sleep(3)
"""
# Processes the JSON file and extracts the relevant data
    # gets the numerical value of each individual JSON param
def dataProcessing():

    data = {}
    timestamp = []
    dTime = deque(maxlen=20)
    dData = deque(maxlen=20)
    start = time.time()

    while time.time() - start < 15:
        try:
            response = rq.get("http://192.168.102.101/api/status.json")
            response.raise_for_status()
            payload = response.json()
        except rq.exceptions.RequestException as err:
            print(f"Error: {err}")
        else:
            timestamp.append(time.time())
            for key, value in payload.items():
                data.setdefault(key, []).append(value)

            dTime.append(timestamp)
            dData.append(data)

            time.sleep(5)

    print(f"timestamp: {dTime}")
    print(f"dData: {dData}")

    # assigns a time/date tag to the current response param
    # saves individual response params into arrays along with the associated tag?

# Uploads the data to an InfluxDB database

# Main
def main():
    """
    print("Static IP (without mask):")
    ip = input()
    print("interface")
    interface = input()

    ipAssignment(interface, ip)
    
    print("Modem IP:")
    modemIP = input()
    pingTest(modemIP)

    getJSON()
    """

    dataProcessing()
    
if __name__ == "__main__":
    main()

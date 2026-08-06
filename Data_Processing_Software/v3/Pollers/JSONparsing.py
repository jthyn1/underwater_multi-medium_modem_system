import requests
import json
from .decoder import OPTICALSTATUS

# Reads JSON data from the modem's API
def readJson():
    try:
        while True:
            response = requests.get("http://192.168.102.101/api/status.json")
            if response.status_code == 200:
                json_data = response.json()
                return json_data
            else:
                print(f"Error: Received status code {response.status_code}")
    except requests.RequestException as e:
        print(f"Request error: {e}")

# Encodes JSON data into bytes for transmission
def encodeJSON(data):
    return OPTICALSTATUS, json.dumps(data).encode('utf-8')

def JSONmain():
    value = readJson()
    JSONtag, encoded_data = encodeJSON(value)
    return JSONtag, encoded_data
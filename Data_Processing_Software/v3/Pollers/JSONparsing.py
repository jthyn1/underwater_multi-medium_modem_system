import requests
import json
from .decoder import OPTICALSTATUS

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
    
def encodeJSON(data):
    return OPTICALSTATUS, json.dumps(data).encode('utf-8')

def JSONmain():
    value = readJson()
    encoded_data = encodeJSON(next(value))
    yield encoded_data
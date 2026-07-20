import requests as rq
import json
from requests.exceptions import rqEx
import time
import influxdb-client as influx

response = rq.get("http://192.168.102.101/api/status.json")
print(response.json())



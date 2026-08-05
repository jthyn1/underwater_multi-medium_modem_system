import requests
import time 

def main():

    try:
        while True:
            response = requests.get("http://192.168.102.101/api/status.json")
            print(response.json())
            time.sleep(1)
    except KeyboardInterrupt:
        print("Exiting...")
        exit(0)
if __name__ == "__main__":
   main()
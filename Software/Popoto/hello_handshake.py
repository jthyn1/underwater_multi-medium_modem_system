from popoto import popoto as popoto_mod
import time
import json

MODEM_IP = "10.0.0.132"     # change per modem
BASE_PORT = 17000

ROLE = "B"                 # "A" = initiator, "B" = responder
HELLO_MSG = "HELLO_FROM_A"
ACK_MSG   = "ACK_FROM_B"

TIMEOUT = 15               # seconds

def send_payload(p, data):
    cmd = {
        "Command": "TransmitJSON",
        "Arguments": {
            "Payload": {
                "Data": data
            }
        }
    }
    p.send(json.dumps(cmd))


def is_payload(msg):
    """Return payload string if acoustic payload exists"""
    try:
        if "Payload" in msg and "Data" in msg["Payload"]:
            return msg["Payload"]["Data"]
    except Exception:
        pass
    return None


def initiator(p):
    print("[A] Sending HELLO")
    send_payload(p, HELLO_MSG)

    start = time.time()
    while time.time() - start < TIMEOUT:
        reply = p.waitForReply(2)
        if not reply:
            continue

        payload = is_payload(reply)
        if payload:
            print(f"[A] Received acoustic payload: {payload}")
            if payload == ACK_MSG:
                print("[A] Handshake complete")
                return

    print("[A] Handshake failed (timeout)")


def responder(p):
    print("[B] Waiting for HELLO")

    while True:
        reply = p.waitForReply(5)
        if not reply:
            continue

        payload = is_payload(reply)
        if payload:
            print(f"[B] Received acoustic payload: {payload}")
            if payload == HELLO_MSG:
                print("[B] Sending ACK")
                send_payload(p, ACK_MSG)
                return


def main():
    p = popoto_mod.popoto(MODEM_IP, BASE_PORT)

    # Required for acoustic command forwarding
    p.isRemoteCmd = False
    p.remoteCommandAck = False

    # Sanity check
    p.send("getVersion")
    print(p.waitForReply(5))

    if ROLE == "A":
        initiator(p)
    else:
        responder(p)

    p.tearDownPopoto()


if __name__ == "__main__":
    main()

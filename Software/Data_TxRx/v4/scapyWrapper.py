#scapyWrapper.py
import time
from scapy.all import Packet, Ether, IP, UDP, ShortField, XByteField, IntField, StrLenField, LongField
from scapy.all import bind_layers, sendp
import Pollers.JSONparsing as JSONparsing
import Pollers.serialparsing as serialparsing
from Pollers.decoder import SENSORS, OPTICALSTATUS, UWO, UWA
import queue
import threading
import struct

frame_queue = queue.Queue(maxsize=100)
data_queue = queue.Queue(maxsize=100)
DATA_TAGS = frozenset({SENSORS, OPTICALSTATUS})
FRAME_DEADLINE = 2.0

# Defines telemetry packet
class TelemetryPacket(Packet):
    name = "TelemetryPacket"
    fields_desc = [
        ShortField("seq_num", 0),
        XByteField("mask", 0),
        LongField("acq_ns", 0),
        IntField("payload_len", 0),
        StrLenField("data_payload", b"", length_from=lambda pkt: pkt.payload_len),
        XByteField("Link", 0),

    ]

# Defines contents of tx frame
class Frame:
    __slots__=("seq", "acq_ns", "mask", "payload")

    def __init__(self, seq, acq_ns, mask, payload):
        self.seq, self.acq_ns, self.mask, self.payload = seq, acq_ns, mask, payload

def build_frame(seq, slots):
    # Slots = {tag: bytes}
    acq_ns = time.time_ns() # time when the frame is built
    mask = 0
    blocks = []
    for tag in sorted(slots):
        mask |= 1 << (tag - 1) # Defines mask that shows bitwise what data passed through and what got dropped
        body = slots[tag] # body based on data added to slots in framer
        blocks.append(struct.pack(">BH", tag, len(body)+body)) # create the struct (len(body) enables header reading of when data starts/ends)
    return Frame(seq, acq_ns, mask, b"".join(blocks)) # joins all data as bytes into a single payload outlined by Frame class)


# Queue sensor data
def qSensor(q):
    reader = serialparsing.readTeensy()
    for line in reader:
        q.put(serialparsing.serialMain(line))

# Queue JSON data
def qJSON(q):
    while True:
        q.put(JSONparsing.JSONmain())
        time.sleep(1)


def framer(data_queue, frame_queue):
    
    seq = 0
    pending = None

    while True:
        slots = {}
        deadline = None

        # If there is still data pending, get it, reset pending, and assign it to slots, then start counting deadline
        if pending is not None:
            tag, data = pending
            pending = None
            slots[tag] = data
            deadline = time.monotonic() + FRAME_DEADLINE

        # If there is data missing (based on tags) and the deadline is not met, continue, otherwise reset
        while DATA_TAGS - slots.keys():
            if deadline is None:
                timeout = None
            else:
                timeout = deadline - time.monotonic()
                if timeout <= 0:
                    break

            # Get data from data queue, if no data, break
            try:
                tag, data = data_queue.get(timeout=timeout)
            except queue.Empty:
                break
            data_queue.task_done()

            # If there is a tag in slots, add the tag and data to pending, then assign data to slots based on tag
            if tag in slots:
                pending = (tag, data)
                break
            slots[tag] = data

            # if the deadline isn't assigned, start counting
            if deadline is None:
                deadline = time.monotonic() + FRAME_DEADLINE

        # if slots isnt empty, increment sequence and build the frame with the data in slots
        if slots:
            seq = (seq + 1) & 0xFFFF
            frame_queue.put(build_frame(seq, slots))


# Defines port # and transmission protocol
PORT = 5555
bind_layers(UDP, TelemetryPacket, dport=PORT)

# Sends packed data to other pi ip through ethernet
def txProtocol_UWO(frame_queue):

    while True:
        frame = frame_queue.get()
        dataLabel = b"data payload"
        pkt = (
            Ether(dst="AA:BB:CC:DD:EE:FF") #Rx MAC address
            / IP(src="192.168.102.100", dst="192.168.102.103")
            / UDP(sport=5000, dport=PORT)
            / TelemetryPacket(
                seq_num=frame.seq,
                mask=frame.mask,
                acq_ns=frame.acq_ns,
                payload_len=len(frame.payload),
                data_payload=frame.payload,
                link=UWO,
            )
        )
        pkt.show()
        sendp(pkt, iface="enx00e04c2f1a80") #Tx device
        frame_queue.task_done()

def main():
    threading.Thread(target=qSensor, args=(data_queue,), daemon=True).start()
    threading.Thread(target=qJSON, args=(data_queue,), daemon=True).start()
    threading.Thread(target=framer, args=(data_queue, frame_queue), daemon=True).start()
    try:
        txProtocol_UWO(frame_queue)
    except KeyboardInterrupt:
        return

if __name__ == "__main__":
   main()

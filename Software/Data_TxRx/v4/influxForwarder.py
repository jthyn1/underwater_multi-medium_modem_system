# influxForwarder
import sqlite3
from sqlite3 import Row
import time
from queue import Queue, Empty
import requests
import threading
from spool import PENDING, IN_FLIGHT, RECEIVED, QUARANTINED
from dataclasses import dataclass


@dataclass(frozen=True)
class Batch:
    seqs: tuple[int, ]


# Read from spool.db
# enqueue information from spool.db
TIMEOUT = 0.5
attempts = 0
data_queue = Queue(maxsize=100)
status_queue = Queue(maxsize=100)

conn = sqlite3.connect("spool.db")
cur = conn.cursor()

def claim_batch(conn, limit=50):
    rows = conn.execute(
        "SELECT id, frame FROM spool WHERE state = ? ORDER BY id LIMIT ?",
        (PENDING, limit)).fetchall()
    if not rows:
        return None
    seqs = tuple(r[0] for r in rows)
    placeholders = ",".join("?" * len(seqs))
    conn.execute(
        f"UPDATE spool SET state = ?, attempts = attempts + 1 "
        f"WHERE id IN ({placeholders})",
        (IN_FLIGHT, *seqs))
    conn.commit()          # lease is durable BEFORE the batch goes on the queue
    return Batch(seqs, pack(rows))

def spool_manager(conn, data_queue, status_queue, stop):
    while not stop.is_set():
        while True:
            try:
                stats = status_queue.get_nowait()
            except Empty:
                break
            apply_result(conn, stats)

    batch = claim_batch(conn, limit=BATCH_SIZE)

def enqueue_data(q):
    cur.execute("SELECT * FROM spool")    
    rows = cur.fetchall()
    for Row in rows:
        q.put(rows, timeout=TIMEOUT)

# send queued data over halow to influxdb server, set flag to 'in flight', have timeout for 'failed to send'
def txHaLow(reqses, data_queue, status_queue):
    blob = data_queue.get(timeout=TIMEOUT)
    state = IN_FLIGHT
    try:
        # Connect to session, upload to inlfuxdb http host
        session = reqses.post(url="http://localhost:8086", data=blob, headers={"content type": "stream"}, timeout=(5,30))
        # Error connecting to server
    except requests.exceptions.RequestException as e:
        print(f"Error connecting: {e}")
        attempts += 1
        return 0
        # Successful upload (http codes 200, 201, 204)
    if session.status_code // 100 == 2:
        state = RECEIVED
        # Get seq_num from blob
        # Enqueue seq_num + RECEIVED status in status_queue
        attempts = 0
        return 0
        # Http error
    if (400 <= session.status_code < 500):
        print(f"Http error: {session.status_code}")
        attempts += 1
        return 0
        # Max attempts and not a requests issue
    if state == IN_FLIGHT and attempts >= 15 and not e:
        print(f"Too many attempts: {attempts}, http status: {session.status_code}")
        state = QUARANTINED
        # Enqueue seq_num + QUARANTINED status in status_queue
        return 1
    if state == IN_FLIGHT and attempts >= 15 and e:
        print(f"Max attempts in requestsException: {e}")
        return 1    

def forwarderState(status_queue):
    # Get status_queue and read seq_num and status
    if state == RECEIVED:
        # Return to spool.py
    if state == QUARANTINED:
        # Return to quarantine.py

def main():
    s = requests.Session()
    threading.Thread(target=enqueue_data, args=(data_queue, status_queue), daemon=True).start()
    try:
        while True:
            txHaLow(s, data_queue, status_queue)
    finally:
        conn.close()
        s.close()

if __name__ == "__main__":
    main()
    

# receive an ack for the data received and *written to* influxdb and pass that ack to spool
"""side note: add clearing metric for spool based on written state"""
# gather HaLow performance metrics, pass to spool
# error handling across reqs

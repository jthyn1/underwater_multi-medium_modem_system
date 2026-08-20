#spool.py

import sqlite3
from dataclasses import asdict, astuple, dataclass
import time
import receiver

# states of data
ABSENT = 0
IN_FLIGHT = 1
RECEIVED = 2 # And acknowledged
QUARANTINED = 3 # Tried and failed multiple times, separated from the rest of data for analysis

conn = sqlite3.connect('spool.db')
cur = conn.cursor()

@dataclass
class receiverSeq:
    id: int
    acq_ns: int
    src: str
    link: str
    payload: bytes

r = asdict(receiver.handle_packet())


SCHEMA = """
CREATE TABLE IF NOT EXISTS spool (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,  
    acq_ns   INTEGER NOT NULL,      -- nanoseconds since epoch
    ins_ns   INTEGER NOT NULL,      -- ns when stored on pi
    src      TEXT NOT NULL,
    link     TEXT NOT NULL,
    seq_num  INTEGER NOT NULL,
    payload  BLOB NOT NULL,
    state    INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    CHECK (state IN (0,1,2,3)),     -- check to ensure state is valid
); STRICT

CREATE INDEX IF NOT EXISTS idx_pending ON reading(id) WHERE state = 0;

SELECT id, acq_ns, src, link, payload
FROM spool
WHERE state = 0 AND attempts < 5

UPDATE spool SET state = 3 WHERE state = 0 AND attempts >= 5;  -- isolate 5+ retry queries
"""

PRAGMA_DEF = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;"
"""

conn.execute(PRAGMA_DEF)
conn.execute(SCHEMA)

def insertPayload():
    while True:
        conn.executemany(
            "INSERT INTO spool (id, acq_ns, ins_ns, src, link, payload, state, attempts)"
            "VALUES (:id, :acq_ns, insTime, :src, :link, :payload, 0,0)"
            {**asdict(r), "ins_ns": time.time()}
        time.sleep(1)
)








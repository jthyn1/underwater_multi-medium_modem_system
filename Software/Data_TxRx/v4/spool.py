import sqlite3
import time
from collections import defaultdict
from queue import Queue, Empty

import receiver

PENDING, IN_FLIGHT, RECEIVED, QUARANTINED = 0, 1, 2, 3
MAX_ATTEMPTS = 5
BATCH_MAX = 64
BATCH_TIMEOUT = 0.5
SWEEP_INTERVAL = 60.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS spool (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    acq_ns   INTEGER NOT NULL,      -- ns since epoch, at acquisition
    ins_ns   INTEGER NOT NULL,      -- ns when written to this pi
    mask     INTEGER NOT NULL,
    link     INTEGER NOT NULL,
    seq_num  INTEGER NOT NULL,
    payload  BLOB    NOT NULL,
    state    INTEGER NOT NULL DEFAULT 0,
    attempts INTEGER NOT NULL DEFAULT 0,
    CHECK (state IN (0, 1, 2, 3))
) STRICT;

CREATE INDEX IF NOT EXISTS idx_pending ON spool(id)
    WHERE state = 0 AND attempts < 5;
"""

INSERT_SQL = (
    "INSERT INTO spool "
    "(acq_ns, ins_ns, mask, link, seq_num, payload, state, attempts) "
    "VALUES (?, ?, ?, ?, ?, ?, 0, 0)"
)

SELECT_PENDING_SQL = (
    "SELECT id, acq_ns, mask, link, payload FROM spool "
    "WHERE state = 0 AND attempts < ? ORDER BY id LIMIT ?"
)

QUARANTINE_SQL = "UPDATE spool SET state = 3 WHERE state = 0 AND attempts >= ?"


def open_db(path="spool.db"):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")     # persistent, but harmless to repeat
    conn.execute("PRAGMA synchronous=NORMAL")   # per-connection, must be set every time
    conn.executescript(SCHEMA)
    return conn


def drain(q, max_n, timeout):
    out = []
    try:
        out.append(q.get(timeout=timeout))
    except Empty:
        return out
    while len(out) < max_n:
        try:
            out.append(q.get_nowait())
        except Empty:
            break
    return out


def main():
    conn = open_db()
    q = Queue(maxsize=1000)
    stats = defaultdict(int)
    sniffer = receiver.startSniff(q, stats)
    last_sweep = time.monotonic()
    try:
        while True:
            batch = drain(q, BATCH_MAX, BATCH_TIMEOUT)
            if batch:
                ins_ns = time.time_ns()
                rows = [(acq_ns, ins_ns, mask, link, seq_num, payload)
                        for (acq_ns, mask, link, seq_num, payload) in batch]
                with conn:
                    conn.executemany(INSERT_SQL, rows)
                stats["rows_written"] += len(rows)

            now = time.monotonic()
            if now - last_sweep >= SWEEP_INTERVAL:
                print(f"spool: {dict(stats)}", flush=True)
                with conn:
                    conn.execute(QUARANTINE_SQL, (MAX_ATTEMPTS,))
                print(f"spool: {dict(stats)}", flush=True)
                last_sweep = now
    except KeyboardInterrupt:
        pass
    finally:
        sniffer.stop()
        conn.close()


if __name__ == "__main__":
    main()




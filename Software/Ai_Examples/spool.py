from __future__ import annotations

import functools
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable, Sequence

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

PENDING = 0
INFLIGHT = 1
DELIVERED = 2
QUARANTINED = 3   # exceeded max_attempts; kept for post-mortem, never retried

# ---------------------------------------------------------------------------
# Capability probe
# ---------------------------------------------------------------------------
#
# UPDATE ... RETURNING landed in SQLite 3.35 (2021). Ubuntu 24.04 ships 3.45,
# so on the CM5 this is always True. The probe exists because the same file
# may end up on some minimal image with an older library, and the fallback
# path below is correct-but-slower rather than broken.

_HAS_RETURNING = sqlite3.sqlite_version_info >= (3, 35, 0)

# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------
#
# STRICT means SQLite actually enforces column types instead of happily
# storing a string in an INTEGER column. It is a modern feature (3.37+);
# Ubuntu 24.04 ships 3.45, so you are fine.
#
# The partial index is the performance-critical line. Without it, every poll
# for work does a full table scan. With it, SQLite maintains a small index
# containing ONLY the pending rows — which is exactly the set the forwarder
# asks for, and it shrinks as rows are delivered.

SCHEMA = """
CREATE TABLE IF NOT EXISTS spool (
    id       INTEGER PRIMARY KEY,          -- rowid alias; monotonic, free
    acq_ns   INTEGER NOT NULL,             -- acquisition time, ns since epoch
    src      TEXT    NOT NULL,             -- e.g. 'teensy', 'popoto', 'luma'
    link     TEXT    NOT NULL,             -- e.g. 'acoustic', 'optical'
    payload  BLOB    NOT NULL,             -- packed struct or JSON bytes
    state    INTEGER NOT NULL DEFAULT 0,   -- 0 pending / 1 in-flight / 2 done / 3 quarantined
    attempts INTEGER NOT NULL DEFAULT 0,   -- poison-row detection
    ins_ns   INTEGER NOT NULL,             -- receipt time; latency diagnostics
    CHECK (state IN (0, 1, 2, 3))          -- costs nothing, catches a typo'd UPDATE
) STRICT;

CREATE INDEX IF NOT EXISTS ix_spool_pending
    ON spool (acq_ns)
    WHERE state = 0;

-- Trim walks delivered rows oldest-first every time it runs. Without this
-- second partial index that is a scan of the whole table; with it, it is a
-- short walk down a small index. Same trick as ix_spool_pending, applied to
-- the other end of the row's life.
CREATE INDEX IF NOT EXISTS ix_spool_delivered
    ON spool (acq_ns)
    WHERE state = 2;
"""


# ---------------------------------------------------------------------------
# Thread safety
# ---------------------------------------------------------------------------
#
# In production the collector and the forwarder are separate PROCESSES, each
# with its own connection — WAL mode is what makes that work, and it is the
# arrangement to prefer, because it lets you restart the forwarder without
# interrupting acquisition.
#
# But a Spool is easy to share between THREADS by accident (a test harness, a
# quick script), and Python's sqlite3 refuses cross-thread use by default with
# an error that reads like a bug in this file. Allowing it is not enough:
# SQLite's own thread safety protects individual statements, and the methods
# below issue BEGIN, then work, then COMMIT. Two threads interleaving there
# would nest transactions on one connection and corrupt the state machine, so
# the lock is doing real work rather than being defensive decoration.
#
# It is an RLock so a method may call another without deadlocking itself.


def _locked(fn):
    @functools.wraps(fn)
    def wrapper(self, *args, **kwargs):
        with self._lock:
            return fn(self, *args, **kwargs)

    return wrapper


class Spool:
    """A bounded, crash-safe FIFO of telemetry rows awaiting upload."""

    def __init__(self, path: str | Path, max_rows: int = 500_000):
        self.path = Path(path)
        self.max_rows = max_rows
        # Cumulative counters for the health metrics. Process-lifetime only —
        # they reset on restart, which is what a *_total counter is supposed
        # to do; the server side handles counter resets.
        self.dropped_delivered_total = 0
        self.dropped_quarantined_total = 0
        self.dropped_pending_total = 0
        # See the note above _locked(): this serializes multi-statement
        # transactions, which SQLite's own thread safety does not.
        self._lock = threading.RLock()
        self._conn = self._connect()
        self._conn.executescript(SCHEMA)

    # -- connection ---------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """Open the database and configure it.

        Two things here are easy to get wrong:

        1. `isolation_level=None` switches OFF Python's implicit transaction
           layer. By default the sqlite3 module silently opens a transaction
           before your INSERT/UPDATE and leaves it open until you call
           .commit() — including across statements you didn't think were
           related. Turning it off means every statement autocommits unless
           you type BEGIN yourself, which is far easier to reason about.

        2. journal_mode is PERSISTENT — it is written into the database file
           and survives reopening. synchronous and busy_timeout are
           PER-CONNECTION and must be re-issued every single time you open
           the file. Forgetting this is the classic "why is it slow / why do
           I get 'database is locked'" bug.
        """
        conn = sqlite3.connect(
            self.path, isolation_level=None, timeout=5.0, check_same_thread=False
        )
        conn.row_factory = sqlite3.Row  # rows behave like dicts: row["acq_ns"]
        conn.execute("PRAGMA journal_mode = WAL")     # persistent
        conn.execute("PRAGMA synchronous  = NORMAL")  # per-connection
        conn.execute("PRAGMA busy_timeout = 5000")    # per-connection, ms
        return conn

    @_locked
    def close(self) -> None:
        self._conn.close()

    # Context-manager sugar, so a forwarder can do `with Spool(path) as s:`
    # and not leak the handle when it dies on an exception.
    def __enter__(self) -> "Spool":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- write path (worked example) ----------------------------------------

    @_locked
    def append(self, acq_ns: int, src: str, link: str, payload: bytes) -> int:
        """Add one reading to the spool. Returns the new row id.

        Note the `?` placeholders. Never build SQL with f-strings or `%` —
        the placeholder is not only injection-safe, it also passes the Python
        value straight through to SQLite's C layer without going via text,
        so an int stays an int.

        The tuple is the second argument to execute(). A one-element tuple
        needs the trailing comma: `(value,)`, not `(value)`.
        """
        cur = self._conn.execute(
            "INSERT INTO spool (acq_ns, src, link, payload, state, attempts, ins_ns) "
            "VALUES (?, ?, ?, ?, ?, 0, ?)",
            (acq_ns, src, link, payload, PENDING, time.time_ns()),
        )
        return cur.lastrowid

    @_locked
    def append_many(self, rows: Iterable[tuple[int, str, str, bytes]]) -> None:
        """Insert a batch inside ONE transaction.

        With autocommit on, each append() is its own transaction and costs an
        fsync. At 1 Hz that is irrelevant. If you ever burst-load (replaying a
        file, draining a modem buffer), wrap it like this and watch the wall
        time drop by an order of magnitude.
        """
        now = time.time_ns()
        self._conn.execute("BEGIN")
        try:
            self._conn.executemany(
                "INSERT INTO spool (acq_ns, src, link, payload, state, attempts, ins_ns) "
                "VALUES (?, ?, ?, ?, 0, 0, ?)",
                [(a, s, l, p, now) for (a, s, l, p) in rows],
            )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    # -- read path (worked example) -----------------------------------------

    @_locked
    def pending_count(self) -> int:
        """Feeds the `spool_pending` health metric."""
        row = self._conn.execute(
            "SELECT COUNT(*) AS n FROM spool WHERE state = ?", (PENDING,)
        ).fetchone()
        return row["n"]

    # -- forwarder path -----------------------------------------------------

    @_locked
    def claim_batch(
        self, limit: int = 500, max_attempts: int | None = None
    ) -> list[sqlite3.Row]:
        """Move up to `limit` oldest pending rows to in-flight and return them.

        Atomicity is the whole point. The read (which rows are pending?) and
        the write (mark them in-flight) must not be separable, or two
        forwarder passes — a retry timer firing while the previous pass is
        still blocked on a slow HaLow write, say — can claim the same row
        twice and burn link budget on a duplicate upload.

        `attempts` increments HERE, on claim, not at ack time. It counts
        tries, not successes, which is exactly what poison-row detection
        needs: a row that crashes the gateway every time never acks, so an
        ack-time counter would sit at zero forever.

        `max_attempts` skips rows that have already been tried that many
        times. They stay pending and stay at the head of the queue (ORDER BY
        acq_ns is unchanged), they are just no longer selected — so they no
        longer block the rows behind them, and `trim()` eventually reaps them
        as the oldest pending rows. Pass None (default) to retry forever.

        Rows come back sorted by acq_ns. RETURNING makes no ordering promise —
        it hands rows back in whatever order the UPDATE touched them — so the
        sort is done in Python. It matters: the gateway batches these into one
        InfluxDB write and monotonic timestamps compress better.
        """
        if limit <= 0:
            return []

        # The subselect is where the ordering and the index live. `state = 0`
        # is written as a literal, not a parameter: to use a partial index
        # SQLite has to prove the query only touches rows the index covers,
        # which it does by matching the index's WHERE clause against the
        # query's WHERE terms. A literal always matches. (Modern SQLite can
        # usually manage it with a bound value too, but the literal removes
        # the question, and the alternative is a silent full table scan on
        # every poll. Check with EXPLAIN QUERY PLAN if you change this —
        # you want "USING COVERING INDEX ix_spool_pending".)
        select_ids = (
            "SELECT id FROM spool WHERE state = 0"
            + ("" if max_attempts is None else " AND attempts < :max_attempts")
            + " ORDER BY acq_ns LIMIT :limit"
        )
        params = {"limit": limit}
        if max_attempts is not None:
            params["max_attempts"] = max_attempts

        cols = "id, acq_ns, src, link, payload, attempts, ins_ns"

        if _HAS_RETURNING:
            # One statement. A single statement in autocommit mode is its own
            # transaction, so there is no window between the read and the
            # write for anyone to squeeze into.
            #
            # Caveat worth knowing: with RETURNING, SQLite does not finish
            # applying the statement until the cursor has been stepped to
            # exhaustion. fetchall() immediately — do not hand this cursor to
            # a caller to iterate lazily, and do not `break` out of it.
            cur = self._conn.execute(
                f"UPDATE spool SET state = {INFLIGHT}, attempts = attempts + 1 "
                f"WHERE id IN ({select_ids}) "
                f"RETURNING {cols}",
                params,
            )
            rows = cur.fetchall()
        else:
            # Fallback for SQLite < 3.35. BEGIN IMMEDIATE takes the write
            # lock up front, so the SELECT and the UPDATE see the same
            # database. A plain BEGIN would take only a read lock and could
            # fail at the UPDATE with SQLITE_BUSY after doing the work.
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                rows = self._conn.execute(
                    f"SELECT {cols} FROM spool WHERE id IN ({select_ids})", params
                ).fetchall()
                if rows:
                    ids = [r["id"] for r in rows]
                    self._conn.execute(
                        f"UPDATE spool SET state = {INFLIGHT}, attempts = attempts + 1 "
                        f"WHERE id IN ({_placeholders(ids)})",
                        ids,
                    )
                self._conn.execute("COMMIT")
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            # The SELECT read `attempts` before the UPDATE bumped it. Patch
            # the returned rows so both paths hand back the same numbers.
            rows = [_row_with(r, attempts=r["attempts"] + 1) for r in rows]

        return sorted(rows, key=lambda r: r["acq_ns"])

    @_locked
    def mark_delivered(self, ids: Sequence[int]) -> None:
        """Called ONLY after the server confirms a durable write.

        Never on a 2xx from an async client that has merely buffered the
        points — flush explicitly, get the confirmation, then call this. The
        whole safety argument (at-least-once delivery over idempotent writes)
        collapses if a row can be marked delivered before it is durable on
        the server: that is the one failure mode that silently loses data
        instead of merely duplicating it.

        The state guard (`AND state = 1`) means a stale ack for a row that
        requeue_inflight() already pulled back to pending is ignored rather
        than resurrecting a delivered state on a row currently in flight.
        """
        ids = list(ids)
        if not ids:
            return

        # SQLite caps host parameters per statement (999 on older builds,
        # 32766 since 3.32). Chunk well under the floor so this is safe on
        # any library the file might land on.
        self._conn.execute("BEGIN")
        try:
            for chunk in _chunks(ids, 400):
                # `IN (?)` does not expand a list — you build the placeholder
                # string yourself. This is the one legitimate use of string
                # formatting in SQL: you are generating placeholders, not
                # interpolating values. The values still ride in as bindings.
                self._conn.execute(
                    f"UPDATE spool SET state = {DELIVERED} "
                    f"WHERE state = {INFLIGHT} AND id IN ({_placeholders(chunk)})",
                    chunk,
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_locked
    def release(self, ids: Sequence[int]) -> None:
        """Put in-flight rows back to pending after a TRANSIENT failure.

        Without this, a link blip strands rows in in-flight until the next
        process restart — they are not lost, but they stop moving, and the
        symptom is a spool that grows while `spool_inflight` sits at a
        constant non-zero number.

        `attempts` is deliberately NOT decremented. It was incremented at
        claim time and it counts tries; a row released ten times has genuinely
        been tried ten times, and that is what quarantine_over() needs to know.
        """
        ids = list(ids)
        if not ids:
            return
        self._conn.execute("BEGIN")
        try:
            for chunk in _chunks(ids, 400):
                self._conn.execute(
                    f"UPDATE spool SET state = {PENDING} "
                    f"WHERE state = {INFLIGHT} AND id IN ({_placeholders(chunk)})",
                    chunk,
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_locked
    def quarantine(self, ids: Sequence[int]) -> None:
        """Set aside rows the gateway has rejected as PERMANENTLY bad.

        A poison row — a malformed payload, an unparseable value, a field
        whose type conflicts with what InfluxDB already stores — fails on
        every retry, forever. Left pending it sits at the head of the queue
        and blocks everything behind it, and the symptom is "no data since
        Tuesday" on a spool that is full and busy.

        Quarantine rather than delete, so there is something to look at. It
        consumes spool space indefinitely, which is why trim() sacrifices
        quarantined rows immediately after delivered ones.
        """
        ids = list(ids)
        if not ids:
            return
        self._conn.execute("BEGIN")
        try:
            for chunk in _chunks(ids, 400):
                self._conn.execute(
                    f"UPDATE spool SET state = {QUARANTINED} "
                    f"WHERE state IN ({PENDING}, {INFLIGHT}) AND id IN ({_placeholders(chunk)})",
                    chunk,
                )
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    @_locked
    def quarantine_over(self, max_attempts: int) -> int:
        """Sweep rows that have burned through their retry budget.

        The backstop for failures the gateway reports as transient but which
        are actually permanent — the ambiguous cases Chapter 30.3 says to
        treat as transient on purpose, so they fail slowly and visibly rather
        than quickly and silently. This is where "slowly and visibly" ends.
        """
        cur = self._conn.execute(
            f"UPDATE spool SET state = {QUARANTINED} "
            f"WHERE state = {PENDING} AND attempts >= ?",
            (max_attempts,),
        )
        return cur.rowcount

    @_locked
    def requeue_inflight(self) -> int:
        """Called once at startup. Returns the number of rows recovered.

        Anything still in-flight when the process died is, by definition, a
        row we do not know the fate of: it may never have left the Pi, or it
        may have landed and had its ack lost. Send it again. That is safe
        because the InfluxDB point identity (measurement + tags + field +
        acquisition timestamp) is unchanged on the retry, so a second write
        overwrites the first rather than duplicating it — which is exactly
        why the acquisition timestamp must never be re-stamped at retry time.

        Deliberately does NOT reset `attempts`. A row that has been claimed
        six times across three reboots is still a poison-row suspect.
        """
        cur = self._conn.execute(
            f"UPDATE spool SET state = {PENDING} WHERE state = {INFLIGHT}"
        )
        return cur.rowcount

    @_locked
    def trim(self) -> int:
        """Enforce the row bound. Returns rows deleted.

        Order of sacrifice:
          1. delivered rows, oldest first — pure history, already on the
             server, costs nothing to lose;
          2. quarantined rows, oldest first — known-bad, kept only for
             post-mortem, and the post-mortem is less valuable than the data
             still trying to get out;
          3. oldest pending rows — real data loss, counted separately so the
             `rows_dropped_total` metric can alarm on it;
          4. in-flight rows are never touched. A delete under a request in
             progress would let a successful ack arrive for a row that no
             longer exists, and mark_delivered would silently no-op.

        Only runs when over the bound, so the common case is one COUNT(*)
        against a small table and no writes at all.

        NOTE: DELETE ... LIMIT needs a compile-time option that is not
        universally enabled, so the LIMIT goes in a subselect instead. That
        form works everywhere.
        """
        total = self._conn.execute("SELECT COUNT(*) AS n FROM spool").fetchone()["n"]
        overflow = total - self.max_rows
        if overflow <= 0:
            return 0

        deleted = 0
        self._conn.execute("BEGIN")
        try:
            cur = self._conn.execute(
                "DELETE FROM spool WHERE id IN ("
                "  SELECT id FROM spool WHERE state = 2 ORDER BY acq_ns LIMIT ?"
                ")",
                (overflow,),
            )
            self.dropped_delivered_total += cur.rowcount
            deleted += cur.rowcount

            still_over = overflow - deleted
            if still_over > 0:
                cur = self._conn.execute(
                    "DELETE FROM spool WHERE id IN ("
                    "  SELECT id FROM spool WHERE state = 3 ORDER BY acq_ns LIMIT ?"
                    ")",
                    (still_over,),
                )
                self.dropped_quarantined_total += cur.rowcount
                deleted += cur.rowcount

            still_over = overflow - deleted
            if still_over > 0:
                # ---- POLICY HOOK -------------------------------------------
                # Everything delivered is gone and we are still over the
                # bound, so from here on trimming destroys data that has
                # never reached the server. Current policy: drop-oldest —
                # keep the most recent window, lose the front of the outage.
                #
                # The alternative is decimation: delete every Nth pending row
                # instead of the oldest N, trading resolution for an unbroken
                # (if coarse) record across the whole outage. Which is right
                # depends on whether a gap or a lower sample rate hurts more
                # downstream, and that is not decided yet.
                #
                # To swap policies, replace the id-selection subquery below.
                # Decimate-by-row-position, for reference:
                #   SELECT id FROM (
                #     SELECT id, ROW_NUMBER() OVER (ORDER BY acq_ns) AS rn
                #     FROM spool WHERE state = 0
                #   ) WHERE rn % :every = 0 LIMIT :n
                # Nothing else in this class assumes drop-oldest.
                # -------------------------------------------------------------
                cur = self._conn.execute(
                    "DELETE FROM spool WHERE id IN ("
                    "  SELECT id FROM spool WHERE state = 0 ORDER BY acq_ns LIMIT ?"
                    ")",
                    (still_over,),
                )
                self.dropped_pending_total += cur.rowcount
                deleted += cur.rowcount

            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

        return deleted

    # -- housekeeping / observability ---------------------------------------

    @_locked
    def purge_delivered(self, older_than_ns: int | None = None) -> int:
        """Delete delivered rows outright. Returns rows deleted.

        trim() only fires under memory pressure; on a healthy link the table
        grows a tail of delivered history that is never needed again. Call
        this on a slow timer (hourly is plenty) to keep the file small and
        the indexes shallow.
        """
        if older_than_ns is None:
            cur = self._conn.execute("DELETE FROM spool WHERE state = 2")
        else:
            cur = self._conn.execute(
                "DELETE FROM spool WHERE state = 2 AND acq_ns < ?", (older_than_ns,)
            )
        self.dropped_delivered_total += cur.rowcount
        return cur.rowcount

    @_locked
    def vacuum(self) -> None:
        """Return free pages to the filesystem.

        Deletes leave free pages inside the file; SQLite reuses them but
        never shrinks the file on its own. On an SD card that matters. This
        rewrites the database and needs roughly 2x the file size in free
        space, so it is a maintenance-window operation, not something to run
        in the forward loop. Cannot run inside a transaction.
        """
        self._conn.execute("VACUUM")

    @_locked
    def stats(self) -> dict:
        """Snapshot for the health metrics described in the design.

        `oldest_pending_age_s` is the one to watch: it is the age of the head
        of the queue, which is the honest measure of how far behind the link
        is. Falls back to None on an empty spool.
        """
        row = self._conn.execute(
            "SELECT "
            "  SUM(state = 0) AS pending, "
            "  SUM(state = 1) AS inflight, "
            "  SUM(state = 2) AS delivered, "
            "  SUM(state = 3) AS quarantined, "
            "  COUNT(*)       AS total, "
            "  MIN(CASE WHEN state = 0 THEN acq_ns END) AS oldest_pending_ns, "
            "  MAX(CASE WHEN state = 0 THEN attempts END) AS max_attempts "
            "FROM spool"
        ).fetchone()

        oldest = row["oldest_pending_ns"]
        return {
            "spool_pending": row["pending"] or 0,
            "spool_inflight": row["inflight"] or 0,
            "spool_delivered": row["delivered"] or 0,
            "spool_quarantined": row["quarantined"] or 0,
            "spool_total": row["total"] or 0,
            "spool_capacity_frac": (row["total"] or 0) / self.max_rows,
            "oldest_pending_age_s": None if oldest is None else (time.time_ns() - oldest) / 1e9,
            "max_attempts": row["max_attempts"] or 0,
            # rows_dropped_total counts every row deleted before delivery
            # confirmation OR after being written off. Delivered rows are not
            # loss, so they are excluded — a counter that ticks during normal
            # operation is a counter nobody alerts on.
            "rows_dropped_total": self.dropped_pending_total + self.dropped_quarantined_total,
            "rows_dropped_pending_total": self.dropped_pending_total,
            "spool_bytes": self._file_bytes(),
        }

    def _file_bytes(self) -> int:
        """Database plus WAL. The WAL matters: §30.4's trap shows up here as a
        `-wal` file growing into the gigabytes while the `.db` stays small."""
        total = 0
        for suffix in ("", "-wal", "-shm"):
            try:
                total += (self.path.parent / (self.path.name + suffix)).stat().st_size
            except OSError:
                pass
        return total


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _placeholders(seq: Sequence) -> str:
    """'?,?,?' for a 3-element sequence."""
    return ",".join("?" * len(seq))


def _chunks(seq: Sequence, n: int):
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _row_with(row: sqlite3.Row, **overrides) -> dict:
    """Copy a Row into a plain dict with some columns replaced.

    sqlite3.Row is immutable, and only the pre-3.35 fallback path needs this,
    so the type difference is confined to a branch that never runs on the CM5.
    Both types support row["col"], which is all the forwarder uses.
    """
    d = dict(row)
    d.update(overrides)
    return d


# ---------------------------------------------------------------------------
# Smoke test — `python3 spool.py`
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import os

    db = "/tmp/spool_test.db"
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(db + suffix)
        except FileNotFoundError:
            pass

    s = Spool(db, max_rows=8)
    base = time.time_ns() - 60 * 1_000_000_000  # backdated so ages read sanely

    for i in range(5):
        s.append(base + i * 1_000_000_000, "teensy", "acoustic", b"\x00" * 42)
    print("pending after append:", s.pending_count())

    # Claim two, ack them: the normal happy path.
    batch = s.claim_batch(limit=2)
    print("claimed:", [(r["id"], r["attempts"]) for r in batch])
    assert [r["id"] for r in batch] == [1, 2], "should claim oldest first"
    s.mark_delivered([r["id"] for r in batch])
    print("pending after ack:", s.pending_count())

    # Claim two more and pretend the process dies mid-upload.
    lost = s.claim_batch(limit=2)
    print("claimed then abandoned:", [r["id"] for r in lost])
    s.close()

    s = Spool(db, max_rows=8)
    print("requeued at startup:", s.requeue_inflight())
    assert s.pending_count() == 3, "3 rows should be pending again"

    # Attempts survived the restart: rows 3 and 4 have been tried once.
    again = s.claim_batch(limit=3)
    print("re-claimed:", [(r["id"], r["attempts"]) for r in again])
    assert [r["attempts"] for r in again] == [2, 2, 1]

    # Poison-row filter: nothing left under 2 attempts once these are pending.
    s.requeue_inflight()
    print("under max_attempts=2:", [r["id"] for r in s.claim_batch(max_attempts=2)])
    s.requeue_inflight()

    # Overflow: 12 rows in a max_rows=8 spool. The 2 delivered rows go first,
    # then the 2 oldest pending.
    s.append_many(
        [(base + (10 + i) * 1_000_000_000, "luma", "optical", b"\xff" * 8) for i in range(7)]
    )
    print("before trim:", s.stats()["spool_total"], "rows")
    print("trimmed:", s.trim())
    st = s.stats()
    print("after trim:", {k: st[k] for k in ("spool_total", "spool_pending", "spool_delivered")})
    assert st["spool_total"] == 8
    assert st["spool_delivered"] == 0, "delivered rows are sacrificed first"
    assert s.dropped_pending_total == 2

    for row in s._conn.execute("SELECT id, acq_ns, src, state, attempts FROM spool"):
        print(dict(row))
    print("stats:", s.stats())
    s.close()
    print("OK")

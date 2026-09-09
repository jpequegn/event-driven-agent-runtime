"""Bounded local ledger. Artifact/event writes share each state transaction."""

import json
import os
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path

from event_runtime.contracts import Snapshot, canonical, digest


class Conflict(ValueError):
    pass


class IntegrityError(ValueError):
    pass


def identity():
    return uuid.uuid4().hex


class Store:
    def __init__(self, path: Path):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        self.db = sqlite3.connect(path, isolation_level=None, timeout=10)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA foreign_keys=ON")
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        version = self.db.execute("PRAGMA user_version").fetchone()[0]
        if version not in (0, 2):
            self.close()
            raise IntegrityError("unsupported database version")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS artifacts(
                hash TEXT PRIMARY KEY, body TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, trigger_key TEXT NOT NULL UNIQUE,
                fingerprint TEXT NOT NULL, snapshot TEXT NOT NULL REFERENCES artifacts(hash),
                context TEXT NOT NULL REFERENCES artifacts(hash), status TEXT NOT NULL,
                revision INTEGER NOT NULL DEFAULT 0,
                latest_brief TEXT REFERENCES artifacts(hash),
                latest_check TEXT REFERENCES artifacts(hash),
                outcome TEXT REFERENCES artifacts(hash), paused_from TEXT,
                parent TEXT REFERENCES runs(id), created_at INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS events(
                seq INTEGER PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                kind TEXT NOT NULL, parent INTEGER REFERENCES events(seq),
                artifact TEXT NOT NULL REFERENCES artifacts(hash), at INTEGER NOT NULL,
                previous TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS events_run ON events(run_id,seq);
            CREATE TRIGGER IF NOT EXISTS artifacts_no_update BEFORE UPDATE ON artifacts
                BEGIN SELECT RAISE(ABORT,'immutable artifact'); END;
            CREATE TRIGGER IF NOT EXISTS artifacts_no_delete BEFORE DELETE ON artifacts
                BEGIN SELECT RAISE(ABORT,'immutable artifact'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_update BEFORE UPDATE ON events
                BEGIN SELECT RAISE(ABORT,'append-only event'); END;
            CREATE TRIGGER IF NOT EXISTS events_no_delete BEFORE DELETE ON events
                BEGIN SELECT RAISE(ABORT,'append-only event'); END;
            CREATE TABLE IF NOT EXISTS seals(
                seq INTEGER PRIMARY KEY, previous TEXT NOT NULL,
                state_hash TEXT NOT NULL, hash TEXT NOT NULL);
            CREATE TRIGGER IF NOT EXISTS seals_no_update BEFORE UPDATE ON seals
                BEGIN SELECT RAISE(ABORT,'append-only seal'); END;
            CREATE TRIGGER IF NOT EXISTS seals_no_delete BEFORE DELETE ON seals
                BEGIN SELECT RAISE(ABORT,'append-only seal'); END;
            PRAGMA user_version=2;
        """)
        try:
            self.verify()
        except Exception:
            self.close()
            raise

    def close(self):
        self.db.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @contextmanager
    def transaction(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            self._verify(self.db)
            yield self.db
            current = self.projection(self.db)
            last = self.db.execute("SELECT * FROM seals ORDER BY seq DESC LIMIT 1").fetchone()
            if last is None or last["state_hash"] != current:
                seq, previous = (last["seq"] + 1, last["hash"]) if last else (1, "")
                if seq > 10_000:
                    raise ValueError("ledger transaction capacity reached")
                self.db.execute(
                    "INSERT INTO seals VALUES(?,?,?,?)",
                    (seq, previous, current, digest([seq, previous, current])),
                )
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    @staticmethod
    def put(c, value):
        body = canonical(value)
        if len(body.encode()) > 100_000:
            raise ValueError("artifact exceeds 100 KB")
        key = digest(value)
        if not c.execute("SELECT 1 FROM artifacts WHERE hash=?", (key,)).fetchone():
            count, size = c.execute(
                "SELECT COUNT(*),COALESCE(SUM(LENGTH(body)),0) FROM artifacts"
            ).fetchone()
            if count >= 5000 or size + len(body) > 20_000_000:
                raise ValueError("artifact store capacity reached")
        c.execute("INSERT OR IGNORE INTO artifacts VALUES(?,?)", (key, body))
        return key

    @staticmethod
    def get(c, key):
        row = c.execute("SELECT body FROM artifacts WHERE hash=?", (key,)).fetchone()
        if row is None:
            raise IntegrityError("missing artifact")
        value = json.loads(row[0])
        if digest(value) != key:
            raise IntegrityError("artifact hash mismatch")
        return value

    @classmethod
    def record(cls, c, run_id, kind, value, now, parent=None):
        last_time = c.execute("SELECT MAX(at) FROM events WHERE run_id=?", (run_id,)).fetchone()[0]
        if type(now) is not int or now < 0 or (last_time is not None and now < last_time):
            raise Conflict("invalid or regressing run clock")
        if parent is not None:
            row = c.execute("SELECT run_id FROM events WHERE seq=?", (parent,)).fetchone()
            if row is None or row[0] != run_id:
                raise Conflict("causal parent belongs to another run")
        last = c.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        seq, previous = (last[0] + 1, last[1]) if last else (1, "")
        if seq > 10_000:
            raise ValueError("ledger event capacity reached")
        artifact = cls.put(c, value)
        fields = [seq, run_id, kind, parent, artifact, now, previous]
        c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?)", (*fields, digest(fields)))
        return seq

    @classmethod
    def new_run(cls, c, snapshot: Snapshot, key: str, now: int, parent=None):
        snapshot = Snapshot.model_validate(snapshot.model_dump())
        if not key.strip() or len(key) > 128 or type(now) is not int or now < 0:
            raise ValueError("invalid trigger key/time")
        fingerprint = digest(snapshot)
        old = c.execute("SELECT * FROM runs WHERE trigger_key=?", (key,)).fetchone()
        if old:
            if old["fingerprint"] != fingerprint or old["parent"] != parent:
                raise Conflict("trigger key reused with different snapshot")
            return old["id"], False
        if c.execute("SELECT COUNT(*) FROM runs").fetchone()[0] >= 100:
            raise ValueError("run capacity reached")
        run_id = identity()
        snap = cls.put(c, snapshot)
        context = cls.put(c, snapshot.context)
        c.execute(
            """INSERT INTO runs
            (id,trigger_key,fingerprint,snapshot,context,status,parent,created_at)
            VALUES(?,?,?,?,?,'queued',?,?)""",
            (run_id, key, fingerprint, snap, context, parent, now),
        )
        cls.record(c, run_id, "run.created", {"snapshot": snap, "parent_run": parent}, now)
        return run_id, True

    def create(self, snapshot, key, now=0, parent=None):
        with self.transaction() as c:
            return self.new_run(c, snapshot, key, now, parent)[0]

    @staticmethod
    def run(c, run_id):
        row = c.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
        if row is None:
            raise ValueError("unknown run")
        return dict(row)

    def inspect(self, run_id):
        with self.transaction() as c:
            run = self.run(c, run_id)
            events = [
                dict(row)
                for row in c.execute("SELECT * FROM events WHERE run_id=? ORDER BY seq", (run_id,))
            ]
            artifacts = {e["artifact"]: self.get(c, e["artifact"]) for e in events}
            for field in ("snapshot", "context", "latest_brief", "latest_check", "outcome"):
                if run[field]:
                    artifacts[run[field]] = self.get(c, run[field])
            return {"run": run, "events": events, "artifacts": artifacts}

    @classmethod
    def _verify(cls, c):
        previous_seal = ""
        state_hash = None
        for expected_seq, seal in enumerate(c.execute("SELECT * FROM seals ORDER BY seq"), 1):
            fields = [seal["seq"], seal["previous"], seal["state_hash"]]
            if (
                seal["seq"] != expected_seq
                or seal["previous"] != previous_seal
                or seal["hash"] != digest(fields)
            ):
                raise IntegrityError("transaction seal mismatch")
            previous_seal, state_hash = seal["hash"], seal["state_hash"]
        if state_hash is not None and state_hash != cls.projection(c):
            raise IntegrityError("materialized state differs from committed seal")
        if state_hash is None and c.execute("SELECT 1 FROM runs LIMIT 1").fetchone():
            raise IntegrityError("unsealed run state")
        previous = ""
        expected = 1
        for row in c.execute("SELECT * FROM events ORDER BY seq"):
            fields = [
                row[k] for k in ("seq", "run_id", "kind", "parent", "artifact", "at", "previous")
            ]
            if row["seq"] != expected or row["previous"] != previous:
                raise IntegrityError("event sequence mismatch")
            if digest(fields) != row["hash"]:
                raise IntegrityError("event hash mismatch")
            cls.get(c, row["artifact"])
            previous = row["hash"]
            expected += 1
        for row in c.execute("SELECT hash FROM artifacts"):
            cls.get(c, row[0])
        for row in c.execute("SELECT snapshot,fingerprint FROM runs"):
            snap = Snapshot.model_validate(cls.get(c, row[0]))
            if digest(snap) != row[1]:
                raise IntegrityError("run snapshot mismatch")
        if c.execute("PRAGMA foreign_key_check").fetchone():
            raise IntegrityError("broken ledger reference")

    def verify(self):
        with self.transaction():
            pass

    @staticmethod
    def projection(c):
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        state = {}
        for table in ("runs", "jobs", "schedules"):
            # Names come only from this fixed allowlist, never external input.
            state[table] = (
                [dict(r) for r in c.execute(f"SELECT * FROM {table} ORDER BY id")]
                if table in tables
                else []
            )
        state["artifacts"] = [r[0] for r in c.execute("SELECT hash FROM artifacts ORDER BY hash")]
        head = c.execute("SELECT seq,hash FROM events ORDER BY seq DESC LIMIT 1").fetchone()
        state["event_head"] = list(head) if head else None
        return digest(state)

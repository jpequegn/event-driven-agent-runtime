"""Durable intervals. No scheduling occurs without an explicit tick invocation."""

import re

from event_runtime.contracts import Snapshot
from event_runtime.store import Conflict, identity


class Schedules:
    def __init__(self, queue):
        self.queue, self.store = queue, queue.store
        self.store.db.execute("""CREATE TABLE IF NOT EXISTS schedules(
            id TEXT PRIMARY KEY, name TEXT NOT NULL UNIQUE,
            snapshot TEXT NOT NULL REFERENCES artifacts(hash), every_seconds INTEGER NOT NULL,
            starts INTEGER NOT NULL, next_due INTEGER NOT NULL, enabled INTEGER NOT NULL)""")

    def add(self, name, snapshot, every_seconds, starts):
        if not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", name):
            raise ValueError("invalid schedule name")
        if not 60 <= every_seconds <= 86400 or starts < 0:
            raise ValueError("interval must be 60..86400 seconds and start nonnegative")
        with self.store.transaction() as c:
            snap = self.store.put(c, snapshot)
            old = c.execute("SELECT * FROM schedules WHERE name=?", (name,)).fetchone()
            if old:
                if (old["snapshot"], old["every_seconds"], old["starts"]) != (
                    snap,
                    every_seconds,
                    starts,
                ):
                    raise Conflict("schedule name reused with different settings")
                return old["id"]
            if c.execute("SELECT COUNT(*) FROM schedules").fetchone()[0] >= 20:
                raise ValueError("schedule capacity reached")
            sid = identity()
            c.execute(
                "INSERT INTO schedules VALUES(?,?,?,?,?,?,1)",
                (sid, name, snap, every_seconds, starts, starts),
            )
            return sid

    def set_enabled(self, name, enabled):
        with self.store.transaction() as c:
            if not c.execute(
                "UPDATE schedules SET enabled=? WHERE name=?", (int(enabled), name)
            ).rowcount:
                raise ValueError("unknown schedule")

    def list(self):
        with self.store.transaction() as c:
            return [dict(r) for r in c.execute("SELECT * FROM schedules ORDER BY name")]

    def tick(self, now, limit=10):
        if now < 0 or not 1 <= limit <= 50:
            raise ValueError("invalid tick time or catch-up limit")
        runs = []
        with self.store.transaction() as c:
            for _ in range(limit):
                schedule = c.execute(
                    "SELECT * FROM schedules WHERE enabled=1 AND next_due<=? "
                    "ORDER BY next_due,name LIMIT 1",
                    (now,),
                ).fetchone()
                if schedule is None:
                    break
                snapshot = Snapshot.model_validate(self.store.get(c, schedule["snapshot"]))
                due = schedule["next_due"]
                run, fresh = self.store.new_run(
                    c, snapshot, f"schedule:{schedule['id']}:{due}", due
                )
                if fresh:
                    event = self.store.record(
                        c,
                        run,
                        "schedule.triggered",
                        {"schedule": schedule["id"], "due": due, "tick_at": now},
                        now,
                    )
                    self.queue.emit(
                        c, run, "brief.requested", snapshot.trigger.model_dump(), now, event
                    )
                c.execute(
                    "UPDATE schedules SET next_due=? WHERE id=?",
                    (due + schedule["every_seconds"], schedule["id"]),
                )
                runs.append(run)
        return runs

"""Fenced jobs for pure handlers. External side effects are deliberately unsupported."""

from event_runtime.contracts import Agent, Brief, Snapshot, Trigger, Verification
from event_runtime.store import Conflict, Store, identity

PAYLOADS = {"brief.requested": Trigger, "brief.drafted": Brief, "brief.verified": Verification}


class Queue:
    def __init__(self, store: Store):
        self.store = store
        store.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs(
                id TEXT PRIMARY KEY, run_id TEXT NOT NULL REFERENCES runs(id),
                event_seq INTEGER NOT NULL REFERENCES events(seq),
                agent TEXT NOT NULL, handler TEXT NOT NULL,
                input TEXT NOT NULL REFERENCES artifacts(hash),
                state TEXT NOT NULL, attempt INTEGER NOT NULL DEFAULT 0,
                max_attempts INTEGER NOT NULL, due INTEGER NOT NULL,
                lease TEXT, lease_until INTEGER, result TEXT REFERENCES artifacts(hash),
                UNIQUE(run_id,event_seq,agent));
            CREATE INDEX IF NOT EXISTS jobs_due ON jobs(state,due);
        """)

    def trigger(self, snapshot, key, now, parent=None):
        with self.store.transaction() as c:
            run, fresh = self.store.new_run(c, snapshot, key, now, parent)
            if fresh:
                self.emit(c, run, "brief.requested", snapshot.trigger.model_dump(), now)
            return run

    def emit(self, c, run_id, kind, payload, now, parent=None):
        value = PAYLOADS[kind].model_validate(payload)
        event = self.store.record(c, run_id, kind, value, now, parent)
        run = self.store.run(c, run_id)
        snapshot = Snapshot.model_validate(self.store.get(c, run["snapshot"]))
        for agent in snapshot.agents:
            if agent.accepts != kind:
                continue
            count = c.execute("SELECT COUNT(*) FROM jobs WHERE run_id=?", (run_id,)).fetchone()[0]
            if count >= 50:
                raise ValueError("per-run job capacity reached")
            c.execute(
                """INSERT INTO jobs
                (id,run_id,event_seq,agent,handler,input,state,max_attempts,due)
                VALUES(?,?,?,?,?,?,'pending',?,?)""",
                (
                    identity(),
                    run_id,
                    event,
                    agent.name,
                    agent.handler,
                    self.store.put(c, value),
                    agent.max_attempts,
                    now,
                ),
            )
        return event

    def claim(self, now: int, run_id=None, lease_seconds=30):
        if type(now) is not int or now < 0 or not 1 <= lease_seconds <= 300:
            raise ValueError("invalid clock or lease")
        with self.store.transaction() as c:
            rows = c.execute(
                """SELECT j.* FROM jobs j JOIN runs r ON r.id=j.run_id
                WHERE r.status IN ('queued','running')
                AND (? IS NULL OR r.id=?)
                AND ((j.state='pending' AND j.due<=?) OR
                     (j.state='leased' AND j.lease_until<=?))
                ORDER BY j.due,j.event_seq,j.agent""",
                (run_id, run_id, now, now),
            ).fetchall()
            for row in rows:
                job = dict(row)
                if job["attempt"] >= job["max_attempts"]:
                    self.dead(c, job, "lease attempts exhausted", now)
                    continue
                lease = identity()
                c.execute(
                    """UPDATE jobs SET state='leased',attempt=attempt+1,
                    lease=?,lease_until=? WHERE id=?""",
                    (lease, now + lease_seconds, job["id"]),
                )
                c.execute("UPDATE runs SET status='running' WHERE id=?", (job["run_id"],))
                self.store.record(
                    c,
                    job["run_id"],
                    "checkpoint",
                    {
                        "job_id": job["id"],
                        "agent": job["agent"],
                        "input": job["input"],
                        "attempt": job["attempt"] + 1,
                        "recovered": job["state"] == "leased",
                        "effects_allowed": False,
                    },
                    now,
                    job["event_seq"],
                )
                return dict(c.execute("SELECT * FROM jobs WHERE id=?", (job["id"],)).fetchone())
            return None

    @staticmethod
    def owned(c, job_id, lease, now):
        row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        if row is None or row["state"] != "leased" or row["lease"] != lease:
            raise Conflict("worker lease no longer owned")
        if now >= row["lease_until"]:
            raise Conflict("worker lease expired")
        return dict(row)

    def dead(self, c, job, reason, now):
        c.execute("UPDATE jobs SET state='dead',lease=NULL WHERE id=?", (job["id"],))
        c.execute(
            "UPDATE jobs SET state='cancelled',lease=NULL WHERE run_id=? "
            "AND state IN ('pending','leased')",
            (job["run_id"],),
        )
        c.execute(
            "UPDATE runs SET status='dead_letter',revision=revision+1 WHERE id=?", (job["run_id"],)
        )
        self.store.record(
            c,
            job["run_id"],
            "dead_letter",
            {"job_id": job["id"], "reason": reason},
            now,
            job["event_seq"],
        )

    def fail(self, job_id, lease, now, reason, retry=True):
        reason = str(reason)[:500]
        with self.store.transaction() as c:
            job = self.owned(c, job_id, lease, now)
            if not retry or job["attempt"] >= job["max_attempts"]:
                self.dead(c, job, reason, now)
            else:
                due = now + 2 ** job["attempt"]
                c.execute(
                    "UPDATE jobs SET state='pending',lease=NULL,due=? WHERE id=?", (due, job_id)
                )
                self.store.record(
                    c,
                    job["run_id"],
                    "recovery",
                    {
                        "job_id": job_id,
                        "action": "retry",
                        "due": due,
                        "reason": reason,
                    },
                    now,
                    job["event_seq"],
                )

    def complete(self, job_id, lease, now, payload):
        with self.store.transaction() as c:
            job = self.owned(c, job_id, lease, now)
            run = self.store.run(c, job["run_id"])
            if run["status"] != "running":
                raise Conflict("run is not running")
            snapshot = Snapshot.model_validate(self.store.get(c, run["snapshot"]))
            agent: Agent = next(a for a in snapshot.agents if a.name == job["agent"])
            value = PAYLOADS[agent.emits].model_validate(payload)
            artifact = self.store.put(c, value)
            c.execute(
                "UPDATE jobs SET state='done',result=?,lease=NULL WHERE id=?", (artifact, job_id)
            )
            if job["handler"] == "draft":
                c.execute(
                    "UPDATE runs SET latest_brief=?,latest_check=NULL,revision=revision+1 "
                    "WHERE id=?",
                    (artifact, run["id"]),
                )
            else:
                state = "context_gap" if value.missing_terms else "review"
                c.execute(
                    "UPDATE runs SET latest_check=?,status=?,revision=revision+1 WHERE id=?",
                    (artifact, state, run["id"]),
                )
            self.emit(c, run["id"], agent.emits, value.model_dump(), now, job["event_seq"])

    def control(self, run_id, action, now):
        with self.store.transaction() as c:
            run = self.store.run(c, run_id)
            state = run["status"]
            if action == "pause" and state in ("queued", "running", "review", "context_gap"):
                c.execute(
                    "UPDATE runs SET paused_from=status,status='paused',revision=revision+1 "
                    "WHERE id=?",
                    (run_id,),
                )
                c.execute(
                    "UPDATE jobs SET state='pending',lease=NULL,due=? "
                    "WHERE run_id=? AND state='leased'",
                    (now, run_id),
                )
            elif action == "resume" and state == "paused":
                c.execute(
                    "UPDATE runs SET status=paused_from,paused_from=NULL,revision=revision+1 "
                    "WHERE id=?",
                    (run_id,),
                )
            elif action == "cancel" and state not in ("completed", "rejected", "cancelled"):
                c.execute(
                    "UPDATE runs SET status='cancelled',revision=revision+1 WHERE id=?", (run_id,)
                )
                c.execute(
                    "UPDATE jobs SET state='cancelled',lease=NULL WHERE run_id=? "
                    "AND state IN ('pending','leased')",
                    (run_id,),
                )
            else:
                raise Conflict("control is invalid for current run state")
            self.store.record(c, run_id, "run.control", {"action": action}, now)

    def jobs(self, run_id):
        with self.store.transaction() as c:
            self.store.run(c, run_id)
            return [
                dict(r)
                for r in c.execute(
                    "SELECT * FROM jobs WHERE run_id=? ORDER BY event_seq,agent", (run_id,)
                )
            ]

"""Closed synthetic sign-off adapter, not a general external-effect handler.

The existing queue drafts a cited risk calculation and its reviewer accepts the
exact revision. Publication intent/observation reuse sealed Store events. The
separate inert sink is the declared idempotency boundary, not the queue's job.
"""

import argparse
import json
import sqlite3
import subprocess
import sys
import time
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path

from event_runtime.contracts import Agent, Context, Snapshot, Source, Trigger, digest
from event_runtime.queue import Queue
from event_runtime.review import Review, Reviewer
from event_runtime.store import Conflict, Store
from event_runtime.worker import Worker

SCENARIOS = (
    "healthy",
    "worker-retry",
    "crash-before",
    "crash-after",
    "lost-ack",
    "duplicate",
    "stale-approval",
    "failed-effect",
    "missing-outcome",
    "out-of-order",
    "review-restart",
    "healthy-held",
)
IMPLEMENTATIONS = ("baseline", "recovery")


def snapshot():
    gross = sum(abs(q) * p for q, p in ((-2, 10), (1, 5)))
    return Snapshot(
        trigger=Trigger(
            goal="Draft the synthetic risk report for local sign-off only.",
            window_start="2026-01-01",
            window_end="2026-01-01",
            sources=[
                Source(
                    id="calculation",
                    title="Read-only synthetic calculation",
                    published="2026-01-01",
                    facts=[f"gross_cents={gross}; breached=false"],
                )
            ],
        ),
        context=Context(),
        agents=[
            Agent(
                name="draft",
                handler="draft",
                accepts="brief.requested",
                emits="brief.drafted",
                prompt="Draft only the supplied calculation.",
            ),
            Agent(
                name="check",
                handler="verify",
                accepts="brief.drafted",
                emits="brief.verified",
                prompt="Check the cited calculation.",
            ),
        ],
    )


def intent(store, run_id, now, *, unsafe=False):
    with store.transaction() as c:
        run = store.run(c, run_id)
        reviews = c.execute(
            "SELECT artifact FROM events WHERE run_id=? AND kind='review.disposition' "
            "ORDER BY seq DESC",
            (run_id,),
        ).fetchall()
        approval = store.get(c, reviews[0][0]) if reviews else None
        if not unsafe and (
            run["status"] != "completed"
            or not approval
            or approval["decision"] != "accept"
            or approval["brief"] != run["latest_brief"]
            or approval["revision"] + 1 != run["revision"]
        ):
            raise Conflict("current revision-bound acceptance required")
        text = store.get(c, run["latest_brief"])["claims"][0]["text"]
        value = {
            "task_version": "risk-signoff/1",
            "run_id": run_id,
            "brief_digest": run["latest_brief"],
            "approved_revision": approval["revision"] if approval else None,
            "payload": {"gross_cents": int(text.split("=")[1].split(";")[0]), "breached": False},
        }
        value["action_id"] = digest(value)
        previous = c.execute(
            "SELECT artifact FROM events WHERE run_id=? AND kind='signoff.intent'",
            (run_id,),
        ).fetchall()
        if previous:
            old = store.get(c, previous[0][0])
            if old != value:
                raise Conflict("intent changed after preparation")
            return old
        store.record(c, run_id, "signoff.intent", value, now)
        return value


def sink(path, value, key, *, fail=False):
    """Count committed attempts separately from effects; key uniqueness is local."""
    with closing(sqlite3.connect(path)) as c, c:
        c.execute("CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY, action_id TEXT)")
        c.execute(
            "CREATE TABLE IF NOT EXISTS results(action_id TEXT PRIMARY KEY, body TEXT, status TEXT)"
        )
        c.execute("INSERT INTO attempts(action_id) VALUES(?)", (key,))
        body = json.dumps(value, sort_keys=True)
        old = c.execute("SELECT body FROM results WHERE action_id=?", (key,)).fetchone()
        if old is not None and old[0] != body:
            raise Conflict("effect key reused with changed payload")
        c.execute(
            "INSERT OR IGNORE INTO results VALUES(?,?,?)",
            (key, body, "confirmed_failure" if fail else "confirmed_success"),
        )


def observe(path, *, available=True):
    if not available:
        return {"rows": None, "attempts": None}
    if not path.exists():
        return {"rows": [], "attempts": 0}
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as c:
        rows = [
            {"action_id": key, "body": json.loads(body), "status": status}
            for key, body, status in c.execute("SELECT * FROM results ORDER BY action_id")
        ]
        return {"rows": rows, "attempts": c.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]}


def note(store, run_id, kind, value, now):
    with store.transaction() as c:
        store.record(c, run_id, kind, value, now)


def crash(directory, run_id, point):
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "event_runtime.signoff",
            "--child",
            point,
            "--directory",
            str(directory),
            "--run-id",
            run_id,
        ],
        capture_output=True,
        timeout=15,
        check=False,
    )
    if done.returncode != 23:
        raise RuntimeError("synthetic crash child did not exit at the boundary")


def run_case(directory, scenario, implementation, trial):
    if scenario not in SCENARIOS or implementation not in IMPLEMENTATIONS or trial not in (0, 1):
        raise ValueError("unsupported synthetic case or configuration")
    started_at = datetime.now(UTC)
    started = time.perf_counter()
    directory.mkdir(parents=True, exist_ok=False)
    database, external = directory / "runtime.sqlite", directory / "inert.sqlite"
    with Store(database) as store:
        queue = Queue(store)
        run_id = queue.trigger(snapshot(), "risk-signoff", 0)
    original_run_id = run_id
    if scenario == "worker-retry":
        crash(directory, run_id, "worker")
    with Store(database) as store:
        queue = Queue(store)
        Worker(queue).work(31 if scenario == "worker-retry" else 1, run_id)
        before = store.inspect(run_id)["run"]["latest_brief"]
    # A fresh worker restores the pending review, then exits without publishing.
    if scenario == "review-restart":
        crash(directory, run_id, "review")
        checkpoint = json.loads((directory / "review-checkpoint.json").read_text())
        with Store(database) as store:
            queue = Queue(store)
            key = "risk-signoff" if implementation == "recovery" else "lost-local-identity"
            run_id = queue.trigger(snapshot(), key, 32)
            if implementation == "recovery":
                if run_id != checkpoint["run_id"] or before != checkpoint["brief"]:
                    raise Conflict("pending review was not restored")
            else:
                Worker(queue).work(32, run_id)
    with Store(database) as store:
        queue = Queue(store)
        run = store.inspect(run_id)["run"]
        stale = scenario == "stale-approval"
        try:
            Reviewer(queue).decide(
                run_id,
                Review(
                    revision=run["revision"] - int(stale),
                    reviewer="simulated-operator",
                    decision="accept",
                    note="Synthetic fixture approval, never an owner label.",
                ),
                33,
            )
        except Conflict:
            if not stale:
                raise
        if not stale or implementation == "baseline":
            prepared = intent(store, run_id, 34, unsafe=stale)
        else:
            prepared = None
    recovery_started = time.perf_counter()
    crashed = scenario in ("crash-before", "crash-after")
    if crashed:
        crash(directory, run_id, "before" if scenario == "crash-before" else "after")
    publication = "not_attempted"
    if prepared is not None:
        # A confirmation before target observation is not independent outcome evidence.
        early_confirmation = scenario == "out-of-order"
        if early_confirmation:
            with Store(database) as store:
                note(
                    store,
                    run_id,
                    "signoff.premature-confirmation",
                    {
                        "action_id": prepared["action_id"],
                        "before_effect": True,
                        "accepted": implementation == "baseline",
                    },
                    34,
                )
        if early_confirmation and implementation == "baseline":
            publication = "confirmed_success"
        else:
            prior = observe(external)
            if not (implementation == "recovery" and prior["rows"]):
                key = prepared["action_id"]
                if implementation == "baseline" and prior["rows"]:
                    key += ":retry"
                sink(external, prepared, key, fail=scenario == "failed-effect")
            if scenario == "lost-ack":
                # Discard the acknowledgement after the durable target commit.
                with Store(database) as store:
                    note(
                        store,
                        run_id,
                        "signoff.acknowledgement-lost",
                        {
                            "action_id": prepared["action_id"],
                            "outcome": "unknown",
                        },
                        34,
                    )
            repeat = scenario in ("duplicate", "lost-ack")
            if repeat:
                key = (
                    prepared["action_id"]
                    if implementation == "recovery"
                    else prepared["action_id"] + ":retry"
                )
                # An actual repeated activity with the same key is not a duplicate effect.
                sink(external, prepared, key)
            observed = observe(external, available=scenario != "missing-outcome")
            publication = "unknown" if observed["rows"] is None else observed["rows"][0]["status"]
            if scenario == "missing-outcome" and implementation == "baseline":
                publication = "confirmed_success"
    with Store(database) as store:
        note(store, run_id, "signoff.observed", {"state": publication}, 35)
        evidence = store.inspect(run_id)
        approval_events = [e for e in evidence["events"] if e["kind"] == "review.disposition"]
        approval = (
            evidence["artifacts"][approval_events[-1]["artifact"]] if approval_events else None
        )
        draft = evidence["artifacts"][evidence["run"]["latest_brief"]]
        jobs = Queue(store).jobs(run_id)
        store.verify()
    return {
        "schema_version": "inert-signoff-receipt/v1",
        "provenance": "synthetic",
        "adoption_authorized": False,
        "scenario": scenario,
        "implementation": implementation,
        "trial": trial,
        "task_version": "risk-signoff/1",
        "run_id": run_id,
        "original_run_id": original_run_id,
        "before_brief": before,
        "after_brief": evidence["run"]["latest_brief"],
        "draft": draft,
        "approval": approval,
        "intent": prepared,
        "publication": publication,
        "external": observe(external, available=scenario != "missing-outcome"),
        "job_attempts": sum(j["attempt"] for j in jobs),
        "crash_exit_codes": [23]
        if crashed or scenario in ("worker-retry", "review-restart")
        else [],
        "recovery_ticks": 31
        if scenario in ("worker-retry", "review-restart")
        else 1
        if crashed
        else 0,
        "known_usage": None,
        "started_at": started_at.isoformat(),
        "ended_at": datetime.now(UTC).isoformat(),
        "duration_seconds": time.perf_counter() - started,
        "recovery_seconds": time.perf_counter() - recovery_started
        if scenario not in ("healthy", "healthy-held")
        else None,
    }


def child(directory, run_id, point):
    import os

    with Store(directory / "runtime.sqlite") as store:
        if point == "worker":
            Queue(store).claim(0, run_id)
        elif point == "review":
            restored = Queue(store).trigger(snapshot(), "risk-signoff", 32)
            pending = store.inspect(restored)["run"]
            if (
                restored != run_id
                or pending["status"] != "review"
                or observe(directory / "inert.sqlite")["rows"]
            ):
                raise Conflict("review restart did not preserve its boundary")
            (directory / "review-checkpoint.json").write_text(
                json.dumps(
                    {
                        "run_id": restored,
                        "brief": pending["latest_brief"],
                        "status": "review",
                    }
                )
            )
        else:
            prepared = intent(store, run_id, 34)
            if point == "after":
                sink(directory / "inert.sqlite", prepared, prepared["action_id"])
    os._exit(23)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", required=True, type=Path)
    parser.add_argument("--child", choices=("worker", "before", "after", "review"))
    parser.add_argument("--run-id")
    args = parser.parse_args()
    if args.child:
        child(args.directory, args.run_id, args.child)
    args.directory.mkdir(parents=True, exist_ok=False)
    receipts = [
        run_case(args.directory / f"{s}-{i}-{t}", s, i, t)
        for s in SCENARIOS
        for i in IMPLEMENTATIONS
        for t in (0, 1)
    ]
    batch = {"schema_version": "inert-signoff-batch/v1", "receipts": receipts}
    path = args.directory / "receipts.json"
    path.write_text(json.dumps(batch, indent=2) + "\n")
    print(json.dumps({"receipts": str(path), "count": len(receipts)}))


if __name__ == "__main__":
    main()

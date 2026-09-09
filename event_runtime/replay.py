"""Fork-and-rerun replay for pure providers. No review decision is carried forward."""

from collections import Counter
from difflib import unified_diff

from event_runtime.contracts import Context, Snapshot, canonical, digest
from event_runtime.queue import Queue
from event_runtime.store import identity
from event_runtime.worker import Worker


def drive(queue: Queue, run_id, start=0):
    """Bounded virtual-clock driver for demos/evals, not a background scheduler."""
    now = start
    for _ in range(20):
        Worker(queue).work(now, run_id)
        report = queue.store.inspect(run_id)
        if report["run"]["status"] not in ("queued", "running"):
            return report
        jobs = [j for j in queue.jobs(run_id) if j["state"] in ("pending", "leased")]
        if not jobs:
            raise ValueError("active run has no recoverable work")
        now = max(
            now + 1, min(j["due"] if j["state"] == "pending" else j["lease_until"] for j in jobs)
        )
    raise ValueError("virtual execution step limit exceeded")


def replay(queue, original, profile, now, use_latest_context=False, key=None, capabilities=None):
    report = queue.store.inspect(original)
    run = report["run"]
    snapshot = Snapshot.model_validate(report["artifacts"][run["snapshot"]])
    data = snapshot.model_dump()
    data["profile"] = profile
    if use_latest_context:
        data["context"] = Context.model_validate(report["artifacts"][run["context"]]).model_dump()
    if capabilities is not None:
        data["capabilities"] = capabilities
    new = queue.trigger(
        Snapshot.model_validate(data), key or f"replay:{identity()}", now, parent=original
    )
    return new


def metrics(report):
    run = report["run"]
    artifacts = report["artifacts"]
    check = artifacts.get(run["latest_check"])
    kinds = Counter(e["kind"] for e in report["events"])
    attempts = [
        artifacts[e["artifact"]] for e in report["events"] if e["kind"] == "handler.attempt"
    ]
    return {
        "status": run["status"],
        "automated_check_passed": bool(check and check["passed"]),
        "accepted": run["status"] == "completed",
        "handler_attempts": len(attempts),
        "retries": kinds["recovery"],
        "review_decisions": kinds["review.disposition"],
        "context_dispositions": kinds["context.disposition"],
        "feedback_cases": kinds["feedback.case"],
        "synthetic_cost_units": sum(a["synthetic_cost_units"] for a in attempts),
        "reasons": check["reasons"] if check else [],
        "external_effects": 0,
    }


def behavior(report):
    run = report["run"]
    return {
        k: report["artifacts"].get(run[k])
        for k in ("snapshot", "context", "latest_brief", "latest_check")
    }


def compare(left, right):
    a, b = behavior(left), behavior(right)
    return {
        "left_run": left["run"]["id"],
        "right_run": right["run"]["id"],
        "same_behavior": digest(a) == digest(b),
        "left_metrics": metrics(left),
        "right_metrics": metrics(right),
        "artifact_changes": {
            k: {"before": digest(a[k]), "after": digest(b[k])} for k in a if a[k] != b[k]
        },
        "diff": "\n".join(
            unified_diff(
                canonical(a).splitlines(),
                canonical(b).splitlines(),
                fromfile="original",
                tofile="replay",
                lineterm="",
            )
        ),
    }


def feedback_cases(report):
    return [
        report["artifacts"][e["artifact"]] for e in report["events"] if e["kind"] == "feedback.case"
    ]

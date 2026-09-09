import json
import sqlite3
import time
from contextlib import contextmanager
from functools import wraps
from pathlib import Path

import typer

from event_runtime.contracts import Context, Snapshot, Trigger, load_agent, read_json
from event_runtime.evaluation import evaluate
from event_runtime.feedback import evaluate_feedback
from event_runtime.output import write_report
from event_runtime.queue import Queue
from event_runtime.replay import compare, drive, feedback_cases, metrics, replay
from event_runtime.review import Resolution, Review, Reviewer
from event_runtime.schedule import Schedules
from event_runtime.store import Store
from event_runtime.worker import Worker

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False)


def guarded(fn):
    @wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except (ValueError, OSError, sqlite3.Error) as exc:
            typer.echo(f"Error: {exc}", err=True)
            raise typer.Exit(2) from exc

    return wrapped


def emit(value):
    typer.echo(json.dumps(value, indent=2, sort_keys=True))


def clock():
    return int(time.time())


def snapshot_files(
    trigger=Path("examples/trigger.json"),
    context=Path("examples/context.json"),
    agents=Path("examples/agents"),
    profile="economy",
):
    return Snapshot.model_validate(
        {
            "trigger": Trigger.model_validate(read_json(trigger)).model_dump(),
            "context": Context.model_validate(read_json(context)).model_dump() if context else {},
            "agents": [load_agent(p).model_dump() for p in sorted(agents.glob("*.md"))],
            "profile": profile,
        }
    )


@contextmanager
def ledger(ctx, create=False):
    path = ctx.obj
    if not create and not path.is_file():
        raise ValueError("database does not exist; use trigger or demo first")
    with Store(path) as store:
        yield Queue(store)


@app.callback()
def main(ctx: typer.Context, db: Path = typer.Option(Path("data/runtime.db"), "--db")):
    """Local, manually driven agent workflows. No daemon, network tools, or publishing."""
    ctx.obj = db


@app.command()
def version():
    """Print the runtime version."""
    typer.echo("0.1.0")


@app.command()
@guarded
def trigger(
    ctx: typer.Context,
    key: str = typer.Option(...),
    source: Path = typer.Option(Path("examples/trigger.json")),
    context: Path = typer.Option(Path("examples/context.json")),
    agents: Path = typer.Option(Path("examples/agents")),
    profile: str = "economy",
):
    """Snapshot configuration and enqueue a brief; do not execute it yet."""
    snapshot = snapshot_files(source, context, agents, profile)
    with ledger(ctx, create=True) as q:
        emit({"run_id": q.trigger(snapshot, key, clock())})


@app.command()
@guarded
def work(ctx: typer.Context, run_id: str | None = None, max_steps: int = 20):
    """Execute a bounded batch of due jobs, then exit. No waiting loop."""
    with ledger(ctx) as q:
        emit(Worker(q).work(clock(), run_id, max_steps))


@app.command()
@guarded
def inspect(ctx: typer.Context, run_id: str):
    """Print run state, causal events, artifacts, jobs, and outcome metrics."""
    with ledger(ctx) as q:
        report = q.store.inspect(run_id)
        emit({**report, "jobs": q.jobs(run_id), "metrics": metrics(report)})


@app.command()
@guarded
def runs(ctx: typer.Context):
    """List saved runs."""
    with ledger(ctx) as q, q.store.transaction() as c:
        emit([dict(r) for r in c.execute("SELECT * FROM runs ORDER BY created_at,id")])


@app.command()
@guarded
def artifact(ctx: typer.Context, artifact_hash: str):
    """Read and verify one saved artifact, including a prompt or feedback case."""
    with ledger(ctx) as q, q.store.transaction() as c:
        emit(q.store.get(c, artifact_hash))


@app.command("verify")
@guarded
def verify_ledger(ctx: typer.Context):
    """Check artifact hashes, event history, snapshots and foreign keys."""
    with ledger(ctx) as q:
        q.store.verify()
        emit({"verified": True})


@app.command()
@guarded
def control(ctx: typer.Context, run_id: str, action: str):
    """Pause, resume, or cancel a run, fencing any active worker."""
    with ledger(ctx) as q:
        q.control(run_id, action, clock())
        emit(q.store.inspect(run_id)["run"])


@app.command()
@guarded
def review(
    ctx: typer.Context,
    run_id: str,
    decision: str,
    revision: int = typer.Option(...),
    reviewer: str = typer.Option(...),
    note: str = typer.Option(...),
    correction: Path | None = None,
):
    """Accept, reject, or submit a corrected Brief JSON for independent rechecking."""
    request = Review.model_validate(
        {
            "revision": revision,
            "reviewer": reviewer,
            "decision": decision,
            "note": note,
            "correction": read_json(correction) if correction else None,
        }
    )
    with ledger(ctx) as q:
        Reviewer(q).decide(run_id, request, clock())
        emit(q.store.inspect(run_id)["run"])


@app.command("resolve-context")
@guarded
def resolve_context(
    ctx: typer.Context,
    run_id: str,
    term: str,
    disposition: str,
    revision: int = typer.Option(...),
    reviewer: str = typer.Option(...),
    value: str | None = None,
    source_id: str | None = None,
):
    """Record a human context-gap disposition; saved changes enqueue a fresh check."""
    request = Resolution.model_validate(
        {
            "revision": revision,
            "reviewer": reviewer,
            "term": term,
            "disposition": disposition,
            "value": value,
            "source_id": source_id,
        }
    )
    with ledger(ctx) as q:
        Reviewer(q).resolve(run_id, request, clock())
        emit(q.store.inspect(run_id)["run"])


@app.command("replay")
@guarded
def replay_run(
    ctx: typer.Context,
    run_id: str,
    profile: str = "careful",
    latest_context: bool = False,
    key: str | None = None,
):
    """Enqueue an isolated rerun from saved inputs, without inherited approvals."""
    with ledger(ctx) as q:
        emit({"run_id": replay(q, run_id, profile, clock(), latest_context, key)})


@app.command()
@guarded
def diff(ctx: typer.Context, left: str, right: str):
    """Compare saved inputs, outputs, verification and metrics from two runs."""
    with ledger(ctx) as q:
        emit(compare(q.store.inspect(left), q.store.inspect(right)))


@app.command()
@guarded
def feedback(ctx: typer.Context, run_id: str):
    """Export human correction cases for regression evaluation."""
    with ledger(ctx) as q:
        emit(feedback_cases(q.store.inspect(run_id)))


@app.command("feedback-eval")
@guarded
def feedback_eval(ctx: typer.Context, run_id: str, profile: str = "careful"):
    """Test a mock provider against verified human correction cases, without side effects."""
    with ledger(ctx) as q:
        emit(evaluate_feedback(q.store, run_id, profile))


@app.command("schedule-add")
@guarded
def schedule_add(
    ctx: typer.Context,
    name: str,
    every_seconds: int = 3600,
    source: Path = typer.Option(Path("examples/trigger.json")),
    context: Path = typer.Option(Path("examples/context.json")),
    agents: Path = typer.Option(Path("examples/agents")),
):
    """Save an interval schedule; only explicit tick calls can enqueue work."""
    with ledger(ctx, create=True) as q:
        emit(
            {
                "schedule_id": Schedules(q).add(
                    name, snapshot_files(source, context, agents), every_seconds, clock()
                )
            }
        )


@app.command()
@guarded
def schedules(ctx: typer.Context):
    """List saved schedules."""
    with ledger(ctx) as q:
        emit(Schedules(q).list())


@app.command("schedule-set")
@guarded
def schedule_set(
    ctx: typer.Context, name: str, enabled: bool = typer.Option(..., "--enabled/--disabled")
):
    """Enable or disable a saved interval schedule. Installs no system task."""
    with ledger(ctx) as q:
        Schedules(q).set_enabled(name, enabled)
        emit(Schedules(q).list())


@app.command()
@guarded
def tick(ctx: typer.Context, limit: int = 10):
    """Enqueue at most limit due schedule occurrences; do not execute jobs."""
    with ledger(ctx) as q:
        emit({"runs": Schedules(q).tick(clock(), limit)})


@app.command()
@guarded
def demo(out: Path = Path("data/demo"), accept_demo: bool = False):
    """Run the synthetic brief demo. Acceptance is opt-in and explicitly simulated."""
    out.mkdir(parents=True, exist_ok=False, mode=0o700)
    with Store(out / "runtime.db") as s:
        q = Queue(s)
        run = q.trigger(snapshot_files(), "demo", 0)
        report = drive(q, run)
        if accept_demo:
            Reviewer(q).decide(
                run,
                Review(
                    revision=report["run"]["revision"],
                    reviewer="simulated-reviewer",
                    decision="accept",
                    note="Explicit --accept-demo fixture approval",
                ),
                20,
            )
            report = s.inspect(run)
        write_report(out / "report.json", json.dumps(report, indent=2) + "\n")
        emit({"run_id": run, "database": str(out / "runtime.db"), **metrics(report)})


@app.command("evaluate")
@guarded
def evaluate_command(out: Path = Path("reports/evaluation")):
    """Run twelve deterministic cases with explicitly simulated review decisions."""
    result = evaluate(out, snapshot_files())
    emit({"report": str(out / "evaluation.md"), "totals": result["totals"]})

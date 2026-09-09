import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest

from event_runtime.contracts import Context, Source, load_agent, read_json
from event_runtime.feedback import evaluate_feedback
from event_runtime.queue import Queue
from event_runtime.replay import compare, drive, replay
from event_runtime.review import Resolution, Review, Reviewer
from event_runtime.schedule import Schedules
from event_runtime.store import Conflict, IntegrityError, Store
from event_runtime.worker import Worker


@pytest.mark.parametrize(
    "sql",
    [
        "UPDATE runs SET status='completed'",
        "UPDATE jobs SET state='done'",
        "UPDATE schedules SET next_due=99999",
    ],
)
def test_mutable_state_tamper_fails_closed(tmp_path, snapshot, sql):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        q.trigger(snapshot, "key", 0)
        Schedules(q).add("timer", snapshot, 60, 0)
        s.db.execute(sql)
        with pytest.raises(IntegrityError):
            Worker(q).work(0)


def test_clock_regression_rolls_back_claim(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot, "key", 10)
        job = q.claim(10)
        before = s.inspect(run)
        with pytest.raises(Conflict):
            q.fail(job["id"], job["lease"], 9, "past")
        assert before == s.inspect(run)


def test_duplicate_json_yaml_and_oversize(tmp_path):
    path = tmp_path / "input"
    path.write_text('{"goal":"one","goal":"two"}')
    with pytest.raises(ValueError, match="duplicate"):
        read_json(path)
    path.write_text("---\nname: one\nname: two\n---\nPrompt")
    with pytest.raises(ValueError, match="duplicate"):
        load_agent(path)
    path.write_text("---\nname: &name one\nhandler: *name\n---\nPrompt")
    with pytest.raises(ValueError, match="aliases"):
        load_agent(path)
    path.write_text("x" * 100001)
    with pytest.raises(ValueError, match="100 KB"):
        read_json(path)


def test_dates_are_unambiguous():
    with pytest.raises(ValueError):
        Source(id="source", title="Title", published="20260901", facts=["A fact"])


def test_concurrent_ticks_and_reviews(tmp_path, snapshot):
    path = tmp_path / "db"
    with Store(path) as s:
        Schedules(Queue(s)).add("timer", snapshot, 60, 0)

    def tick(_):
        with Store(path) as s:
            return Schedules(Queue(s)).tick(0)

    with ThreadPoolExecutor(max_workers=4) as pool:
        issued = [run for batch in pool.map(tick, range(4)) for run in batch]
    assert len(issued) == 1
    run = issued[0]
    with Store(path) as s:
        report = drive(Queue(s), run)
    request = Review(
        revision=report["run"]["revision"], reviewer="operator", decision="accept", note="Checked"
    )

    def review(_):
        with Store(path) as s:
            try:
                Reviewer(Queue(s)).decide(run, request, 1)
                return True
            except Conflict:
                return False

    with ThreadPoolExecutor(max_workers=4) as pool:
        assert sum(pool.map(review, range(4))) == 1


def test_approved_context_replay_and_self_review(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot.model_copy(update={"context": Context()}), "gap", 0)
        before = drive(q, run)
        req = Resolution(
            revision=2,
            reviewer="brief-drafter",
            term="slo",
            disposition="save-definition",
            value="Service-level objective",
        )
        with pytest.raises(Conflict):
            Reviewer(q).resolve(run, req, 1)
        Reviewer(q).resolve(run, req.model_copy(update={"reviewer": "operator"}), 1)
        Worker(q).work(1, run)
        replay_id = replay(q, run, "economy", 2, use_latest_context=True)
        after = drive(q, replay_id, 2)
        diff = compare(before, after)
        assert diff["right_metrics"]["automated_check_passed"]
        assert not diff["right_metrics"]["accepted"]
        assert {"context", "latest_check"} <= diff["artifact_changes"].keys()
        result = evaluate_feedback(s, run, "careful")
        assert result["passed"] == 1 and result["invalid_cases"] == 0


def test_actual_process_restart(tmp_path):
    path = tmp_path / "db"
    prefix = [sys.executable, "-m", "event_runtime", "--db", str(path)]

    def cli(*args):
        completed = subprocess.run(prefix + list(args), text=True, capture_output=True, timeout=20)
        assert completed.returncode == 0, completed.stderr
        return json.loads(completed.stdout)

    run = cli("trigger", "--key", "restart")["run_id"]
    cli("work", "--run-id", run, "--max-steps", "1")
    assert cli("inspect", run)["run"]["status"] == "running"
    cli("work", "--run-id", run)
    assert cli("inspect", run)["run"]["status"] == "review"
    assert cli("work", "--run-id", run) == []
    assert cli("verify")["verified"]

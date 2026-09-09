from event_runtime.contracts import Context
from event_runtime.queue import Queue
from event_runtime.store import Store
from event_runtime.worker import Worker


def test_resume_to_review(tmp_path, snapshot):
    path = tmp_path / "db"
    with Store(path) as s:
        q = Queue(s)
        run = q.trigger(snapshot, "key", 0)
        assert len(Worker(q).work(0, run, 1)) == 1
        assert s.inspect(run)["run"]["status"] == "running"
    with Store(path) as s:
        q = Queue(s)
        assert len(Worker(q).work(1, run)) == 1
        report = s.inspect(run)
        assert report["run"]["status"] == "review"
        assert report["artifacts"][report["run"]["latest_check"]]["passed"]
        assert report["run"]["outcome"] is None
        assert Worker(q).work(2, run) == []


def test_transient_retry(tmp_path, snapshot):
    snapshot = snapshot.model_copy(
        update={"trigger": snapshot.trigger.model_copy(update={"fault": "transient"})}
    )
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot, "retry", 0)
        assert Worker(q).step(0, run)["status"] == "retry"
        assert Worker(q).work(1, run) == []
        assert len(Worker(q).work(2, run)) == 2
        assert s.inspect(run)["run"]["status"] == "review"


def test_untrusted_claims_and_missing_context(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        bad = snapshot.model_copy(
            update={"trigger": snapshot.trigger.model_copy(update={"fault": "invalid"})}
        )
        run = q.trigger(bad, "bad", 0)
        Worker(q).work(0, run)
        report = s.inspect(run)
        assert not report["artifacts"][report["run"]["latest_check"]]["passed"]
        gap = q.trigger(snapshot.model_copy(update={"context": Context()}), "gap", 0)
        Worker(q).work(0, gap)
        assert s.inspect(gap)["run"]["status"] == "context_gap"


def test_capability_cutoff(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot.model_copy(update={"capabilities": ["verify"]}), "denied", 0)
        Worker(q).work(0, run)
        assert s.inspect(run)["run"]["status"] == "dead_letter"
        assert not s.inspect(run)["run"]["latest_brief"]

import pytest

from event_runtime.contracts import Brief, Claim, Context
from event_runtime.queue import Queue
from event_runtime.review import Resolution, Review, Reviewer
from event_runtime.store import Conflict, Store
from event_runtime.worker import Worker


def test_accept_is_revision_bound(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot, "key", 0)
        Worker(q).work(0, run)
        revision = s.inspect(run)["run"]["revision"]
        req = Review(revision=revision, reviewer="operator", decision="accept", note="Checked")
        with pytest.raises(Conflict):
            Reviewer(q).decide(run, req.model_copy(update={"revision": 0}), 1)
        Reviewer(q).decide(run, req, 1)
        report = s.inspect(run)
        assert report["run"]["status"] == "completed"
        assert report["artifacts"][report["run"]["outcome"]]["external_effects"] == []
        with pytest.raises(Conflict):
            Reviewer(q).decide(run, req, 2)


def test_correction_requires_reverification(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot, "key", 0)
        Worker(q).work(0, run)
        bad = Brief(title="Bad", claims=[Claim(text="Invented", source_id="episode-a")])
        review = Reviewer(q)
        review.decide(
            run,
            Review(
                revision=2,
                reviewer="operator",
                decision="correct",
                note="Test ungrounded correction",
                correction=bad,
            ),
            1,
        )
        assert s.inspect(run)["run"]["status"] == "queued"
        Worker(q).work(1, run)
        revision = s.inspect(run)["run"]["revision"]
        with pytest.raises(Conflict):
            review.decide(
                run,
                Review(
                    revision=revision,
                    reviewer="operator",
                    decision="accept",
                    note="Cannot bypass checks",
                ),
                2,
            )
        review.decide(
            run,
            Review(
                revision=revision, reviewer="operator", decision="reject", note="Unsupported claims"
            ),
            2,
        )
        assert s.inspect(run)["run"]["status"] == "rejected"


def test_context_gap_feedback(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot.model_copy(update={"context": Context()}), "gap", 0)
        Worker(q).work(0, run)
        Reviewer(q).resolve(
            run,
            Resolution(
                revision=2,
                reviewer="operator",
                term="slo",
                disposition="save-definition",
                value="Service-level objective",
            ),
            1,
        )
        Worker(q).work(1, run)
        report = s.inspect(run)
        assert report["run"]["status"] == "review"
        assert report["artifacts"][report["run"]["latest_check"]]["passed"]
        assert any(e["kind"] == "feedback.case" for e in report["events"])
        updated = report["artifacts"][report["run"]["context"]]
        assert updated["definitions"]["slo"] == "Service-level objective"
        assert "operator:operator" in updated["provenance"]["slo"]

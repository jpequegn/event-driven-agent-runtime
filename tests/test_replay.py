from event_runtime.evaluation import evaluate
from event_runtime.queue import Queue
from event_runtime.replay import compare, drive, replay
from event_runtime.review import Review, Reviewer
from event_runtime.store import Store


def test_replay_does_not_inherit_acceptance(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        original = q.trigger(snapshot, "original", 0)
        report = drive(q, original)
        Reviewer(q).decide(
            original,
            Review(
                revision=report["run"]["revision"],
                reviewer="operator",
                decision="accept",
                note="Verified",
            ),
            1,
        )
        before = s.inspect(original)
        same = replay(q, original, "economy", 2)
        same_report = drive(q, same, 2)
        assert compare(before, same_report)["same_behavior"]
        assert same_report["run"]["status"] == "review"
        assert same_report["run"]["outcome"] is None
        different = replay(q, original, "careful", 3)
        assert "latest_brief" in compare(before, drive(q, different, 3))["artifact_changes"]
        assert s.inspect(original) == before


def test_evaluation(tmp_path, snapshot):
    result = evaluate(tmp_path / "eval", snapshot)
    assert len(result["cases"]) == 12
    assert all(r["accepted"] == 2 for r in result["totals"])
    assert all(r["retries"] == 1 for r in result["totals"])
    assert result["external_effects"] == 0
    assert result["failure_clusters"]["context_gap"] == 2

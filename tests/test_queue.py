from concurrent.futures import ThreadPoolExecutor

import pytest

from event_runtime.queue import Queue
from event_runtime.store import Conflict, Store


def test_atomic_claim(tmp_path, snapshot):
    path = tmp_path / "db"
    with Store(path) as s:
        q = Queue(s)
        run = q.trigger(snapshot, "key", 0)
        assert run == q.trigger(snapshot, "key", 0)

    def claim(_):
        with Store(path) as s:
            return Queue(s).claim(0)

    with ThreadPoolExecutor(max_workers=4) as pool:
        claims = list(pool.map(claim, range(4)))
    assert len([c for c in claims if c]) == 1


def test_expiry_fencing_and_retry(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        q.trigger(snapshot, "key", 0)
        first = q.claim(0, lease_seconds=1)
        second = q.claim(1, lease_seconds=1)
        with pytest.raises(Conflict):
            q.fail(first["id"], first["lease"], 1, "late")
        q.fail(second["id"], second["lease"], 1, "temporary")
        assert q.claim(4) is None
        third = q.claim(5)
        q.fail(third["id"], third["lease"], 5, "exhausted")
        assert s.inspect(third["run_id"])["run"]["status"] == "dead_letter"


def test_pause_cancel_fence_workers(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        q = Queue(s)
        run = q.trigger(snapshot, "key", 0)
        job = q.claim(0)
        q.control(run, "pause", 1)
        assert q.claim(2) is None
        with pytest.raises(Conflict):
            q.fail(job["id"], job["lease"], 2, "late")
        q.control(run, "resume", 2)
        assert q.claim(2)
        q.control(run, "cancel", 3)
        assert q.claim(100) is None
        assert q.jobs(run)[0]["state"] == "cancelled"

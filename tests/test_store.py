import sqlite3

import pytest

from event_runtime.store import Conflict, IntegrityError, Store


def test_restart_and_idempotency(tmp_path, snapshot):
    path = tmp_path / "state.db"
    with Store(path) as s:
        run = s.create(snapshot, "key")
        assert run == s.create(snapshot, "key")
        with pytest.raises(Conflict):
            s.create(snapshot.model_copy(update={"profile": "careful"}), "key")
    with Store(path) as s:
        report = s.inspect(run)
        assert len(report["events"]) == 1
        assert report["artifacts"][report["run"]["snapshot"]]["profile"] == "economy"


def test_rollback_and_immutability(tmp_path, snapshot):
    with Store(tmp_path / "db") as s:
        run = s.create(snapshot, "key")
        with pytest.raises(RuntimeError), s.transaction() as c:
            s.record(c, run, "test", {"x": 1}, 1)
            raise RuntimeError("crash")
        assert len(s.inspect(run)["events"]) == 1
        with pytest.raises(sqlite3.IntegrityError):
            s.db.execute("UPDATE artifacts SET body='{}'")
        s.db.execute("DROP TRIGGER artifacts_no_update")
        s.db.execute("UPDATE artifacts SET body='{}'")
        with pytest.raises(IntegrityError):
            s.verify()

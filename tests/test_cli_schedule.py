import json

from typer.testing import CliRunner

from event_runtime.cli import app
from event_runtime.queue import Queue
from event_runtime.schedule import Schedules
from event_runtime.store import Store


def test_schedule_tick_restart_and_dedup(tmp_path, snapshot):
    path = tmp_path / "db"
    with Store(path) as s:
        schedule = Schedules(Queue(s))
        schedule.add("daily", snapshot, 60, 0)
        assert len(schedule.tick(180, 2)) == 2
    with Store(path) as s:
        schedule = Schedules(Queue(s))
        assert len(schedule.tick(180, 10)) == 2
        assert schedule.tick(180) == []
        assert s.db.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 4
        schedule.set_enabled("daily", False)
        assert schedule.tick(1000) == []


def test_cli_demo_review_replay(tmp_path):
    runner = CliRunner()
    out = tmp_path / "demo"
    result = runner.invoke(app, ["demo", "--out", str(out)])
    assert result.exit_code == 0, result.output
    initial = json.loads(result.output)
    assert initial["status"] == "review" and not initial["accepted"]
    prefix = ["--db", str(out / "runtime.db")]
    report = json.loads(runner.invoke(app, prefix + ["inspect", initial["run_id"]]).output)
    result = runner.invoke(
        app,
        prefix
        + [
            "review",
            initial["run_id"],
            "accept",
            "--reviewer",
            "operator",
            "--revision",
            str(report["run"]["revision"]),
            "--note",
            "Checked sources",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "completed"
    result = runner.invoke(app, prefix + ["replay", initial["run_id"]])
    assert result.exit_code == 0, result.output
    new = json.loads(result.output)["run_id"]
    assert runner.invoke(app, prefix + ["work", "--run-id", new]).exit_code == 0
    assert runner.invoke(app, prefix + ["verify"]).exit_code == 0
    result = runner.invoke(app, prefix + ["diff", initial["run_id"], new])
    assert not json.loads(result.output)["same_behavior"]


def test_missing_db_fails_without_creating(tmp_path):
    path = tmp_path / "absent.db"
    result = CliRunner().invoke(app, ["--db", str(path), "runs"])
    assert result.exit_code == 2
    assert not path.exists()

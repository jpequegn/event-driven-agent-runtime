from pathlib import Path

import pytest

from event_runtime.signoff import IMPLEMENTATIONS, SCENARIOS, observe, run_case
from event_runtime.store import Store


@pytest.mark.parametrize("scenario", SCENARIOS)
@pytest.mark.parametrize("implementation", IMPLEMENTATIONS)
def test_inert_signoff_reopens_actual_target_and_sealed_runtime(tmp_path, scenario, implementation):
    directory = tmp_path / "case"
    receipt = run_case(directory, scenario, implementation, 0)
    assert receipt["provenance"] == "synthetic"
    assert receipt["adoption_authorized"] is False
    assert receipt["known_usage"] is None
    assert receipt["duration_seconds"] >= 0
    external = observe(directory / "inert.sqlite", available=scenario != "missing-outcome")
    assert external == receipt["external"]
    with Store(directory / "runtime.sqlite") as store:
        store.verify()
    if scenario == "missing-outcome":
        assert external["rows"] is None
        assert receipt["publication"] == (
            "unknown" if implementation == "recovery" else "confirmed_success"
        )
    elif scenario == "stale-approval":
        assert receipt["approval"] is None
        assert len(external["rows"]) == (0 if implementation == "recovery" else 1)
    elif scenario == "out-of-order" and implementation == "baseline":
        assert external["rows"] == []
    elif scenario in ("crash-after", "lost-ack", "duplicate"):
        assert len(external["rows"]) == (1 if implementation == "recovery" else 2)
    else:
        assert len(external["rows"]) == 1
    if scenario in ("crash-before", "crash-after", "worker-retry"):
        assert receipt["crash_exit_codes"] == [23]
    if scenario == "worker-retry":
        assert receipt["job_attempts"] == 3
    if scenario == "review-restart":
        assert (receipt["run_id"] == receipt["original_run_id"]) == (implementation == "recovery")
    if implementation == "recovery" and scenario in ("lost-ack", "duplicate"):
        assert external["attempts"] == 2  # retried activity, one effect


def test_no_network_publisher_or_arbitrary_scenario_and_no_overwrite(tmp_path: Path):
    with pytest.raises(ValueError):
        run_case(tmp_path / "a", "https://not-an-adapter.invalid", "recovery", 0)
    (tmp_path / "keep").mkdir()
    with pytest.raises(FileExistsError):
        run_case(tmp_path / "keep", "healthy", "recovery", 0)

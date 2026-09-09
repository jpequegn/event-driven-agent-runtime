from pathlib import Path

import pytest
from pydantic import ValidationError

from event_runtime.contracts import Agent, Snapshot, Trigger, canonical, load_agent, read_json


def test_fixture():
    agents = [load_agent(p) for p in sorted(Path("examples/agents").glob("*.md"))]
    trigger = Trigger.model_validate(read_json(Path("examples/trigger.json")))
    from event_runtime.contracts import Context

    snapshot = Snapshot(trigger=trigger, agents=agents, context=Context())
    assert canonical(snapshot) == canonical(snapshot.model_dump())
    assert len(agents) == 2


def test_reject_code_and_identity_overrides():
    with pytest.raises(ValidationError):
        Agent(
            name="x",
            handler="draft",
            accepts="brief.requested",
            emits="brief.drafted",
            prompt="Text",
            command="rm",
        )
    with pytest.raises(ValidationError):
        Agent(
            name="x", handler="draft", accepts="brief.drafted", emits="brief.drafted", prompt="Text"
        )

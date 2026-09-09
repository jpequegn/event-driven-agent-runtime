from pathlib import Path

import pytest

from event_runtime.contracts import Context, Snapshot, Trigger, load_agent, read_json


@pytest.fixture
def snapshot():
    return Snapshot(
        trigger=Trigger.model_validate(read_json(Path("examples/trigger.json"))),
        context=Context.model_validate(read_json(Path("examples/context.json"))),
        agents=[load_agent(p) for p in sorted(Path("examples/agents").glob("*.md"))],
    )

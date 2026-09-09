"""Data contracts. Markdown is configuration and prompt text, never executable code."""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Text = Annotated[str, StringConstraints(min_length=1, max_length=2000)]
Profile = Literal["economy", "careful"]


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class Agent(Contract):
    name: Name
    handler: Literal["draft", "verify"]
    accepts: Literal["brief.requested", "brief.drafted"]
    emits: Literal["brief.drafted", "brief.verified"]
    prompt: Text
    max_attempts: int = Field(default=3, ge=1, le=5)

    @model_validator(mode="after")
    def compatible(self):
        expected = {
            "draft": ("brief.requested", "brief.drafted"),
            "verify": ("brief.drafted", "brief.verified"),
        }
        if (self.accepts, self.emits) != expected[self.handler]:
            raise ValueError("handler must use its typed event contract")
        return self


class Source(Contract):
    id: Name
    title: Text
    published: str
    facts: list[Text] = Field(min_length=1, max_length=8)

    @model_validator(mode="after")
    def valid_date(self):
        date.fromisoformat(self.published)
        return self


class Trigger(Contract):
    goal: Text
    window_start: str
    window_end: str
    sources: list[Source] = Field(min_length=1, max_length=10)
    required_terms: list[Name] = Field(default_factory=list, max_length=10)
    high_risk: bool = False
    fault: Literal["none", "transient", "invalid"] = "none"

    @model_validator(mode="after")
    def valid_window(self):
        if date.fromisoformat(self.window_start) > date.fromisoformat(self.window_end):
            raise ValueError("inverted source window")
        if len({s.id for s in self.sources}) != len(self.sources):
            raise ValueError("duplicate source ID")
        if len(set(self.required_terms)) != len(self.required_terms):
            raise ValueError("duplicate required term")
        return self


class Context(Contract):
    definitions: dict[Name, Text] = Field(default_factory=dict, max_length=20)
    ignored_terms: list[Name] = Field(default_factory=list, max_length=10)
    provenance: dict[Name, Text] = Field(default_factory=dict, max_length=20)


class Claim(Contract):
    text: Text
    source_id: Name


class Brief(Contract):
    title: Text
    claims: list[Claim] = Field(min_length=1, max_length=80)


class Verification(Contract):
    passed: bool
    reasons: list[str]
    missing_terms: list[str]


class Snapshot(Contract):
    trigger: Trigger
    context: Context
    agents: list[Agent] = Field(min_length=2, max_length=2)
    profile: Profile = "economy"
    capabilities: list[Literal["draft", "verify"]] = Field(
        default_factory=lambda: ["draft", "verify"], max_length=2
    )

    @model_validator(mode="after")
    def unique_agents(self):
        if {a.handler for a in self.agents} != {"draft", "verify"}:
            raise ValueError("exactly one draft and one verification agent required")
        if len({a.name for a in self.agents}) != 2:
            raise ValueError("agent names must be unique")
        return self


def canonical(value) -> str:
    if isinstance(value, BaseModel):
        value = value.model_dump(mode="json")
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def read_json(path: Path):
    raw = path.read_bytes()
    if len(raw) > 100_000:
        raise ValueError("input exceeds 100 KB")
    return json.loads(raw)


def load_agent(path: Path) -> Agent:
    raw = path.read_text()
    if len(raw.encode()) > 16_000 or not raw.startswith("---\n"):
        raise ValueError("expected bounded YAML front matter")
    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError("missing front matter terminator")
    header = yaml.safe_load(parts[0][4:])
    if not isinstance(header, dict) or "prompt" in header:
        raise ValueError("invalid agent metadata")
    return Agent.model_validate(dict(header, prompt=parts[1].strip()))

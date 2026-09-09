"""Data contracts. Markdown is configuration and prompt text, never executable code."""

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

Name = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Profile = Literal["economy", "careful"]
ArtifactHash = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]


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
        if date.fromisoformat(self.published).isoformat() != self.published:
            raise ValueError("source date must use YYYY-MM-DD")
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
        for value in (self.window_start, self.window_end):
            if date.fromisoformat(value).isoformat() != value:
                raise ValueError("window dates must use YYYY-MM-DD")
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
    brief_hash: ArtifactHash
    context_hash: ArtifactHash
    passed: bool
    reasons: list[
        Literal[
            "unknown_citation",
            "unsupported_claim",
            "outside_source_window",
            "missing_source_coverage",
            "context_gap",
        ]
    ]
    missing_terms: list[Name]

    @model_validator(mode="after")
    def consistent(self):
        if self.passed != (not self.reasons):
            raise ValueError("verification result contradicts reasons")
        if bool(self.missing_terms) != ("context_gap" in self.reasons):
            raise ValueError("context gap reasons must identify missing terms")
        return self


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
    with path.open("rb") as stream:
        raw = stream.read(100_001)
    if len(raw) > 100_000:
        raise ValueError("input exceeds 100 KB")
    return json.loads(raw, object_pairs_hook=unique_mapping)


def unique_mapping(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate mapping key: {key}")
        result[key] = value
    return result


class UniqueLoader(yaml.SafeLoader):
    pass


def yaml_mapping(loader, node):
    return unique_mapping(
        (loader.construct_object(k), loader.construct_object(v)) for k, v in node.value
    )


UniqueLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, yaml_mapping)


def load_agent(path: Path) -> Agent:
    with path.open("rb") as stream:
        data = stream.read(16_001)
    raw = data.decode("utf-8")
    if len(data) > 16_000 or not raw.startswith("---\n"):
        raise ValueError("expected bounded YAML front matter")
    parts = raw.split("\n---\n", 1)
    if len(parts) != 2:
        raise ValueError("missing front matter terminator")
    try:
        metadata = parts[0][4:]
        if any(isinstance(event, yaml.AliasEvent) for event in yaml.parse(metadata)):
            raise ValueError("YAML aliases are not supported")
        header = yaml.load(metadata, Loader=UniqueLoader)
    except yaml.YAMLError as exc:
        raise ValueError("invalid YAML front matter") from exc
    if not isinstance(header, dict) or "prompt" in header:
        raise ValueError("invalid agent metadata")
    return Agent.model_validate(dict(header, prompt=parts[1].strip()))

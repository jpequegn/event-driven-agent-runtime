---
name: brief-drafter
handler: draft
accepts: brief.requested
emits: brief.drafted
max_attempts: 3
---
Draft a concise brief using only the supplied source facts. Cite every claim.

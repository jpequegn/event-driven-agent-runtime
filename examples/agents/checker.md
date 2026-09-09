---
name: evidence-checker
handler: verify
accepts: brief.drafted
emits: brief.verified
max_attempts: 3
---
Check source IDs, exact fact support, source-window metadata, and required context.
Do not equate a draft or a passing automated check with human acceptance.

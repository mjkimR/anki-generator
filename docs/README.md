# Documentation Guide

Each document type has one job. Prefer links over repeating the same rationale in several
places.

| Need | Read or update |
|---|---|
| Understand the system as it works now | [Architecture](architecture.md) |
| Understand why a durable choice was made | [ADRs](decisions/README.md) |
| See unfinished outcomes and priorities | [Roadmap](roadmap.md) |
| Work on the codebase | [Development guide](development.md) |
| Use the product | [English user guide](user_guide/README.md) or [Korean user guide](user_guide/README.kr.md) |
| Validate card JSON | [Schema rules](schema_rules.md) |
| Inspect implementation chronology | Git history |

## Content ownership

- **Architecture says what is true now.** No dates, shipped markers, backlog, or rejected
  alternatives.
- **ADRs say why a consequential choice was made.** Accepted ADRs are append-only; a changed
  decision gets a new ADR that supersedes the old one.
- **Roadmap says what remains to achieve.** Completed items are removed, not retained as a
  changelog.
- **Git preserves chronology.** Do not maintain a second implementation-history document.
- **User guides explain workflows.** Keep the English and Korean guides behaviorally aligned.
- **Development instructions protect implementation constraints.** They may summarize an
  invariant but should link to its ADR for rationale.

## Change workflow

```text
idea -> roadmap
     -> proposed ADR when a consequential choice is needed
     -> accepted ADR after the decision
     -> architecture update when implementation lands
     -> remove the roadmap item
```

Do not copy a completed roadmap section into another active document. Move only current facts
to architecture and only durable rationale to an ADR.

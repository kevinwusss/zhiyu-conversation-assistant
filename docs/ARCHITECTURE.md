# Architecture and engineering decisions

| Layer | Files | Responsibility |
|---|---|---|
| Interface | `app/ui/` | User interaction, background work and visible error states |
| Orchestration | `app/core/engine.py` | Build context, generate candidates, record drafts and coordinate delivery |
| Persistence | `app/database.py` | SQLite transactions, migrations and receipts |
| Context | `app/memory/`, `app/persona/` | Recent history, keyword retrieval, explicit facts and descriptive style statistics |
| Model transport | `app/llm/provider.py` | HTTPS requests, timeouts and validated structured responses |
| Client integration | `app/adapters/` | Platform-specific read/fill/send behaviour |
| Controls | `app/automation/safety.py` | Pause, rate limits and operation checks |

## Decisions and trade-offs

SQLite keeps installation simple and supports inspection, but this prototype has no multi-user server architecture. A provider protocol makes deterministic tests possible without claiming mock output represents model quality. Heuristic ranking is understandable but does not establish correctness. Extractive context avoids a separate summarisation call but can lose meaning and consume context space.

Snapshots bind operations to observed context. Delivery results distinguish success from uncertainty, and uncertainty blocks automatic retry. These measures reduce particular failure modes; they do not prove that all UI automation is safe. Accessibility trees and client versions remain external dependencies.

## Data flow and privacy

Manual imports and client reads can enter local SQLite storage. Candidate generation transmits selected context to the configured external model API. Successful-send feedback can affect future style statistics. Derived memory deletion preserves original conversation data and duplicate-prevention receipts. There is no claim of end-to-end encryption, legal compliance certification or automatic anonymisation of model prompts.

## Next research questions

Compare context-free suggestions with history and persona variants on a consented or synthetic evaluation set. Define relevance, style fit and unsafe commitment criteria before collecting ratings. Report sample size, annotation method, disagreement and confidence intervals. Measure generation latency separately from UI automation. These are future experiments, not completed results.

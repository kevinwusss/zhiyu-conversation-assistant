# Evaluation and reproducibility

Automated regression tests cover candidate validation, context changes, conversation isolation, memory deletion, duplicate delivery, uncertain outcomes, risk controls, parsers and interface state. These are behavioural tests; they are not a language-model benchmark or a security proof.

The offline demo uses synthetic input and fixed replies with a temporary SQLite database. The UI verification script uses synthetic conversations and a mock provider. Its screenshots must be labelled accordingly.

One legacy wxauto test accesses the actual desktop and is opt-in via `ZHIYU_LIVE_UIA_TEST=1`. It requires the separately installed legacy backend and an interactive Windows session. Default CI excludes this integration test explicitly.

The workflow targets Windows and Python 3.12. A hosted CI result is only established after GitHub actually runs it. Local results are recorded below after execution. No live messaging, recipient delivery, real API quality or admissions outcome is established by these checks.

## Local verification — 28 September 2026

Environment: Windows, Python 3.14, PySide6 6.11.2, using the existing project's interpreter against the separate export.

| Check | Observed result |
|---|---|
| Unit/UI suite | 133 tests run: 132 passed, 1 explicitly skipped legacy desktop integration test |
| Ruff | Passed for app, tests and scripts |
| Offline demo | Passed; three fixed mock candidates; temporary database; no external sends |
| Application smoke test | Passed; window opened and closed normally |
| Synthetic UI verification | Passed; inspected the light-theme screenshot included in README |
| Publication scanner | No configured credential or private-file patterns found in publication files |
| Exact private-config comparison | No current private configuration credential values found in publication text |
| Fresh virtual-environment installation | Not completed: the Qt wheel download stalled on the default index and an alternate mirror; attempts were stopped |
| Python 3.12 / GitHub-hosted CI | Not executed locally; workflow provided for verification after upload |
| Live WeChat/QQ/DingTalk and optional backend installation | Not validated in this release preparation |

The stale auto-reply test was updated to enable the already-required per-contact opt-in. The corresponding runtime protection was retained. One optional backend import is mocked in its unit test so it does not force installation of a live client integration.

There is no measured model accuracy, latency benchmark, user-study result or coverage percentage. Dependency ranges are not a full lockfile, so future installations may resolve different package versions.

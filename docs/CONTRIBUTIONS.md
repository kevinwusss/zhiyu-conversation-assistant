# Contribution and AI-assistance disclosure

AI coding assistance was used in developing and preparing this repository. The code alone cannot establish who personally authored, designed or reviewed every component.

Before submitting an application, the applicant should record their own verified contribution in these categories: problem definition; requirements and trade-offs; code personally implemented or reviewed; tests personally executed; failures investigated; limitations understood. Use actual examples and commits where available. Do not claim sole unaided authorship, invented collaborators, historical commits or unperformed experiments.

The portfolio preparation added a separate export, English documentation, a synthetic offline demonstration, dependency installation without local reference directories, a release checker and a Windows CI workflow. Existing application modules and tests were carried over. The optional legacy desktop test was made explicitly opt-in. Publication and hosted CI are separate steps.

Release preparation also corrected a stale UI test to enable the existing per-contact auto-reply opt-in, mocked an optional backend import in a unit test, and fixed one formatting violation. The runtime opt-in protection was preserved. Base and optional client dependencies were separated to make the offline demonstration independent of WeChat packages.

A defensible short description, after personally reproducing and understanding the demonstration:

> Developed an AI-assisted Windows conversation-assistant prototype integrating Python, PySide6, SQLite and a language-model API. Explored contact-specific context, editable reply suggestions and delivery controls, and evaluated application behaviour using automated tests and synthetic demonstrations.

Edit this wording to reflect the applicant's actual role. Do not turn test counts into model accuracy, and do not claim this project guarantees an admissions outcome.

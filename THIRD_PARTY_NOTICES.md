# Third-party components and provenance

This export contains the application's `app/` and `tests/` code, selected scripts and new portfolio documentation. It excludes downloaded reference repositories and private runtime files.

- **PySide6 Essentials / Qt:** graphical interface; upstream licensing applies.
- **requests / python-dotenv:** HTTP transport and configuration parsing.
- **uiautomation / pywin32 / winocr:** Windows integration and optional OCR facilities.
- **wechatauto-replica 1.2.1:** optional WeChat backend, declared in the separate integrations requirements for installation from the package index. The locally inspected upstream package metadata declares Apache-2.0. Upstream: https://github.com/fanyuantaier/wechatauto-replica . No upstream source is bundled here.
- **wxauto:** a legacy adapter remains in `app/adapters/wechat_legacy.py`; this backend is not installed by the default requirements and is not part of the default verification path. Historical project notes identify Apache-2.0; verify the chosen upstream revision before separately distributing it.
- **Fay and LangChain:** prior project notes describe architectural inspiration. Neither complete reference repository is included or required by this export. This preparation is not a line-by-line copyright audit.
- **DeepSeek-compatible model service:** an external inference service, not a model trained by the applicant.

Do not describe these libraries, model weights or platform internals as original work. This document is attribution, not a replacement for dependency licence texts or a grant of rights over third-party material.

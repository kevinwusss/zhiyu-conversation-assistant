"""Check Git-tracked (or pre-Git export) files without printing secret values."""
from pathlib import Path
import re
import subprocess

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED = {".git", ".venv", "__pycache__", ".ruff_cache", ".pytest_cache", ".impeccable"}
PRIVATE = {"data", "logs", "wechatauto_logs", "github"}


def main():
    if (ROOT / ".git").exists():
        result = subprocess.run(["git", "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True)
        paths = [ROOT / p for p in result.stdout.decode("utf-8").split("\0") if p]
    else:
        paths = [p for p in ROOT.rglob("*") if p.is_file() and not EXCLUDED.intersection(p.relative_to(ROOT).parts)]
    findings = []
    for path in paths:
        rel = path.relative_to(ROOT)
        if PRIVATE.intersection(rel.parts) or (path.name.startswith(".env") and path.name != ".env.example") or path.suffix in {".db", ".sqlite", ".log"}:
            findings.append(f"private/runtime file: {rel}")
        if path.suffix.lower() in {".png", ".jpg"}:
            continue
        value = path.read_text(encoding="utf-8", errors="replace")
        if re.search(r"sk-[A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|-----BEGIN (?:RSA |OPENSSH )?PRIVATE KEY-----", value):
            findings.append(f"credential pattern: {rel}")
        if re.search(r"[A-Za-z]:\\Users\\", value):
            findings.append(f"personal local path: {rel}")
    if findings:
        raise SystemExit("\n".join(findings))
    print(f"PASS: scanned {len(paths)} files; no configured private-file or credential patterns found.")
    print("Pattern checks do not replace reviewing screenshots and the Git staged diff.")


if __name__ == "__main__":
    main()

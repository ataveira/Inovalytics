import json
import re
from pathlib import Path

DEP_RE = re.compile(r"ultralytics", re.IGNORECASE)
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+ultralytics\b", re.MULTILINE | re.IGNORECASE)


def _targets(root: Path) -> list[Path]:
    files = [root / "pyproject.toml", root / "frontend" / "package.json"]
    for d in ("backend", "docker"):
        files += [
            p
            for p in (root / d).rglob("*")
            if p.is_file() and "__pycache__" not in p.parts and p != Path(__file__).resolve()
        ]
    return files


def test_no_ultralytics(repo_root: Path) -> None:
    offenders = []
    for p in _targets(repo_root):
        text = p.read_text(encoding="utf-8", errors="ignore")
        if p.name in ("pyproject.toml", "package.json"):
            if DEP_RE.search(text):
                offenders.append(p)
        elif (
            IMPORT_RE.search(text)
            or p.suffix in (".txt", ".yml", ".yaml", "")
            and DEP_RE.search(text)
        ):
            offenders.append(p)
    assert not offenders, offenders


def test_package_json_parses(repo_root: Path) -> None:
    json.loads((repo_root / "frontend" / "package.json").read_text(encoding="utf-8"))

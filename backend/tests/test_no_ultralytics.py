import json
import re
from pathlib import Path

DEP_RE = re.compile(r"ultralytics", re.IGNORECASE)
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+ultralytics\b", re.MULTILINE | re.IGNORECASE)


IGNORED_DIRS = {".venv", "node_modules", "__pycache__", "docs", ".git"}
CACHE_SUFFIXES = ("_cache",)


def _ignored(p: Path) -> bool:
    return any(part in IGNORED_DIRS or part.endswith(CACHE_SUFFIXES) for part in p.parts)


def is_dependency_file(p: Path) -> bool:
    """Qualquer arquivo que não seja código Python (Dockerfile, .toml, .cfg, .json, .sh...)."""
    return p.suffix != ".py"


def _targets(root: Path) -> list[Path]:
    files = [root / "pyproject.toml", root / "frontend" / "package.json"]
    self_path = Path(__file__).resolve()
    for d in ("backend", "docker", ".github"):
        files += [
            p
            for p in (root / d).rglob("*")
            if p.is_file() and not _ignored(p.relative_to(root)) and p.resolve() != self_path
        ]
    return files


def _read_text(p: Path) -> str | None:
    data = p.read_bytes()
    if b"\x00" in data:  # binário
        return None
    return data.decode("utf-8", errors="ignore")


def test_no_ultralytics(repo_root: Path) -> None:
    offenders = []
    for p in _targets(repo_root):
        text = _read_text(p)
        if text is None:
            continue
        pattern = DEP_RE if is_dependency_file(p) else IMPORT_RE
        if pattern.search(text):
            offenders.append(p)
    assert not offenders, offenders


def test_package_json_parses(repo_root: Path) -> None:
    json.loads((repo_root / "frontend" / "package.json").read_text(encoding="utf-8"))

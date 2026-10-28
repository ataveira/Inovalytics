import re
from pathlib import Path

from _files import project_files

SECRET_RE = re.compile(
    r"""(?ix)
    \b(?:\w*password|\w*secret|\w*token|api[_-]?key|access[_-]?key)\w*
    [ \t]*[=:][ \t]*["']?([^\s"'<>${}#]{4,})
    """
)
PRIVATE_KEY_RE = re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")
PLACEHOLDERS = {"changeme", "example", "placeholder", "xxxx", "none", "null", "true", "false"}
BINARY = {".png", ".jpg", ".ico", ".lock", ".pyc"}


def _is_credential(path: Path, text: str, m: re.Match[str]) -> bool:
    if m.group(1).lower() in PLACEHOLDERS:
        return False
    if text[max(m.start() - 2, 0) : m.start()] == "${":  # `${VAR:?mensagem}`: referência
        return False
    # Em Python, atribuição sem aspas é expressão (ex.: `x_password=e.get(...)`), não literal.
    return not (path.suffix == ".py" and text[m.start(1) - 1] not in "\"'")


def test_no_real_credentials_in_files(repo_root: Path) -> None:
    offenders = []
    for p in project_files(repo_root):
        if p == Path(__file__).resolve() or p.suffix in BINARY or p.name == "package-lock.json":
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        if PRIVATE_KEY_RE.search(text) or any(
            _is_credential(p, text, m) for m in SECRET_RE.finditer(text)
        ):
            offenders.append(p)
    assert not offenders, offenders


def test_env_not_tracked(repo_root: Path) -> None:
    names = {p.name for p in project_files(repo_root)}
    assert ".env" not in names

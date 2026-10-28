import json
import re
import tomllib
from pathlib import Path

ROW_RE = re.compile(r"^\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*$")
SPDX_RE = re.compile(r"^[A-Za-z0-9.+-]+( (OR|AND) [A-Za-z0-9.+-]+)*$")
FORBIDDEN = ("AGPL", "GPL-", "SSPL")


def _norm(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def _declared(root: Path) -> set[str]:
    names: set[str] = set()
    proj = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    reqs = list(proj.get("dependencies", []))
    for group in proj.get("optional-dependencies", {}).values():
        reqs += group
    for req in reqs:
        m = re.match(r"[A-Za-z0-9._-]+", req)
        assert m, req
        names.add(_norm(m.group(0)))
    pkg = json.loads((root / "frontend" / "package.json").read_text(encoding="utf-8"))
    for key in ("dependencies", "devDependencies", "optionalDependencies"):
        names |= {_norm(n) for n in pkg.get(key, {})}
    return names


def _rows(root: Path) -> dict[str, tuple[str, str, str]]:
    rows: dict[str, tuple[str, str, str]] = {}
    for line in (root / "docs" / "licencas.md").read_text(encoding="utf-8").splitlines():
        m = ROW_RE.match(line)
        if m and m.group(1) not in ("Nome",) and not set(m.group(1)) <= {"-"}:
            rows[_norm(m.group(1))] = (m.group(2), m.group(3), m.group(4))
    return rows


def test_all_dependencies_registered(repo_root: Path) -> None:
    declared, rows = _declared(repo_root), _rows(repo_root)
    assert declared
    assert not declared - rows.keys(), declared - rows.keys()


def test_registered_rows_are_complete(repo_root: Path) -> None:
    for name, (version, spdx, url) in _rows(repo_root).items():
        assert version, name
        assert SPDX_RE.match(spdx), (name, spdx)
        assert not any(f in spdx.upper() for f in FORBIDDEN), (name, spdx)
        assert url.startswith("https://"), (name, url)


def _container_rows(root: Path) -> list[tuple[str, str]]:
    text = (root / "docs" / "licencas.md").read_text(encoding="utf-8")
    section = text.split("## Imagens de contêiner", 1)[1].split("\n## ", 1)[0]
    rows = []
    for line in section.splitlines():
        m = ROW_RE.match(line)
        if m and m.group(1) not in ("Nome",) and not set(m.group(1)) <= {"-"}:
            rows.append((m.group(1), m.group(2)))
    return rows


def _used_images(root: Path) -> dict[str, str]:
    """Imagens usadas (compose sem a build da API, mais FROM do Dockerfile): repo -> tag."""
    import yaml

    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    refs = [s["image"] for s in compose["services"].values() if "build" not in s]
    for line in (root / "docker" / "api" / "Dockerfile").read_text(encoding="utf-8").splitlines():
        if line.upper().startswith("FROM "):
            refs.append(line.split()[1])
    images = {}
    for ref in refs:
        repo, tag = ref.rsplit(":", 1)
        images[repo] = tag
    return images


def test_container_image_tags_match_compose_and_dockerfile(repo_root: Path) -> None:
    rows = _container_rows(repo_root)
    images = _used_images(repo_root)
    assert rows and images
    documented = set()
    for repo, tag in images.items():
        matches = [r for r in rows if repo.split("/")[-1] in r[0] or repo in r[0]]
        assert len(matches) == 1, f"imagem {repo} sem linha única em docs/licencas.md"
        version = matches[0][1]
        # A tag pode ter sufixo de variante (ex.: 3.12.11-slim-bookworm); o prefixo é a versão.
        assert tag == version or tag.startswith(version + "-"), (repo, tag, version)
        documented.add(matches[0])
    assert documented == set(rows), "linha em docs/licencas.md sem imagem correspondente"

"""Coletores da verificação de licenças (F0-04b): Python, npm, imagens e GitHub Actions.

Só leitura local: ``importlib.metadata``, ``json``, ``tomllib`` e texto. Sem rede, sem
``subprocess``, sem executar ``npm`` ou ``pip``. ``packaging`` e PyYAML são importados tarde.
"""

from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from typing import Any

from inovalytics.tools.license_policy import (
    DESCONHECIDA,
    DIVERGE_DO_REGISTRO,
    NAO_REGISTRADO,
    ULTRALYTICS,
    Component,
    Violation,
    pep503,
)
from inovalytics.tools.license_registry import DocRow

# Montada por concatenação: o gate de texto do repositório varre este diretório.
_VENDOR = "ultra" + "lytics"


class InputError(Exception):
    """Entrada ilegível ou inválida (arquivo faltando, TOML/JSON quebrado, lock incompatível)."""


@dataclass(frozen=True)
class Collected:
    components: tuple[Component, ...]
    issues: tuple[Violation, ...]


# ---------------------------------------------------------------- Python


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise InputError(f"não foi possível ler {path}: {exc}") from exc


def _load_toml(path: Path) -> dict[str, Any]:
    try:
        return tomllib.loads(_read(path))
    except tomllib.TOMLDecodeError as exc:
        raise InputError(f"TOML inválido em {path}: {exc}") from exc


def _load_json(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(_read(path))
    except json.JSONDecodeError as exc:
        raise InputError(f"JSON inválido em {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise InputError(f"JSON inválido em {path}: esperado um objeto")
    return data


@dataclass
class _Walk:
    required_by: dict[str, set[str]]
    missing: set[str]
    issues: list[Violation]


def _walk(
    roots: Sequence[str], dists: dict[str, metadata.Distribution], root_name: str, label: str
) -> _Walk:
    from packaging.requirements import InvalidRequirement, Requirement

    out = _Walk({}, set(), [])
    done: dict[str, set[str]] = {}
    stack: list[tuple[str, str]] = [(r, label) for r in roots]
    while stack:
        req_str, parent = stack.pop()
        try:
            req = Requirement(req_str)
        except InvalidRequirement:
            out.issues.append(
                Violation(
                    DESCONHECIDA, "python", "", req_str, "", f"{req_str}: requisito ilegível."
                )
            )
            continue
        name = pep503(req.name)
        if name == root_name:
            continue
        extras = {pep503(e) for e in req.extras}
        env_extras = {""} if parent == label else {""} | done.get(parent, set())
        if req.marker is not None and not any(
            req.marker.evaluate({"extra": e}) for e in env_extras
        ):
            continue
        out.required_by.setdefault(name, set()).add(parent)
        dist = dists.get(name)
        if dist is None:
            out.missing.add(name)
            continue
        first = name not in done
        new = extras - done.get(name, set())
        if not first and not new:
            continue
        done.setdefault(name, set()).update(extras)
        for line in dist.requires or []:
            stack.append((line, name))
    return out


def _meta(md: Any, key: str) -> str | None:
    values = md.get_all(key)
    return str(values[0]) if values else None


def _dist_components_refs(dist: metadata.Distribution) -> tuple[str, ...]:
    md = dist.metadata
    refs: list[str] = list(dist.requires or [])
    for key in ("Home-page", "Author", "Author-email", "Maintainer", "Maintainer-email"):
        val = _meta(md, key)
        if val:
            refs.append(val)
    refs.extend(md.get_all("Project-URL") or [])
    return tuple(refs)


def collect_python(root: Path, metadata_paths: Sequence[str] | None = None) -> Collected:
    proj = _load_toml(root / "pyproject.toml").get("project", {})
    root_name = pep503(str(proj.get("name", "")))
    runtime_roots = list(proj.get("dependencies", []))
    dev_roots = list(proj.get("optional-dependencies", {}).get("dev", []))
    dists: dict[str, metadata.Distribution] = {}
    found = (
        metadata.distributions(path=list(metadata_paths))
        if metadata_paths is not None
        else metadata.distributions()
    )
    for d in found:
        n = _meta(d.metadata, "Name")
        if n:
            dists.setdefault(pep503(n), d)

    rt = _walk(runtime_roots, dists, root_name, "projeto")
    dv = _walk(dev_roots, dists, root_name, "projeto (dev)")
    direct = set()
    from packaging.requirements import Requirement

    for r in [*runtime_roots, *dev_roots]:
        direct.add(pep503(Requirement(r).name))

    comps: list[Component] = []
    for name in sorted({*rt.required_by, *dv.required_by}):
        runtime = name in rt.required_by
        group = "runtime" if runtime else "dev"
        by = rt.required_by[name] if runtime else dv.required_by[name]
        dist = dists.get(name)
        common: dict[str, Any] = {
            "ecosystem": "python",
            "name": name,
            "group": group,
            "required_by": tuple(sorted(by)),
            "direct": name in direct,
        }
        if dist is None:
            comps.append(Component(version="", installed=False, **common))
            continue
        md = dist.metadata
        classifiers = tuple(c for c in (md.get_all("Classifier") or []) if c.startswith("License"))
        comps.append(
            Component(
                version=dist.version,
                expression=_meta(md, "License-Expression"),
                license_field=_meta(md, "License"),
                classifiers=classifiers,
                references=_dist_components_refs(dist),
                **common,
            )
        )
    return Collected(tuple(comps), tuple(rt.issues + dv.issues))


# ---------------------------------------------------------------- npm

_NPM_REGISTRY = "https://registry.npmjs.org/"


def _npm_name(key: str) -> str:
    return key.rsplit("node_modules/", 1)[-1]


def _npm_license(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, dict) and isinstance(value.get("type"), str):
        return str(value["type"])
    if isinstance(value, list) and value:
        parts = [_npm_license(v) for v in value]
        if all(parts):
            return "(" + " OR ".join(str(p) for p in parts) + ")"
    return "ILEGIVEL"


def collect_npm(root: Path, frontend: str = "frontend") -> Collected:
    lock = _load_json(root / frontend / "package-lock.json")
    package_json = _load_json(root / frontend / "package.json")
    if lock.get("lockfileVersion") not in (2, 3):
        raise InputError(
            f"package-lock.json: lockfileVersion {lock.get('lockfileVersion')!r} não suportado "
            "(use 2 ou 3)."
        )
    declared: set[str] = set()
    for key in ("dependencies", "devDependencies", "optionalDependencies", "peerDependencies"):
        declared |= set(package_json.get(key, {}))
    packages: dict[str, Any] = lock.get("packages", {})
    if not isinstance(packages, dict):
        raise InputError("package-lock.json: 'packages' deve ser um objeto.")
    for pkey, pentry in packages.items():
        if not isinstance(pentry, dict):
            raise InputError(
                f"package-lock.json: entrada {pkey!r} inválida (esperado objeto, "
                f"veio {type(pentry).__name__})."
            )
    issues: list[Violation] = []
    comps: list[Component] = []
    top_level = {_npm_name(k) for k in packages if k.count("node_modules/") == 1}
    for name in sorted(declared - top_level):
        issues.append(
            Violation(
                NAO_REGISTRADO, "npm", "", name, "",
                f"{name}: declarado em package.json mas ausente do lock (lock desatualizado).",
            )
        )  # fmt: skip
    for key, entry in sorted(packages.items()):
        if key == "":
            continue
        name = _npm_name(key)
        group = "dev" if entry.get("dev") or entry.get("devOptional") else "runtime"
        version = str(entry.get("version", ""))
        resolved = entry.get("resolved")
        if isinstance(resolved, str) and not resolved.startswith(_NPM_REGISTRY):
            issues.append(
                Violation(
                    DESCONHECIDA, "npm", group, name, version,
                    f"{name}: origem fora do registro npm ({resolved}); não verificável.",
                )
            )  # fmt: skip
        refs: list[str] = [name, key]
        if isinstance(resolved, str):
            refs.append(resolved)
        for dep_key in ("dependencies", "peerDependencies", "optionalDependencies"):
            for dname, dspec in (entry.get(dep_key) or {}).items():
                refs.extend([str(dname), str(dspec)])
        comps.append(
            Component(
                ecosystem="npm",
                name=name,
                version=version,
                group=group,
                expression=_npm_license(entry.get("license")),
                references=tuple(refs),
                direct=name in declared and key.count("node_modules/") == 1,
                needs_registry=name in declared and key.count("node_modules/") == 1,
            )
        )
    return Collected(tuple(comps), tuple(issues))


# ---------------------------------------------------------------- imagens e actions

_FROM = re.compile(r"^\s*FROM\s+((?:--\S+\s+)*)(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)


def split_image(ref: str) -> tuple[str, str] | None:
    """(repositório, tag) ou None se não houver tag explícita."""
    ref = ref.split("@", 1)[0]
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        return None
    repo, tag = ref.rsplit(":", 1)
    return repo, tag


def _compose_files(root: Path) -> list[Path]:
    """docker-compose*.yml|yaml e compose*.yml|yaml na raiz e (recursivo) sob docker/."""
    found: set[Path] = set()
    docker_dir = root / "docker"
    scopes = [(root, False)] + ([(docker_dir, True)] if docker_dir.is_dir() else [])
    for base, recursive in scopes:
        for prefix in ("docker-compose", "compose"):
            for ext in ("yml", "yaml"):
                pattern = f"{prefix}*.{ext}"
                found.update(base.rglob(pattern) if recursive else base.glob(pattern))
    return sorted(p for p in found if p.is_file())


def _dockerfiles(root: Path) -> list[Path]:
    """Dockerfile, Dockerfile.* e *.Dockerfile na raiz e (recursivo) sob docker/."""
    found: set[Path] = set()
    docker_dir = root / "docker"
    scopes = [(root, False)] + ([(docker_dir, True)] if docker_dir.is_dir() else [])
    for base, recursive in scopes:
        for pattern in ("Dockerfile", "Dockerfile.*", "*.Dockerfile"):
            found.update(base.rglob(pattern) if recursive else base.glob(pattern))
    return sorted(p for p in found if p.is_file())


def _image_refs(root: Path) -> tuple[list[str], list[Violation]]:
    import yaml

    refs: list[str] = []
    issues: list[Violation] = []
    for compose_path in _compose_files(root):
        try:
            compose = yaml.safe_load(_read(compose_path)) or {}
        except yaml.YAMLError as exc:
            raise InputError(f"YAML inválido em {compose_path}: {exc}") from exc
        services = compose.get("services") if isinstance(compose, dict) else None
        for svc in (services or {}).values():
            if isinstance(svc, dict) and "image" in svc and "build" not in svc:
                refs.append(str(svc["image"]))
    for df in _dockerfiles(root):
        stages: set[str] = set()
        for line in _read(df).splitlines():
            m = _FROM.match(line)
            if not m:
                continue
            image = m.group(2)
            if image.lower() == "scratch" or image.lower() in stages:
                if m.group(3):
                    stages.add(m.group(3).lower())
                continue
            if m.group(3):
                stages.add(m.group(3).lower())
            refs.append(image)
    return refs, issues


def _canon_repo(repo: str) -> str:
    """Repositório completo, sem o registro padrão do Docker Hub nem o namespace ``library/``."""
    repo = repo.strip().lower()
    for prefix in ("index.docker.io/", "docker.io/"):
        if repo.startswith(prefix):
            repo = repo[len(prefix) :]
    return repo.removeprefix("library/")


def _row_repo(row: DocRow) -> str:
    """Repositório de uma linha do registro: o nome sem a nota entre parênteses."""
    return _canon_repo(row.name.split(" (", 1)[0])


def collect_images(root: Path, rows: Sequence[DocRow]) -> Collected:
    refs, issues = _image_refs(root)
    comps: list[Component] = []
    used_rows: set[str] = set()
    seen: set[str] = set()
    seen_pairs: set[tuple[str, str]] = set()

    def bad(code: str, ref: str, msg: str) -> None:
        issues.append(Violation(code, "image", "runtime", ref, "", msg))

    for ref in refs:
        if ref in seen:
            continue
        seen.add(ref)
        # Antes de qualquer `continue`: sem tag, com variável ou não registrada também conta.
        if _VENDOR in ref.lower():
            bad(
                ULTRALYTICS,
                ref,
                f"{ref}: ligado à Ultralytics (AGPL-3.0), proibido; exceção não se aplica.",
            )
        if "${" in ref or "$" in ref:
            bad(DESCONHECIDA, ref, f"{ref}: referência de imagem não resolvida (variável).")
            continue
        parts = split_image(ref)
        if parts is None or parts[1] == "latest":
            bad(DESCONHECIDA, ref, f"{ref}: imagem sem tag fixa (ou :latest) não é verificável.")
            continue
        repo, tag = parts
        if (repo, tag) in seen_pairs:
            continue
        seen_pairs.add((repo, tag))
        matches = [r for r in rows if _row_repo(r) == _canon_repo(repo)]
        if len(matches) != 1:
            bad(
                NAO_REGISTRADO,
                ref,
                f"{ref}: sem linha única em 'Imagens de contêiner' de docs/licencas.md.",
            )
            continue
        row = matches[0]
        used_rows.add(row.name)
        if not (tag == row.version or tag.startswith(row.version + "-")):
            bad(
                DIVERGE_DO_REGISTRO,
                ref,
                f"{ref}: tag {tag} difere da versão registrada ({row.version}).",
            )
        comps.append(
            Component(
                ecosystem="image",
                name=repo,
                version=tag,
                group="runtime",
                expression=row.spdx,
                references=(repo, ref),
                needs_registry=False,
            )
        )
    for row in rows:
        if row.name not in used_rows:
            bad(
                DIVERGE_DO_REGISTRO,
                row.name,
                f"{row.name}: linha de imagem sem uso no compose/Dockerfile.",
            )
    return Collected(tuple(comps), tuple(issues))


_USES = re.compile(r"^\s*-?\s*uses:\s*['\"]?([^\s'\"#]+)")


def _workflow_uses(root: Path) -> list[str]:
    wf = root / ".github" / "workflows"
    files: Iterable[Path] = sorted([*wf.glob("*.yml"), *wf.glob("*.yaml")]) if wf.is_dir() else []
    out: list[str] = []
    for f in files:
        for line in _read(f).splitlines():
            m = _USES.match(line)
            if m:
                out.append(m.group(1))
    return out


def collect_actions(root: Path, rows: Sequence[DocRow]) -> Collected:
    comps: list[Component] = []
    issues: list[Violation] = []
    used: set[str] = set()
    seen: set[str] = set()

    def bad(code: str, ref: str, msg: str) -> None:
        issues.append(Violation(code, "action", "dev", ref, "", msg))

    for ref in _workflow_uses(root):
        if ref.startswith("./") or ref in seen:
            continue
        seen.add(ref)
        # Antes de qualquer `continue`: action sem versão, docker:// ou não registrada também conta.
        if _VENDOR in ref.lower():
            bad(
                ULTRALYTICS,
                ref,
                f"{ref}: ligado à Ultralytics (AGPL-3.0), proibido; exceção não se aplica.",
            )
        name, _, version = ref.partition("@")
        segs = name.split("/")
        if ref.startswith("docker://") or len(segs) < 2 or not version:
            bad(
                DESCONHECIDA,
                ref,
                f"{ref}: action não verificável (docker://, sem dono/repo ou sem versão).",
            )
            continue
        full = "/".join(segs[:2])
        if segs[0] != "actions":
            bad(
                DESCONHECIDA, ref, f"{ref}: somente actions oficiais (dono 'actions/') são aceitas."
            )
        matches = [r for r in rows if r.name == full]
        if len(matches) != 1:
            bad(
                NAO_REGISTRADO,
                ref,
                f"{ref}: sem linha única na seção 'GitHub Actions' de docs/licencas.md.",
            )
            continue
        used.add(full)
        if matches[0].version != version:
            bad(
                DIVERGE_DO_REGISTRO,
                ref,
                f"{ref}: versão difere da registrada ({matches[0].version}).",
            )
        comps.append(
            Component(
                ecosystem="action",
                name=full,
                version=version,
                group="dev",
                expression=matches[0].spdx,
                references=(ref,),
                needs_registry=False,
            )
        )
    for row in rows:
        if row.name not in used:
            bad(
                DIVERGE_DO_REGISTRO, row.name, f"{row.name}: linha de action sem uso nos workflows."
            )
    return Collected(tuple(comps), tuple(issues))

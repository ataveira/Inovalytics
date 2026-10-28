"""Núcleo puro da verificação de licenças (F0-04a).

Nada aqui lê arquivos, ambiente ou rede: ``verify`` trabalha só sobre dados injetados. A única
importação tardia é ``packaging`` (avaliação de especificadores de versão das exceções).
Não é parecer jurídico: ver ADR-004 para a política e os limites.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date

ECOSYSTEMS = ("python", "npm", "image", "action")
GROUPS = ("runtime", "dev")

PROIBIDA = "PROIBIDA"
DESCONHECIDA = "DESCONHECIDA"
AUSENTE = "AUSENTE"
NAO_REGISTRADO = "NAO_REGISTRADO"
DIVERGE_DO_REGISTRO = "DIVERGE_DO_REGISTRO"
ULTRALYTICS = "ULTRALYTICS"
GRUPO_INVALIDO = "GRUPO_INVALIDO"
EXCECAO_INVALIDA = "EXCECAO_INVALIDA"

# A palavra é montada por concatenação: o gate de texto do repositório varre este diretório.
_FORBIDDEN_VENDOR = "ultra" + "lytics"

GENERIC_MARK = "normalizado de forma genérica"


@dataclass(frozen=True)
class Policy:
    allowed: frozenset[str]
    exception_only: frozenset[str]


DEFAULT_POLICY = Policy(
    allowed=frozenset(
        {
            "MIT",
            "BSD-2-Clause",
            "BSD-3-Clause",
            "Apache-2.0",
            "ISC",
            "PSF-2.0",
            "Python-2.0",
            "0BSD",
            "Unlicense",
            "CC0-1.0",
            "PostgreSQL",
            "Zlib",
            "BlueOak-1.0.0",
        }
    ),
    exception_only=frozenset({"MPL-2.0"}),
)

# ---------------------------------------------------------------- normalização

_FORBIDDEN_RE = re.compile(
    r"(agpl|\bgpl|lgpl|sspl|busl|business source|non[-_ ]?commercial"
    r"|server[-_ ]side[-_ ]public|proprietary|\bgnu\b|affero|-nc(?![a-z]))",
    re.IGNORECASE,
)
_EMPTY = {"", "unknown", "none", "n/a", "na", "null"}
_MAX_FIELD_LEN = 100

# Chave: texto em minúsculas com espaços colapsados. Valor: (id SPDX, genérico?).
_LEGACY: dict[str, tuple[str, bool]] = {
    "mit license": ("MIT", False),
    "the mit license": ("MIT", False),
    "bsd license": ("BSD-3-Clause", True),
    "apache software license": ("Apache-2.0", True),
    "apache 2.0": ("Apache-2.0", False),
    "apache 2": ("Apache-2.0", False),
    "apache license 2.0": ("Apache-2.0", False),
    "apache license, version 2.0": ("Apache-2.0", False),
    "apache license v2.0": ("Apache-2.0", False),
    "apache software license 2.0": ("Apache-2.0", False),
    "bsd 3-clause license": ("BSD-3-Clause", False),
    "bsd-3-clause license": ("BSD-3-Clause", False),
    "new bsd license": ("BSD-3-Clause", False),
    "3-clause bsd license": ("BSD-3-Clause", False),
    "bsd 2-clause license": ("BSD-2-Clause", False),
    "2-clause bsd license": ("BSD-2-Clause", False),
    "simplified bsd license": ("BSD-2-Clause", False),
    "python software foundation license": ("PSF-2.0", False),
    "psf license": ("PSF-2.0", False),
    "isc license": ("ISC", False),
    "isc license (iscl)": ("ISC", False),
    "the unlicense": ("Unlicense", False),
    "the unlicense (unlicense)": ("Unlicense", False),
    "cc0 1.0 universal (cc0 1.0) public domain dedication": ("CC0-1.0", False),
    "zlib license": ("Zlib", False),
    "zlib/libpng license (zlib-acknowledgement)": ("Zlib", False),
    "postgresql license": ("PostgreSQL", False),
    "blue oak model license 1.0.0": ("BlueOak-1.0.0", False),
    "mozilla public license 2.0": ("MPL-2.0", False),
    "mozilla public license 2.0 (mpl 2.0)": ("MPL-2.0", False),
    "mozilla public license 2.0 (mpl-2.0)": ("MPL-2.0", False),
    "gnu lesser general public license v3 (lgplv3)": ("LGPL-3.0-only", False),
    "gnu lesser general public license v3 or later (lgplv3+)": ("LGPL-3.0-or-later", False),
    "gnu general public license v2 (gplv2)": ("GPL-2.0-only", False),
    "gnu general public license v2 or later (gplv2+)": ("GPL-2.0-or-later", False),
    "gnu general public license v3 (gplv3)": ("GPL-3.0-only", False),
    "gnu general public license v3 or later (gplv3+)": ("GPL-3.0-or-later", False),
    "gnu affero general public license v3": ("AGPL-3.0-only", False),
}


@dataclass(frozen=True)
class Leaf:
    id: str
    forbidden: bool = False


@dataclass(frozen=True)
class Op:
    kind: str  # "AND" ou "OR"
    items: tuple[Node, ...]


Node = Leaf | Op


@dataclass(frozen=True)
class Norm:
    tree: Node | None
    generic: bool = False


def _known_ids(policy: Policy) -> dict[str, str]:
    return {i.lower(): i for i in policy.allowed | policy.exception_only}


def _collapse(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip()).lower()


def _resolve_leaf(token: str, policy: Policy) -> tuple[Leaf, bool] | None:
    key = _collapse(token)
    legacy = _LEGACY.get(key)
    if legacy:
        spdx, generic = legacy
        return Leaf(spdx, bool(_FORBIDDEN_RE.search(spdx))), generic
    known = _known_ids(policy).get(key)
    if known:
        return Leaf(known), False
    if _FORBIDDEN_RE.search(token):
        return Leaf(token.strip(), True), False
    return None


def _tokenize(text: str) -> list[str]:
    return re.findall(r"\(|\)|[^\s()]+", text)


class _Parser:
    def __init__(self, tokens: list[str], policy: Policy) -> None:
        self.tokens = tokens
        self.pos = 0
        self.policy = policy
        self.generic = False

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def parse(self) -> Node | None:
        node = self._or()
        if node is None or self.pos != len(self.tokens):
            return None
        return node

    def _or(self) -> Node | None:
        items = []
        first = self._and()
        if first is None:
            return None
        items.append(first)
        while (self._peek() or "").upper() == "OR":
            self.pos += 1
            nxt = self._and()
            if nxt is None:
                return None
            items.append(nxt)
        return items[0] if len(items) == 1 else Op("OR", tuple(items))

    def _and(self) -> Node | None:
        items = []
        first = self._atom()
        if first is None:
            return None
        items.append(first)
        while (self._peek() or "").upper() == "AND":
            self.pos += 1
            nxt = self._atom()
            if nxt is None:
                return None
            items.append(nxt)
        return items[0] if len(items) == 1 else Op("AND", tuple(items))

    def _atom(self) -> Node | None:
        tok = self._peek()
        if tok is None:
            return None
        self.pos += 1
        if tok == "(":
            node = self._or()
            if node is None or self._peek() != ")":
                return None
            self.pos += 1
            return node
        if tok == ")" or tok.upper() in {"AND", "OR", "WITH"}:
            return None
        resolved = _resolve_leaf(tok, self.policy)
        if resolved is None:
            return None
        leaf, generic = resolved
        self.generic = self.generic or generic
        return leaf


def normalize(text: str | None, policy: Policy = DEFAULT_POLICY) -> Norm:
    """Converte uma string de licença (SPDX, legada ou classifier) em árvore; falha fechada."""
    if text is None:
        return Norm(None)
    stripped = text.strip()
    if stripped.lower() in _EMPTY or "\n" in stripped or len(stripped) >= _MAX_FIELD_LEN:
        return Norm(None)
    whole = _resolve_leaf(stripped, policy)
    if whole is not None and _collapse(stripped) in {*_LEGACY, *_known_ids(policy)}:
        return Norm(whole[0], whole[1])
    parser = _Parser(_tokenize(stripped), policy)
    tree = parser.parse()
    if tree is None:
        # Texto livre sobre copyleft/proprietária vira PROIBIDA, não "desconhecida".
        if _FORBIDDEN_RE.search(stripped):
            return Norm(Leaf(stripped, True))
        return Norm(None)
    return Norm(tree, parser.generic)


def normalize_classifier(classifier: str, policy: Policy = DEFAULT_POLICY) -> Norm | None:
    """Normaliza um classifier ``License :: ...``; None se não for de licença."""
    if not classifier.startswith("License ::"):
        return None
    tail = classifier[len("License ::") :].strip()
    if tail.startswith("OSI Approved ::"):
        tail = tail[len("OSI Approved ::") :].strip()
    return normalize(tail, policy)


def leaves(node: Node) -> list[Leaf]:
    if isinstance(node, Leaf):
        return [node]
    return [leaf for item in node.items for leaf in leaves(item)]


def canonical(node: Node) -> str:
    """Texto canônico (operandos ordenados) para comparar com o registro."""
    if isinstance(node, Leaf):
        return node.id
    inner = f" {node.kind} ".join(sorted(canonical(i) for i in node.items))
    return f"({inner})"


def id_set(node: Node) -> frozenset[str]:
    return frozenset(leaf.id for leaf in leaves(node))


def evaluate(node: Node, ok: Callable[[Leaf], bool]) -> bool:
    if isinstance(node, Leaf):
        return ok(node)
    results = (evaluate(i, ok) for i in node.items)
    return any(results) if node.kind == "OR" else all(results)


def render(node: Node) -> str:
    """Texto legível (SPDX) de uma árvore, para o registro gerado."""
    if isinstance(node, Leaf):
        return node.id
    parts = [f"({render(i)})" if isinstance(i, Op) else render(i) for i in node.items]
    return f" {node.kind} ".join(parts)


# ---------------------------------------------------------------- dados injetáveis


@dataclass(frozen=True)
class Component:
    ecosystem: str
    name: str
    version: str
    group: str | None
    expression: str | None = None
    license_field: str | None = None
    classifiers: tuple[str, ...] = ()
    references: tuple[str, ...] = ()
    required_by: tuple[str, ...] = ()
    direct: bool = False
    needs_registry: bool = True
    installed: bool = True


@dataclass(frozen=True)
class Violation:
    code: str
    ecosystem: str
    group: str
    package: str
    version: str
    message: str

    def sort_key(self) -> tuple[int, str, str, str, str, str]:
        eco = ECOSYSTEMS.index(self.ecosystem) if self.ecosystem in ECOSYSTEMS else len(ECOSYSTEMS)
        return (eco, self.group, self.package, self.code, self.version, self.message)


@dataclass(frozen=True)
class RegEntry:
    name: str
    spdx: str
    group: str | None = None


Registry = Mapping[tuple[str, str], RegEntry]


@dataclass(frozen=True)
class Snapshot:
    components: Sequence[Component]
    issues: Sequence[Violation] = ()


@dataclass(frozen=True)
class Report:
    violations: tuple[Violation, ...]
    notes: tuple[str, ...]
    counts: Mapping[tuple[str, str], int]
    used_exceptions: tuple[str, ...]
    unused_exceptions: tuple[str, ...]

    @property
    def ok(self) -> bool:
        return not self.violations


def pep503(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def registry_key(ecosystem: str, name: str) -> tuple[str, str]:
    # Python usa PEP 503 (separadores equivalentes). npm só ignora caixa e espaços: "a-b", "a_b" e
    # "a.b" são pacotes distintos no npm e não podem colidir entre si nem com um pacote Python.
    if ecosystem == "python":
        return ("pkg", pep503(name))
    return (ecosystem, name.strip().lower())


# ---------------------------------------------------------------- resolução por componente


@dataclass(frozen=True)
class Resolution:
    tree: Node | None
    status: str  # "ok", AUSENTE ou DESCONHECIDA
    generic: bool
    by_expression: bool


def resolve(c: Component, policy: Policy = DEFAULT_POLICY) -> Resolution:
    """Ordem: ``License-Expression`` decide sozinha; senão ``License`` somada aos classifiers."""
    if c.expression is not None and c.expression.strip():
        n = normalize(c.expression, policy)
        return Resolution(n.tree, "ok" if n.tree else DESCONHECIDA, n.generic, True)
    trees: list[Node] = []
    generic = False
    any_source = False
    if c.license_field is not None and c.license_field.strip():
        any_source = c.license_field.strip().lower() not in _EMPTY or any_source
        n = normalize(c.license_field, policy)
        if n.tree is not None:
            trees.append(n.tree)
            generic = generic or n.generic
    for cl in c.classifiers:
        n2 = normalize_classifier(cl, policy)
        if n2 is None:
            continue
        any_source = True
        if n2.tree is not None:
            trees.append(n2.tree)
            generic = generic or n2.generic
    if trees:
        tree = trees[0] if len(trees) == 1 else Op("AND", tuple(trees))
        return Resolution(tree, "ok", generic, False)
    return Resolution(None, DESCONHECIDA if any_source else AUSENTE, False, False)


def declared_strings(c: Component) -> list[str]:
    out = [s for s in (c.expression, c.license_field) if s and s.strip()]
    for cl in c.classifiers:
        if cl.startswith("License ::"):
            out.append(cl.split("::")[-1].strip())
    return out


# ---------------------------------------------------------------- exceções


_REQUIRED = (
    "ecossistema",
    "pacote",
    "versoes",
    "grupo",
    "licenca_declarada",
    "licenca_verificada",
    "evidencia",
    "justificativa",
    "aprovador",
    "data",
    "adr",
)


@dataclass(frozen=True)
class ValidException:
    ecosystem: str
    package: str
    versions: str
    group: str
    declared: str
    verified: str
    label: str


def _version_tuple(v: str) -> tuple[int, ...] | None:
    m = re.match(r"^v?(\d+(?:\.\d+)*)", v.strip())
    return tuple(int(p) for p in m.group(1).split(".")) if m else None


def _npm_clauses(spec: str) -> list[tuple[str, tuple[int, ...]]] | None:
    clauses = []
    for part in re.split(r"[,\s]+", spec.strip()):
        if not part:
            continue
        m = re.match(r"^(==|>=|<=|>|<|=)?\s*v?(\d+(?:\.\d+)*)$", part)
        if not m:
            return None
        clauses.append((m.group(1) or "==", tuple(int(p) for p in m.group(2).split("."))))
    return clauses or None


def _pad(a: tuple[int, ...], b: tuple[int, ...]) -> tuple[tuple[int, ...], tuple[int, ...]]:
    n = max(len(a), len(b))
    return a + (0,) * (n - len(a)), b + (0,) * (n - len(b))


def specifier_valid(ecosystem: str, spec: str) -> bool:
    spec = spec.strip()
    if not spec or "*" in spec:
        return False
    if ecosystem == "npm":
        return _npm_clauses(spec) is not None
    from packaging.specifiers import InvalidSpecifier, SpecifierSet

    try:
        SpecifierSet(spec)
    except InvalidSpecifier:
        return False
    return True


def version_matches(ecosystem: str, spec: str, version: str) -> bool:
    if ecosystem == "npm":
        clauses = _npm_clauses(spec)
        current = _version_tuple(version)
        if clauses is None or current is None:
            return False
        for op, want in clauses:
            a, b = _pad(current, want)
            ok = {
                "==": a == b,
                "=": a == b,
                ">=": a >= b,
                "<=": a <= b,
                ">": a > b,
                "<": a < b,
            }[op]
            if not ok:
                return False
        return True
    from packaging.specifiers import InvalidSpecifier, SpecifierSet
    from packaging.version import InvalidVersion

    try:
        return SpecifierSet(spec).contains(version, prereleases=True)
    except (InvalidSpecifier, InvalidVersion):
        return False


def _validate_exception(
    raw: Mapping[str, object], today: date, policy: Policy
) -> tuple[ValidException | None, str, str, str]:
    """Devolve (exceção válida ou None, ecossistema, pacote, erro). Erro vazio = válida."""
    eco = str(raw.get("ecossistema") or "")
    pkg = str(raw.get("pacote") or "?")
    problems: list[str] = []
    for key in _REQUIRED:
        val = raw.get(key)
        if not isinstance(val, str) or not val.strip():
            problems.append(f"campo obrigatório ausente ou vazio: {key}")
    if problems:
        return None, eco, pkg, "; ".join(problems)
    s = {k: str(raw[k]).strip() for k in _REQUIRED}
    if s["ecossistema"] not in ("python", "npm"):
        problems.append("ecossistema deve ser python ou npm")
    if s["grupo"] not in GROUPS:
        problems.append("grupo deve ser runtime ou dev (nunca ambos)")
    try:
        when = date.fromisoformat(s["data"])
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", s["data"]):
            problems.append("data deve ser AAAA-MM-DD")
        elif when > today:
            problems.append("data no futuro")
    except ValueError:
        problems.append("data inválida (use AAAA-MM-DD)")
    if s["ecossistema"] in ("python", "npm") and not specifier_valid(
        s["ecossistema"], s["versoes"]
    ):
        problems.append("versoes inválido ou amplo demais (nunca '*')")
    norm = normalize(s["licenca_verificada"], policy)
    if norm.tree is None or not isinstance(norm.tree, Leaf):
        problems.append("licenca_verificada deve ser um único id SPDX permitido ou MPL-2.0")
    elif norm.tree.id not in policy.allowed | policy.exception_only:
        problems.append("licenca_verificada fora da lista permitida")
    if not s["evidencia"].startswith("https://"):
        problems.append("evidencia deve ser uma URL https")
    if problems:
        return None, s["ecossistema"], s["pacote"], "; ".join(problems)
    label = f"{s['pacote']} {s['versoes']} ({s['ecossistema']}/{s['grupo']})"
    verified = norm.tree.id if isinstance(norm.tree, Leaf) else s["licenca_verificada"]
    return (
        ValidException(
            s["ecossistema"],
            pep503(s["pacote"]),
            s["versoes"],
            s["grupo"],
            s["licenca_declarada"],
            verified,
            label,
        ),
        s["ecossistema"],
        s["pacote"],
        "",
    )


def _declared_matches(exc: ValidException, c: Component) -> bool:
    if exc.declared.strip().upper() == "AUSENTE":
        return not declared_strings(c)
    want = exc.declared.strip().lower()
    return any(want == d.strip().lower() for d in declared_strings(c))


def validate_exceptions(
    exceptions: Sequence[Mapping[str, object]], today: date, policy: Policy
) -> tuple[list[ValidException], list[Violation]]:
    """Separa as exceções válidas (únicas) das inválidas, que viram ``EXCECAO_INVALIDA``."""
    valid: list[ValidException] = []
    problems: list[Violation] = []
    seen: set[tuple[str, str, str, str]] = set()
    for raw in exceptions:
        exc, eco, pkg, error = _validate_exception(raw, today, policy)
        if exc is None:
            problems.append(
                Violation(
                    EXCECAO_INVALIDA,
                    eco,
                    str(raw.get("grupo") or ""),
                    pkg,
                    "",
                    f"Exceção inválida para {pkg}: {error}.",
                )
            )
            continue
        key = (exc.ecosystem, exc.package, exc.group, exc.versions)
        if key in seen:
            problems.append(
                Violation(
                    EXCECAO_INVALIDA,
                    exc.ecosystem,
                    exc.group,
                    exc.package,
                    "",
                    f"Exceção duplicada para {exc.package} ({exc.versions}, {exc.group}).",
                )
            )
            continue
        seen.add(key)
        valid.append(exc)
    return valid, problems


def vendor_hit(c: Component) -> bool:
    """Ultralytics: nunca suprimível por exceção."""
    name_n = pep503(c.name.split("/")[-1] if c.ecosystem == "action" else c.name)
    return (
        name_n.startswith(_FORBIDDEN_VENDOR)
        or _mentions_vendor(c.name)
        or any(_mentions_vendor(r) for r in c.references)
    )


def license_fails(c: Component, res: Resolution, policy: Policy) -> bool:
    """True se a licença resolvida não passa sozinha (sem exceção)."""
    if not c.installed or res.tree is None:
        return True
    return not evaluate(res.tree, lambda lf: lf.id in policy.allowed)


def find_exception(
    valid: Sequence[ValidException], c: Component, res: Resolution
) -> ValidException | None:
    """Exceção válida que cobre o componente; nunca para licença proibida nem Ultralytics."""
    if c.group not in GROUPS or vendor_hit(c):
        return None
    if res.tree is not None and any(lf.forbidden for lf in leaves(res.tree)):
        return None
    for exc in valid:
        if (
            exc.ecosystem == c.ecosystem
            and exc.package == pep503(c.name)
            and exc.group == c.group
            and version_matches(c.ecosystem, exc.versions, c.version)
            and _declared_matches(exc, c)
        ):
            return exc
    return None


# ---------------------------------------------------------------- verify


def _mentions_vendor(text: str) -> bool:
    return _FORBIDDEN_VENDOR in text.lower()


def verify(
    snapshot: Snapshot,
    policy: Policy,
    registry: Registry,
    exceptions: Sequence[Mapping[str, object]],
    *,
    today: date,
) -> Report:
    """Função pura: mesmos dados, mesmo relatório."""
    violations: list[Violation] = list(snapshot.issues)
    notes: list[str] = []
    counts: dict[tuple[str, str], int] = {(e, g): 0 for e in ECOSYSTEMS for g in GROUPS}

    valid, exc_violations = validate_exceptions(exceptions, today, policy)
    violations.extend(exc_violations)

    used: set[str] = set()
    covering: dict[int, ValidException] = {}

    for reg_entry in registry.values():
        norm = normalize(reg_entry.spdx, policy)
        eco = "registro"
        grp = reg_entry.group or ""
        if norm.tree is None:
            violations.append(
                Violation(
                    DESCONHECIDA,
                    eco,
                    grp,
                    reg_entry.name,
                    "",
                    f"Registro de {reg_entry.name} com licença inválida ou desconhecida: "
                    f"'{reg_entry.spdx}'.",
                )
            )
        elif any(leaf.forbidden for leaf in leaves(norm.tree)) and not evaluate(
            norm.tree, lambda lf: not lf.forbidden
        ):
            violations.append(
                Violation(
                    PROIBIDA,
                    eco,
                    grp,
                    reg_entry.name,
                    "",
                    f"Registro de {reg_entry.name} com licença proibida: '{reg_entry.spdx}'.",
                )
            )
        elif not evaluate(norm.tree, lambda lf: lf.id in policy.allowed | policy.exception_only):
            violations.append(
                Violation(
                    DESCONHECIDA,
                    eco,
                    grp,
                    reg_entry.name,
                    "",
                    f"Registro de {reg_entry.name} com licença fora da lista permitida: "
                    f"'{reg_entry.spdx}'.",
                )
            )

    for c in snapshot.components:
        group = c.group if c.group in GROUPS else None
        g = c.group if isinstance(c.group, str) else ""

        def add(code: str, msg: str, c: Component = c, g: str = g) -> None:
            violations.append(Violation(code, c.ecosystem, g, c.name, c.version, msg))

        if group is None:
            add(GRUPO_INVALIDO, f"{c.name}: grupo inválido ({c.group!r}); use runtime ou dev.")
        else:
            counts[(c.ecosystem, group)] = counts.get((c.ecosystem, group), 0) + 1

        is_vendor = vendor_hit(c)
        if is_vendor:
            add(
                ULTRALYTICS,
                f"{c.name}: ligado à Ultralytics (AGPL-3.0), proibido; exceção não se aplica.",
            )

        res = resolve(c, policy)
        if res.generic:
            notes.append(f"{c.ecosystem}/{g} {c.name} {c.version}: licença ({GENERIC_MARK}).")
        fail_code = ""
        fail_msg = ""
        if not c.installed:
            fail_code, fail_msg = (
                AUSENTE,
                f"{c.name}: exigido mas não instalado; licença não verificável.",
            )
        elif res.tree is None:
            fail_code = res.status
            fail_msg = (
                f"{c.name}: licença ausente (nenhum metadado de licença)."
                if res.status == AUSENTE
                else f"{c.name}: licença desconhecida ou ilegível."
            )
        elif not evaluate(res.tree, lambda lf: lf.id in policy.allowed):
            has_forbidden = any(lf.forbidden for lf in leaves(res.tree))
            only_exc = not has_forbidden and evaluate(
                res.tree, lambda lf: lf.id in policy.allowed | policy.exception_only
            )
            fail_code = PROIBIDA if has_forbidden or only_exc else DESCONHECIDA
            fail_msg = f"{c.name}: licença {canonical(res.tree)} " + (
                "proibida."
                if has_forbidden
                else "exige exceção registrada."
                if only_exc
                else "fora da lista permitida."
            )
        if fail_code:
            match = find_exception(valid, c, res)
            if match is not None:
                used.add(match.label)
                covering[id(c)] = match
            else:
                add(fail_code, fail_msg)

        if c.needs_registry:
            entry = registry.get(registry_key(c.ecosystem, c.name))
            if entry is None:
                add(NAO_REGISTRADO, f"{c.name}: não está registrado na documentação de licenças.")
            else:
                reg = normalize(entry.spdx, policy)
                covered = covering.get(id(c))
                if covered is not None and reg.tree is not None:
                    # Sob exceção, o registro vale contra a licenca_verificada (também nas diretas).
                    if canonical(reg.tree) != covered.verified:
                        add(
                            DIVERGE_DO_REGISTRO,
                            f"{c.name}: registro ({entry.spdx}) difere da licenca_verificada da "
                            f"exceção ({covered.verified}).",
                        )
                elif res.tree is not None and reg.tree is not None:
                    same = (
                        canonical(res.tree) == canonical(reg.tree)
                        if res.by_expression
                        else id_set(res.tree) == id_set(reg.tree)
                    )
                    if not same:
                        add(
                            DIVERGE_DO_REGISTRO,
                            f"{c.name}: licença dos metadados ({canonical(res.tree)}) difere do "
                            f"registro ({entry.spdx}).",
                        )
                if c.group == "runtime" and entry.group == "dev":
                    add(
                        DIVERGE_DO_REGISTRO,
                        f"{c.name}: é dependência de runtime mas o registro a lista como dev.",
                    )

    unused = tuple(sorted(e.label for e in valid if e.label not in used))
    return Report(
        violations=tuple(sorted(violations, key=Violation.sort_key)),
        notes=tuple(sorted(set(notes))),
        counts=counts,
        used_exceptions=tuple(sorted(used)),
        unused_exceptions=unused,
    )

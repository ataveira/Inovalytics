"""Parser puro do corpo de importação Pascal VOC (F1-08b). Só biblioteca padrão, sem I/O.

Pascal VOC é o nome público de um formato: um XML por imagem com `<object><name>` e
`<bndbox>` em pixels. Escrito a partir dessa descrição pública; nada é lido de disco e nenhuma
chave do corpo é usada como caminho. O XML passa pelo leitor seguro (`xml_reader`); este módulo só
dá a semântica.

Contrato: `parse_voc(bytes, VocLimits) -> ParsedVoc` é determinística. Defeitos do corpo e limites
levantam `ImportRejectedError`; defeitos por item levantam também, salvo com `collect_errors=True`
(vão para `errors`, para o serviço juntá-los aos de casamento). Nenhuma outra exceção escapa. Os
limites são conferidos antes do laço de parse: bytes do corpo antes de `json.loads`, quantidade de
arquivos, bytes por arquivo, `<` totais e `<object` totais (varreduras em C).

A conversão para coordenadas normalizadas (`convert_box`) precisa das dimensões da imagem gravada
e por isso é chamada pelo serviço; ela mora aqui para ser testada junto da convenção de pixels:
índices 1-based e inclusivos do devkit (`x0 = max(xmin - 1, 0)`, `x1 = xmax`).
"""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from typing import Any, NamedTuple

from inovalytics.imports.common import (
    bad_constant,
    check_class_name,
    depth_exceeded,
    pairs_hook,
    parse_float,
    parse_int,
    representable,
    shown,
    to_normalized,
)
from inovalytics.imports.errors import ErrorCollector, ImportRejectedError, ItemError
from inovalytics.imports.xml_reader import XmlDoc, XmlLimits, XmlRejectedError, read_xml

__all__ = [
    "ParsedVoc",
    "VocBox",
    "VocFile",
    "VocLimits",
    "convert_box",
    "parse_voc",
]

K_FILE = "arquivo"
K_ANNOTATION = "anotacao"  # defeito do arquivo XML (index = posição na ordem de chave)
K_OBJECT = "objeto"  # defeito de um <object> (index = posição no arquivo; group = arquivo)
MAX_DEPTH = 2
MAX_KEY_CHARS = 1024
MAX_NUMBER_CHARS = 32
MAX_SIZE_DIGITS = 9
TOL = 1e-6
SUFFIX = ".xml"
_NUMBER_CHARS = frozenset("0123456789+-.eE")
_CONTROL = frozenset({"Cc", "Cs", "Zl", "Zp"})
_OBJECT_MARK = "<object"


@dataclass(frozen=True)
class VocLimits:
    max_json_bytes: int = 16 * 1024 * 1024
    max_images: int = 50_000  # arquivos XML
    max_annotations: int = 300_000  # objetos
    max_categories: int = 1_000
    max_errors: int = 100
    max_xml_bytes: int = 256 * 1024
    # Orçamento de '<' entre todos os arquivos. Medido (F1-08b): ~1,5 a 2,6 us por '<' em Python
    # 3.13 / expat 2.9.0; 600 000 mantém o pior caso legítimo perto de 1,5 s. Reconfirmar na F1-08d.
    max_total_markup: int = 600_000
    max_depth: int = 16
    max_elements: int = 20_000
    max_attributes: int = 32
    max_text_chars: int = 1024

    def xml_limits(self) -> XmlLimits:
        return XmlLimits(
            max_bytes=self.max_xml_bytes,
            max_depth=self.max_depth,
            max_elements=self.max_elements,
            max_attributes=self.max_attributes,
            max_text_chars=self.max_text_chars,
        )


class VocBox(NamedTuple):
    """Caixa em pixels como no arquivo (índices 1-based inclusivos), do objeto `index`."""

    file_index: int
    obj_index: int  # posição do <object> no arquivo
    line: int
    class_id: int  # índice em `ParsedVoc.names`
    xmin: float
    ymin: float
    xmax: float
    ymax: float


@dataclass(frozen=True, slots=True)
class VocFile:
    index: int  # posição na ordem de chave ordenada
    key: str  # chave do corpo (até 1024 caracteres, sem controles); nunca é caminho
    base_name: str  # último componente da chave sem ".xml" (nada toca disco)
    declared_filename: str | None  # <filename> direto de <annotation>, ou None
    declared_size: tuple[int, int] | None  # <size> (largura, altura) positivos, ou None
    size_line: int | None
    box_count: int

    @property
    def name(self) -> str:
        """Chave exibível (sem controles, até 200 caracteres); calculada só quando pedida."""
        return shown(self.key)


@dataclass(frozen=True)
class ParsedVoc:
    names: tuple[str, ...]  # classes na ordem da primeira aparição
    files: tuple[VocFile, ...]
    boxes: tuple[VocBox, ...]  # ordem (arquivo, documento)
    ignored_difficult: int = 0
    ignored_truncated: int = 0
    ignored_occluded: int = 0
    ignored_pose: int = 0
    ignored_parts: int = 0
    without_size: int = 0
    origin_zero_adjustments: int = 0
    # Só com `collect_errors=True`; vazio quando o parser já levantou.
    errors: tuple[ItemError, ...] = ()
    errors_total: int = 0

    @property
    def files_count(self) -> int:
        return len(self.files)

    @property
    def boxes_count(self) -> int:
        return len(self.boxes)


def _fail(code: str, message: str) -> ImportRejectedError:
    return ImportRejectedError([ItemError(code, K_FILE, None, None, message)], 1)


def _limit(name: str) -> ImportRejectedError:
    return _fail("limite_excedido", f"limite excedido: {name}")


def _load(data: bytes, lim: VocLimits) -> dict[str, Any]:
    if len(data) > lim.max_json_bytes:
        raise _limit("max_json_bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _fail("json_invalido", "o corpo não é UTF-8 válido") from None
    try:
        obj = json.loads(
            text,
            object_pairs_hook=pairs_hook,
            parse_constant=bad_constant,
            parse_int=parse_int,
            parse_float=parse_float,
        )
    except RecursionError:
        raise _limit("max_depth") from None
    except (ValueError, MemoryError):
        raise _fail("json_invalido", "JSON inválido") from None
    if depth_exceeded(obj, MAX_DEPTH):
        raise _limit("max_depth")
    if not isinstance(obj, dict):
        raise _fail("json_invalido", "a raiz do JSON deve ser um objeto")
    return obj


def _key_problem(key: str) -> tuple[str, str] | None:
    """(código, mensagem) se a chave do arquivo é inaceitável; o tamanho vem antes de tudo."""
    if not key or len(key) > MAX_KEY_CHARS:
        return "rotulo_nome_invalido", "nome do arquivo XML vazio ou maior que 1024"
    if not key.isprintable() and any(unicodedata.category(c) in _CONTROL for c in key):
        return "rotulo_nome_invalido", "nome do arquivo XML com caractere de controle"
    if not key.endswith(SUFFIX):
        return "rotulo_extensao_invalida", "o arquivo de anotação deve terminar em '.xml'"
    return None


def _base_name(key: str) -> str:
    return key.replace("\\", "/").rsplit("/", 1)[-1][: -len(SUFFIX)]


def _number(tok: str) -> float | None:
    if len(tok) > MAX_NUMBER_CHARS or not _NUMBER_CHARS.issuperset(tok):
        return None
    try:
        v = float(tok)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def convert_box(
    xmin: float, ymin: float, xmax: float, ymax: float, width: int, height: int
) -> tuple[tuple[float, float, float, float], bool] | None:
    """Pixels VOC (1-based inclusivos) -> `((cx, cy, w, h), fora_da_imagem)` normalizados com as
    dimensões da imagem GRAVADA. `None` se o esquema não guarda a caixa (centro fora de [0, 1] ou
    maior que a imagem). `fora_da_imagem` indica que passa da borda e é gravada sem recorte."""
    x0 = max(xmin - 1.0, 0.0)
    y0 = max(ymin - 1.0, 0.0)
    w = xmax - x0
    h = ymax - y0
    if not representable(x0, y0, w, h, width, height):
        return None
    outside = xmax > width + TOL or ymax > height + TOL
    return to_normalized(x0, y0, w, h, width, height), outside


class _Shown:
    """Nome exibível calculado sob demanda e uma vez por arquivo (coletor cheio não paga)."""

    __slots__ = ("_key", "_val")

    def __init__(self, key: str) -> None:
        self._key = key
        self._val: str | None = None

    def __call__(self) -> str:
        if self._val is None:
            self._val = shown(self._key)
        return self._val


def parse_voc(data: bytes, limits: VocLimits, *, collect_errors: bool = False) -> ParsedVoc:
    """Valida o corpo JSON e extrai classes, arquivos e caixas em pixels."""
    try:
        return _parse(data, limits, collect_errors)
    except ImportRejectedError:
        raise
    except Exception:  # defesa final: nenhuma outra exceção escapa
        raise _fail("json_invalido", "o corpo não pôde ser interpretado") from None


class _State:
    def __init__(self, lim: VocLimits, col: ErrorCollector) -> None:
        self.lim = lim
        self.col = col
        self.names: list[str] = []
        self.class_ids: dict[str, int] = {}
        self.boxes: list[VocBox] = []
        self.difficult = self.truncated = self.occluded = self.pose = self.parts = 0
        self.without_size = 0
        self.origin_zero = 0


def _parse(data: bytes, lim: VocLimits, collect_errors: bool) -> ParsedVoc:
    obj = _load(data, lim)
    annotations = obj.get("annotations")
    if not isinstance(annotations, dict):
        raise _fail("estrutura_invalida", "'annotations' ausente ou não é um objeto")
    if len(annotations) > lim.max_images:
        raise _limit("max_images")

    col = ErrorCollector(lim.max_errors)
    keys = sorted(annotations)
    # Tudo o que é limite sai antes do laço de parse (varreduras em C, sem split nem parse).
    markup_total = 0
    object_total = 0
    for fi, key in enumerate(keys):
        text = annotations[key]
        if not isinstance(text, str):
            col.add("rotulo_invalido", K_ANNOTATION, None, fi, "o conteúdo do XML deve ser texto",
                    file=_Shown(key))  # fmt: skip
            continue
        if len(text) > lim.max_xml_bytes:
            raise _limit("max_xml_bytes")
        markup_total += text.count("<")
        object_total += text.count(_OBJECT_MARK)
    if markup_total > lim.max_total_markup:
        raise _limit("max_total_markup")
    if object_total > lim.max_annotations:
        raise _limit("max_annotations")

    st = _State(lim, col)
    xml_lim = lim.xml_limits()
    files: list[VocFile] = []
    for fi, key in enumerate(keys):
        text = annotations[key]
        if not isinstance(text, str):
            continue
        problem = _key_problem(key)
        label = _Shown(key)
        if problem is None and not _base_name(key).strip():
            problem = ("rotulo_nome_invalido", "nome do arquivo XML vazio")
        if problem is not None:
            col.add(problem[0], K_ANNOTATION, None, fi, problem[1], file=label)
            continue
        try:
            doc = read_xml(text, xml_lim)
        except XmlRejectedError as e:
            col.add(e.code, K_ANNOTATION, None, fi, e.message, file=label, line=e.line)
            continue
        files.append(_read_file(st, doc, fi, key, label))
    if not collect_errors:
        col.raise_if_any(ImportRejectedError)
    return ParsedVoc(
        tuple(st.names),
        tuple(files),
        tuple(st.boxes),
        st.difficult,
        st.truncated,
        st.occluded,
        st.pose,
        st.parts,
        st.without_size,
        st.origin_zero,
        tuple(col.items()),
        col.total,
    )


def _children(doc: XmlDoc) -> dict[int, list[int]]:
    kids: dict[int, list[int]] = {}
    for i, node in enumerate(doc.nodes):
        if node.parent >= 0:
            kids.setdefault(node.parent, []).append(i)
    return kids


def _group(doc: XmlDoc, kids: dict[int, list[int]], parent: int) -> dict[str, list[int]]:
    by: dict[str, list[int]] = {}
    for c in kids.get(parent, ()):
        by.setdefault(doc.nodes[c].name, []).append(c)
    return by


def _read_file(st: _State, doc: XmlDoc, fi: int, key: str, label: _Shown) -> VocFile:
    col = st.col
    nodes = doc.nodes
    kids = _children(doc)
    top = _group(doc, kids, 0)

    def file_err(code: str, msg: str, line: int) -> None:
        col.add(code, K_ANNOTATION, None, fi, msg, file=label, line=line)

    declared_filename: str | None = None
    fnames = top.get("filename", [])
    if len(fnames) > 1:
        file_err("elemento_duplicado", "mais de um <filename>", nodes[fnames[1]].line)
    elif fnames and nodes[fnames[0]].text:
        declared_filename = nodes[fnames[0]].text

    declared_size: tuple[int, int] | None = None
    size_line: int | None = None
    sizes = top.get("size", [])
    if not sizes:
        st.without_size += 1
    elif len(sizes) > 1:
        file_err("elemento_duplicado", "mais de um <size>", nodes[sizes[1]].line)
    else:
        size_line = nodes[sizes[0]].line
        declared_size = _read_size(doc, kids, sizes[0], file_err)

    before = len(st.boxes)
    for pos, oi in enumerate(top.get("object", [])):
        _read_object(st, doc, kids, oi, fi, pos, label)
        if len(st.boxes) > st.lim.max_annotations:
            raise _limit("max_annotations")
    return VocFile(
        fi, key, _base_name(key), declared_filename, declared_size, size_line,
        len(st.boxes) - before,
    )  # fmt: skip


def _read_size(
    doc: XmlDoc, kids: dict[int, list[int]], si: int, file_err: Any
) -> tuple[int, int] | None:
    nodes = doc.nodes
    by = _group(doc, kids, si)
    values: list[int] = []
    for tag in ("width", "height"):
        found = by.get(tag, [])
        if len(found) != 1:
            code = "elemento_duplicado" if found else "size_invalido"
            line = nodes[found[1]].line if found else nodes[si].line
            file_err(code, f"<size> sem um único <{tag}>", line)
            return None
        n = nodes[found[0]]
        t = n.text
        if not (t.isascii() and t.isdigit()) or len(t) > MAX_SIZE_DIGITS:
            file_err("size_invalido", f"<{tag}> do <size> deve ser inteiro positivo", n.line)
            return None
        v = int(t)
        if v == 0:
            file_err("tamanho_divergente", f"<{tag}> do <size> é zero", n.line)
            return None
        values.append(v)
    return values[0], values[1]


def _flag(doc: XmlDoc, by: dict[str, list[int]], tag: str) -> str | None:
    found = by.get(tag)
    return doc.nodes[found[0]].text if found else None


def _read_object(
    st: _State, doc: XmlDoc, kids: dict[int, list[int]], oi: int, fi: int, pos: int, label: _Shown
) -> None:
    col = st.col
    nodes = doc.nodes
    by = _group(doc, kids, oi)

    def err(code: str, msg: str, line: int) -> None:
        col.add(code, K_OBJECT, None, pos, msg, file=label, line=line, group=fi)

    ok = True
    name = ""
    found = by.get("name", [])
    if not found or (len(found) == 1 and not nodes[found[0]].text):
        err("nome_ausente", "objeto sem <name>", nodes[oi].line)
        ok = False
    elif len(found) > 1:
        err("elemento_duplicado", "mais de um <name> no objeto", nodes[found[1]].line)
        ok = False
    else:
        name = nodes[found[0]].text
        if not check_class_name(name):
            err("classe_invalida", "nome de classe inválido", nodes[found[0]].line)
            ok = False

    box = _read_bndbox(doc, kids, by, oi, err)
    if box is None:
        ok = False

    # Ignorados são contados (nunca descartados em silêncio) mesmo quando o objeto tem defeito.
    if _flag(doc, by, "difficult") == "1":
        st.difficult += 1
    if _flag(doc, by, "truncated") == "1":
        st.truncated += 1
    if _flag(doc, by, "occluded") == "1":
        st.occluded += 1
    pose = _flag(doc, by, "pose")
    if pose and pose != "Unspecified":
        st.pose += 1
    st.parts += len(by.get("part", ()))

    if not ok or box is None:
        return
    cid = st.class_ids.get(name)
    if cid is None:
        if len(st.names) >= st.lim.max_categories:
            raise _limit("max_categories")
        cid = len(st.names)
        st.class_ids[name] = cid
        st.names.append(name)
    if box[0] < 1.0 or box[1] < 1.0:
        st.origin_zero += 1
    st.boxes.append(VocBox(fi, pos, nodes[oi].line, cid, *box))


def _read_bndbox(
    doc: XmlDoc,
    kids: dict[int, list[int]],
    by: dict[str, list[int]],
    oi: int,
    err: Any,
) -> tuple[float, float, float, float] | None:
    nodes = doc.nodes
    found = by.get("bndbox", [])
    if not found:
        err("bndbox_ausente", "objeto sem <bndbox>", nodes[oi].line)
        return None
    if len(found) > 1:
        err("elemento_duplicado", "mais de um <bndbox> no objeto", nodes[found[1]].line)
        return None
    bi = found[0]
    coords = _group(doc, kids, bi)
    vals: dict[str, float] = {}
    for tag in ("xmin", "ymin", "xmax", "ymax"):
        c = coords.get(tag, [])
        if len(c) > 1:
            err("elemento_duplicado", f"mais de um <{tag}> no <bndbox>", nodes[c[1]].line)
        elif not c or not nodes[c[0]].text:
            err("coordenada_ausente", f"<{tag}> ausente ou vazio", nodes[c[0] if c else bi].line)
        else:
            v = _number(nodes[c[0]].text)
            if v is None:
                msg = f"<{tag}>: número inválido (use decimal finito de até 32 caracteres)"
                err("numero_invalido", msg, nodes[c[0]].line)
            else:
                vals[tag] = v
    if len(vals) < 4:
        return None
    xmin, ymin, xmax, ymax = vals["xmin"], vals["ymin"], vals["xmax"], vals["ymax"]
    if min(vals.values()) < 0 or xmax < xmin or ymax < ymin or xmax <= 0 or ymax <= 0:
        err("bbox_invalida", "<bndbox> com valor negativo, máximo menor que o mínimo ou vazio",
            nodes[bi].line)  # fmt: skip
        return None
    return xmin, ymin, xmax, ymax

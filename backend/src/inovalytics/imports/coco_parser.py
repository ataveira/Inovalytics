"""Parser puro de arquivos COCO "instances" (F1-06a). Só biblioteca padrão, sem I/O.

Escrito a partir da especificação pública do formato (campos `images`, `annotations` e
`categories`). Não acessa banco, rede nem disco; URLs do arquivo nunca são lidas como endereço.

Contrato: `parse_coco(bytes, CocoLimits) -> ParsedCoco` é determinística. Qualquer defeito levanta
`CocoImportError` com a lista de `ItemError` ordenada por `(kind, index)`; nenhuma outra exceção
escapa. Os limites são conferidos antes de construir estruturas de domínio. Não é streaming: o
limite de bytes é a defesa de memória.

A conversão para coordenadas normalizadas (`to_normalized`) mora aqui para que o parser (dimensões
declaradas) e o serviço (dimensões gravadas) usem exatamente a mesma aritmética.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from typing import Any

from inovalytics.imports.errors import CocoImportError, ErrorCollector, ItemError

MAX_NAME_CHARS = 1024
MAX_COORD = 1e9
MAX_INT_DIGITS = 4300
BORDER_EPS = 1e-9

_STRUCT_RE = re.compile(r'"[^"\\]*(?:\\.[^"\\]*)*"|[\[\]{}]', re.DOTALL)

K_FILE = "arquivo"
K_IMAGE = "imagem"
K_CATEGORY = "categoria"
K_ANNOTATION = "anotacao"


@dataclass(frozen=True)
class CocoLimits:
    max_json_bytes: int = 32 * 1024 * 1024
    max_images: int = 50_000
    max_annotations: int = 300_000
    max_categories: int = 1_000
    max_depth: int = 12
    max_errors: int = 100
    max_dimension: int = 16384


@dataclass(frozen=True, slots=True)
class CocoImage:
    id: int
    file_name: str
    width: int
    height: int
    index: int


@dataclass(frozen=True, slots=True)
class CocoCategory:
    id: int
    name: str  # já sem espaços nas pontas
    index: int


@dataclass(frozen=True, slots=True)
class CocoBox:
    """Caixa em pixels, canto superior esquerdo, como no arquivo."""

    annotation_id: int | None
    index: int
    image_id: int
    category_id: int
    x: float
    y: float
    w: float
    h: float


@dataclass(frozen=True, slots=True)
class CrowdItem:
    id: int | None
    index: int


@dataclass(frozen=True)
class ParsedCoco:
    images: tuple[CocoImage, ...]
    categories: tuple[CocoCategory, ...]
    boxes: tuple[CocoBox, ...]
    warnings: tuple[ItemError, ...]  # limitada a max_errors; o total real está abaixo
    warnings_total: int
    crowd_ignored: tuple[CrowdItem, ...]
    segmentations_ignored: int


def to_normalized(
    x: float, y: float, w: float, h: float, width: int, height: int
) -> tuple[float, float, float, float]:
    """`(x_center, y_center, width, height)` normalizados a partir de pixels e da imagem."""
    return ((x + w / 2) / width, (y + h / 2) / height, w / width, h / height)


def representable(x: float, y: float, w: float, h: float, width: int, height: int) -> bool:
    """A caixa cabe no esquema (centro em [0, 1], largura e altura em (0, 1])?"""
    xc, yc, nw, nh = to_normalized(x, y, w, h, width, height)
    return 0.0 <= xc <= 1.0 and 0.0 <= yc <= 1.0 and 0.0 < nw <= 1.0 and 0.0 < nh <= 1.0


def sticks_out(x: float, y: float, w: float, h: float, width: int, height: int) -> bool:
    """A caixa ultrapassa a borda da imagem (tolerância de 1e-9 no toque exato)?"""
    return (
        x < -BORDER_EPS
        or y < -BORDER_EPS
        or x + w > width + BORDER_EPS
        or y + h > height + BORDER_EPS
    )


def _fail(code: str, message: str) -> CocoImportError:
    return CocoImportError([ItemError(code, K_FILE, None, None, message)], 1)


def _is_int(v: Any) -> bool:
    return type(v) is int


def _is_num(v: Any) -> bool:
    return type(v) in (int, float)


def _pairs_hook(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in pairs:
        if k in out:
            raise ValueError("chave duplicada")
        out[k] = v
    return out


def _bad_constant(_: str) -> Any:
    raise ValueError("constante não finita")


def _parse_int(s: str) -> int:
    if len(s) > MAX_INT_DIGITS:
        raise ValueError("inteiro grande demais")
    return int(s)


def _parse_float(s: str) -> float:
    v = float(s)
    if not math.isfinite(v):
        raise ValueError("número não finito")
    return v


def _depth_exceeded(text: str, max_depth: int) -> bool:
    """Varredura linear de aninhamento fora de strings; para ao exceder `max_depth`."""
    depth = 0
    for m in _STRUCT_RE.finditer(text):
        c = m.group()
        if c in "[{":
            depth += 1
            if depth > max_depth:
                return True
        elif c in "]}":
            depth -= 1
    return False


def _load(data: bytes, lim: CocoLimits) -> dict[str, Any]:
    if len(data) > lim.max_json_bytes:
        raise _fail("limite_excedido", "limite excedido: max_json_bytes")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise _fail("json_invalido", "o arquivo não é UTF-8 válido") from None
    if _depth_exceeded(text, lim.max_depth):
        raise _fail("limite_excedido", "limite excedido: max_depth")
    try:
        obj = json.loads(
            text,
            object_pairs_hook=_pairs_hook,
            parse_constant=_bad_constant,
            parse_int=_parse_int,
            parse_float=_parse_float,
        )
    except (ValueError, RecursionError, MemoryError):
        raise _fail("json_invalido", "JSON inválido") from None
    if not isinstance(obj, dict):
        raise _fail("json_invalido", "a raiz do JSON deve ser um objeto")
    return obj


def _section(
    obj: dict[str, Any], key: str, limit: int, limit_name: str, errs: list[ItemError]
) -> list[Any]:
    v = obj.get(key)
    if not isinstance(v, list) or not all(isinstance(i, dict) for i in v):
        msg = f"'{key}' ausente ou não é uma lista de objetos"
        errs.append(ItemError("estrutura_invalida", K_FILE, None, None, msg))
        return []
    if len(v) > limit:
        msg = f"limite excedido: {limit_name}"
        errs.append(ItemError("limite_excedido", K_FILE, None, None, msg))
        return []
    return v


def _check_name(v: Any) -> bool:
    return isinstance(v, str) and bool(v.strip()) and len(v) <= MAX_NAME_CHARS


def _parse_images(
    raw: list[Any], lim: CocoLimits, col: ErrorCollector
) -> tuple[list[CocoImage], dict[int, CocoImage], set[int]]:
    images: list[CocoImage] = []
    by_id: dict[int, CocoImage] = {}
    seen: set[int] = set()
    for i, it in enumerate(raw):
        iid = it.get("id")
        ident = iid if _is_int(iid) else None
        ok = True
        if not _is_int(iid) or iid < 0:
            col.add("imagem_id_invalido", K_IMAGE, None, i, "id da imagem deve ser inteiro >= 0")
            ok = False
        elif iid in seen:
            col.add("imagem_id_duplicado", K_IMAGE, iid, i, "id de imagem repetido")
            ok = False
        else:
            seen.add(iid)
        if not _check_name(it.get("file_name")):
            msg = "file_name deve ser texto não vazio de até 1024 caracteres"
            col.add("imagem_file_name_invalido", K_IMAGE, ident, i, msg)
            ok = False
        dims = []
        for key in ("width", "height"):
            d = it.get(key)
            if not _is_int(d) or d <= 0 or d > lim.max_dimension:
                msg = f"{key} deve ser inteiro positivo até {lim.max_dimension}"
                col.add("imagem_dimensao_invalida", K_IMAGE, ident, i, msg)
                ok = False
            dims.append(d)
        if ok:
            img = CocoImage(iid, it["file_name"], dims[0], dims[1], i)
            images.append(img)
            by_id[iid] = img
    return images, by_id, seen


def _parse_categories(raw: list[Any], col: ErrorCollector) -> tuple[list[CocoCategory], set[int]]:
    cats: list[CocoCategory] = []
    ids: set[int] = set()
    names: set[str] = set()
    for i, it in enumerate(raw):
        cid = it.get("id")
        ident = cid if _is_int(cid) else None
        ok = True
        if not _is_int(cid):
            col.add(
                "categoria_id_invalido", K_CATEGORY, None, i, "id da categoria deve ser inteiro"
            )
            ok = False
        elif cid in ids:
            col.add("categoria_id_duplicado", K_CATEGORY, cid, i, "id de categoria repetido")
            ok = False
        else:
            ids.add(cid)
        name = it.get("name")
        if not _check_name(name):
            msg = "name deve ser texto não vazio de até 1024 caracteres"
            col.add("categoria_nome_invalido", K_CATEGORY, ident, i, msg)
            ok = False
        else:
            stripped = name.strip()
            if stripped in names:
                msg = "nome de categoria repetido"
                col.add("categoria_nome_duplicado", K_CATEGORY, ident, i, msg)
                ok = False
            names.add(stripped)
        if ok:
            cats.append(CocoCategory(cid, name.strip(), i))
    return cats, ids


def _bbox(raw: Any) -> tuple[float, float, float, float] | None:
    if not isinstance(raw, list) or len(raw) != 4:
        return None
    for v in raw:
        if not _is_num(v) or abs(v) > MAX_COORD:
            return None
    x, y, w, h = (float(v) for v in raw)
    if w <= 0 or h <= 0:
        return None
    return x, y, w, h


def _parse_annotations(
    raw: list[Any],
    by_id: dict[int, CocoImage],
    image_ids: set[int],
    category_ids: set[int],
    col: ErrorCollector,
    warn: ErrorCollector,
) -> tuple[list[CocoBox], list[CrowdItem], int]:
    boxes: list[CocoBox] = []
    crowd: list[CrowdItem] = []
    segs = 0
    seen: set[int] = set()
    for i, it in enumerate(raw):
        aid = it.get("id")
        ident = aid if _is_int(aid) else None
        if "id" in it:
            if not _is_int(aid):
                col.add("anotacao_id_invalido", K_ANNOTATION, None, i, "id da anotação inválido")
            elif aid in seen:
                col.add("anotacao_id_duplicado", K_ANNOTATION, aid, i, "id de anotação repetido")
            else:
                seen.add(aid)
        img_id, cat_id = it.get("image_id"), it.get("category_id")
        img_ok = _is_int(img_id) and img_id in image_ids
        if not img_ok:
            msg = "image_id não corresponde a nenhuma imagem do arquivo"
            col.add("anotacao_imagem_inexistente", K_ANNOTATION, ident, i, msg)
        cat_ok = _is_int(cat_id) and cat_id in category_ids
        if not cat_ok:
            msg = "category_id não corresponde a nenhuma categoria do arquivo"
            col.add("anotacao_categoria_inexistente", K_ANNOTATION, ident, i, msg)
        is_crowd = False
        if "iscrowd" in it:
            c = it["iscrowd"]
            if c is False:
                c = 0
            if not _is_int(c) or c not in (0, 1):  # True, null, texto e outros números
                msg = "iscrowd deve ser 0, 1 ou ausente"
                col.add("iscrowd_invalido", K_ANNOTATION, ident, i, msg)
                continue
            is_crowd = c == 1
        if is_crowd:
            crowd.append(CrowdItem(ident, i))
            continue
        if "bbox" not in it:
            col.add("bbox_ausente", K_ANNOTATION, ident, i, "anotação sem bbox")
            continue
        box = _bbox(it["bbox"])
        if box is None:
            msg = "bbox deve ser [x, y, largura, altura] numéricos, com largura e altura > 0"
            col.add("bbox_invalida", K_ANNOTATION, ident, i, msg)
            continue
        x, y, w, h = box
        img = by_id.get(img_id) if img_ok else None
        if img is not None:
            if not representable(x, y, w, h, img.width, img.height):
                msg = "a caixa não cabe no esquema (centro fora da imagem ou maior que ela)"
                col.add("caixa_nao_representavel", K_ANNOTATION, ident, i, msg)
                continue
            if sticks_out(x, y, w, h, img.width, img.height):
                msg = "a caixa ultrapassa a borda da imagem e foi mantida sem recorte"
                warn.add("caixa_fora_da_imagem", K_ANNOTATION, ident, i, msg)
        if it.get("segmentation"):
            segs += 1
        if img_ok and cat_ok:
            boxes.append(CocoBox(ident, i, img_id, cat_id, x, y, w, h))
    return boxes, crowd, segs


def parse_coco(data: bytes, limits: CocoLimits) -> ParsedCoco:
    """Valida e extrai imagens, categorias e caixas de um COCO "instances" (JSON em bytes)."""
    try:
        return _parse(data, limits)
    except CocoImportError:
        raise
    except Exception:  # defesa final: nenhuma outra exceção escapa
        raise _fail("json_invalido", "arquivo não pôde ser interpretado") from None


def _parse(data: bytes, lim: CocoLimits) -> ParsedCoco:
    obj = _load(data, lim)
    errs: list[ItemError] = []
    raw_images = _section(obj, "images", lim.max_images, "max_images", errs)
    raw_annotations = _section(obj, "annotations", lim.max_annotations, "max_annotations", errs)
    raw_categories = _section(obj, "categories", lim.max_categories, "max_categories", errs)
    if errs:
        errs.sort(key=ItemError.sort_key)
        raise CocoImportError(errs[: lim.max_errors], len(errs))

    col = ErrorCollector(lim.max_errors)
    warn = ErrorCollector(lim.max_errors)
    images, by_id, image_ids = _parse_images(raw_images, lim, col)
    categories, category_ids = _parse_categories(raw_categories, col)
    boxes, crowd, segs = _parse_annotations(
        raw_annotations, by_id, image_ids, category_ids, col, warn
    )
    col.raise_if_any()
    return ParsedCoco(
        tuple(images),
        tuple(categories),
        tuple(boxes),
        tuple(warn.items()),
        warn.total,
        tuple(crowd),
        segs,
    )

"""Parser puro do corpo de importação de rótulos txt (F1-07b). Só biblioteca padrão, sem I/O.

Formato de rótulos txt (um formato de dados público): uma linha
`classe cx cy w h` (centro, largura e altura normalizados) por caixa, um arquivo por imagem, mais a
lista de nomes de classe. Escrito a partir dessa descrição pública; nada é lido de disco e nenhuma
chave do corpo é usada como caminho.

Contrato: `parse_txt_labels(bytes, TxtLimits) -> ParsedTxtLabels` é determinística. Qualquer
defeito levanta `ImportRejectedError` (erros por item ordenados por `(kind, index, line)`, com
`file` e `line`); nenhuma outra exceção escapa. Os limites são conferidos antes dos laços: bytes do
corpo antes de `json.loads`, quantidade de arquivos e de linhas (`count("\\n")`) antes de qualquer
`split`. Linhas se separam só em `\\n` (nunca `splitlines`), de modo que a numeração é estável.
Sem expressão regular: cada linha é fatiada por `str.split()` depois de ter o tamanho conferido.
"""

from __future__ import annotations

import json
import math
import unicodedata
from dataclasses import dataclass
from typing import Any, NamedTuple

from inovalytics.imports.common import (
    bad_constant,
    depth_exceeded,
    pairs_hook,
    parse_float,
    parse_int,
    shown,
)
from inovalytics.imports.errors import ErrorCollector, ImportRejectedError, ItemError
from inovalytics.imports.names_yaml import (
    TxtLimits,
    normalize_names,
    parse_names_yaml,
)

__all__ = ["LabelFile", "ParsedTxtLabels", "TxtBox", "TxtLimits", "parse_txt_labels"]

K_FILE = "arquivo"
K_LABEL = "rotulo"
MAX_DEPTH = 4
MAX_LINE_CHARS = 1024
MAX_KEY_CHARS = 1024
MAX_NUMBER_CHARS = 32
MAX_CLASS_DIGITS = 9
TOL = 1e-6
HI = 1.000001  # 1 + TOL como literal: a fronteira é exatamente o que o usuário escreve
LO = -1e-6
_NUMBER_CHARS = frozenset("0123456789+-.eE")
_CONTROL = frozenset({"Cc", "Cs", "Zl", "Zp"})


class TxtBox(NamedTuple):
    """Caixa normalizada (centro, largura, altura) da linha `line` do arquivo `file_index`."""

    file_index: int
    line: int
    class_id: int  # índice em `names`
    cx: float
    cy: float
    w: float
    h: float


@dataclass(frozen=True, slots=True)
class LabelFile:
    index: int  # posição na ordem de chave ordenada
    name: str  # chave exibível (sem controles, até 200 caracteres)
    base_name: str  # último componente da chave sem o ".txt" (nada toca disco)
    box_count: int


@dataclass(frozen=True)
class ParsedTxtLabels:
    names: tuple[str, ...]
    files: tuple[LabelFile, ...]
    boxes: tuple[TxtBox, ...]  # ordem (arquivo, linha)
    warnings: tuple[ItemError, ...]  # limitada a max_errors; o total real está abaixo
    warnings_total: int
    # Só com `collect_errors=True`: defeitos por item adiados para o serviço juntá-los aos de
    # casamento (o relatório mostra tudo de uma vez). Vazio quando o parser já levantou.
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


def _fields(line: str) -> list[str]:
    """Separa os campos de uma linha já conferida (até 1024 caracteres). Ponto de teste."""
    return line.split()


def _load(data: bytes, lim: TxtLimits) -> dict[str, Any]:
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


def _names(obj: dict[str, Any], lim: TxtLimits) -> list[str]:
    has_names = "names" in obj
    has_yaml = "data_yaml" in obj
    if has_names and has_yaml:
        raise _fail("names_ambiguo", "envie 'names' ou 'data_yaml', não os dois")
    if has_names:
        return normalize_names(obj["names"], lim)
    if has_yaml:
        text = obj["data_yaml"]
        if not isinstance(text, str):
            raise _fail("estrutura_invalida", "'data_yaml' deve ser texto")
        return parse_names_yaml(text, lim)
    raise _fail("names_ausente", "informe 'names' ou 'data_yaml'")


def _key_problem(key: str) -> tuple[str, str] | None:
    """(código, mensagem) se a chave de arquivo é inaceitável; o tamanho vem antes de tudo."""
    if not key or len(key) > MAX_KEY_CHARS:
        return "rotulo_nome_invalido", "nome do arquivo de rótulo vazio ou maior que 1024"
    if not key.isprintable() and any(unicodedata.category(c) in _CONTROL for c in key):
        return "rotulo_nome_invalido", "nome do arquivo de rótulo com caractere de controle"
    if not key.endswith(".txt"):
        return "rotulo_extensao_invalida", "o arquivo de rótulo deve terminar em '.txt'"
    return None


def _base_name(key: str) -> str:
    return key.replace("\\", "/").rsplit("/", 1)[-1][:-4]


def _number(tok: str) -> float | None:
    if len(tok) > MAX_NUMBER_CHARS or not _NUMBER_CHARS.issuperset(tok):
        return None
    try:
        v = float(tok)
    except ValueError:
        return None
    return v if math.isfinite(v) else None


def parse_txt_labels(
    data: bytes, limits: TxtLimits, *, collect_errors: bool = False
) -> ParsedTxtLabels:
    """Valida o corpo JSON e extrai nomes, arquivos de rótulo e caixas normalizadas.

    Defeitos por item levantam `ImportRejectedError`, salvo com `collect_errors=True`: aí vão para
    `errors`/`errors_total` do resultado (defeitos do corpo, de `names` e limites sempre levantam).
    """
    try:
        return _parse(data, limits, collect_errors)
    except ImportRejectedError:
        raise
    except Exception:  # defesa final: nenhuma outra exceção escapa
        raise _fail("json_invalido", "o corpo não pôde ser interpretado") from None


def _parse(data: bytes, lim: TxtLimits, collect_errors: bool) -> ParsedTxtLabels:
    obj = _load(data, lim)
    labels = obj.get("labels")
    if not isinstance(labels, dict):
        raise _fail("estrutura_invalida", "'labels' ausente ou não é um objeto")
    names = _names(obj, lim)
    if len(labels) > lim.max_images:
        raise _limit("max_images")

    col = ErrorCollector(lim.max_errors)
    keys = sorted(labels)
    lines_total = 0
    for fi, key in enumerate(keys):
        text = labels[key]
        if not isinstance(text, str):
            col.add("rotulo_invalido", K_LABEL, None, fi, "o conteúdo do rótulo deve ser texto",
                    file=shown(key))  # fmt: skip
            continue
        lines_total += text.count("\n")  # varredura em C; nenhum split antes do teste abaixo
    if lines_total > lim.max_annotations + lim.max_images:
        raise _limit("max_linhas")

    warn = ErrorCollector(lim.max_errors)
    files: list[LabelFile] = []
    boxes: list[TxtBox] = []
    n_names = len(names)
    max_boxes = lim.max_annotations
    for fi, key in enumerate(keys):
        text = labels[key]
        if not isinstance(text, str):
            continue
        problem = _key_problem(key)
        if problem is not None:
            col.add(problem[0], K_LABEL, None, fi, problem[1], file=shown(key))
            continue
        base = _base_name(key)
        if not base.strip():
            col.add("rotulo_nome_invalido", K_LABEL, None, fi, "nome do rótulo vazio",
                    file=shown(key))  # fmt: skip
            continue
        if text.startswith("﻿"):
            text = text[1:]
        before = len(boxes)
        fname: str | None = None
        for ln, raw in enumerate(text.split("\n"), 1):
            if raw.endswith("\r"):
                raw = raw[:-1]
            problem = None
            if len(raw) > MAX_LINE_CHARS:
                problem = ("linha_longa", "linha com mais de 1024 caracteres")
            else:
                parts = _fields(raw)
                n = len(parts)
                if n == 0:
                    continue
                if n != 5:
                    problem = _columns_problem(n)
                else:
                    problem = _box_problem(parts, n_names, boxes, fi, ln, warn, key)
            if problem is not None:
                if fname is None:
                    fname = shown(key)
                col.add(problem[0], K_LABEL, None, fi, problem[1], file=fname, line=ln)
            elif len(boxes) > max_boxes:
                raise _limit("max_annotations")
        files.append(LabelFile(fi, shown(key), base, len(boxes) - before))
    if not collect_errors:
        col.raise_if_any(ImportRejectedError)
    return ParsedTxtLabels(
        tuple(names),
        tuple(files),
        tuple(boxes),
        tuple(warn.items()),
        warn.total,
        tuple(col.items()),
        col.total,
    )


def _columns_problem(n: int) -> tuple[str, str]:
    if n < 5:
        return "colunas_insuficientes", f"a linha tem {n} colunas; esperado 5"
    if n == 6:
        return "coluna_extra", "a linha tem 6 colunas (parece confiança de predição); esperado 5"
    if n >= 7 and n % 2 == 1:
        return "segmentacao_nao_suportada", "polígonos de segmentação não são suportados"
    return "colunas_excedentes", f"a linha tem {n} colunas; esperado 5"


def _box_problem(
    parts: list[str],
    n_names: int,
    boxes: list[TxtBox],
    fi: int,
    ln: int,
    warn: ErrorCollector,
    key: str,
) -> tuple[str, str] | None:
    """Valida os 5 campos; em caso de sucesso acrescenta a caixa e devolve None."""
    c = parts[0]
    if not (c.isascii() and c.isdigit()):
        return "classe_invalida", "a classe deve ser um inteiro sem sinal (só dígitos)"
    if len(c) > MAX_CLASS_DIGITS or int(c) >= n_names:
        return "classe_fora_do_intervalo", "classe fora do intervalo de names"
    nums: list[float] = []
    for tok in parts[1:]:
        v = _number(tok)
        if v is None:
            return "numero_invalido", "número inválido (use decimal finito de até 32 caracteres)"
        nums.append(v)
    cx, cy, w, h = nums
    if w <= 0 or h <= 0:
        return "caixa_area_zero", "largura e altura devem ser maiores que zero"
    if not (LO <= cx <= HI and LO <= cy <= HI and w <= HI and h <= HI):
        return "valor_fora_do_intervalo", "valor fora de [0, 1] (tolerância de 1e-6)"
    # `+ 0.0` normaliza o zero negativo (`-0` vira 0.0), para não gravar `-0.0` no banco
    cx = min(max(cx, 0.0), 1.0) + 0.0
    cy = min(max(cy, 0.0), 1.0) + 0.0
    w = min(w, 1.0)
    h = min(h, 1.0)
    if cx - w / 2 < LO or cx + w / 2 > HI or cy - h / 2 < LO or cy + h / 2 > HI:
        warn.add(
            "caixa_fora_da_imagem",
            K_LABEL,
            None,
            fi,
            "a caixa ultrapassa a borda da imagem e foi mantida sem recorte",
            file=shown(key),
            line=ln,
        )
    boxes.append(TxtBox(fi, ln, int(c), cx, cy, w, h))
    return None

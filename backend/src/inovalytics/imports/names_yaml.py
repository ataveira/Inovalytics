"""Nomes de classe de um `data.yaml` (subconjunto mínimo) ou de JSON (F1-07a). Só biblioteca padrão.

Escrito a partir da descrição pública do arquivo `data.yaml` (chaves `names` e `nc`). Não é um
leitor de YAML: aceita só o que está descrito em `parse_names_yaml` e recusa o resto com
`yaml_nao_suportado`. Sem expressão regular, sem recursão e sem retrocesso: uma passada por linha,
com índices e `str.find`. Todo escalar é texto (nada de booleano, número ou data). Chaves de nível
raiz que não são `names` nem `nc` e seus filhos são ignorados sem interpretação (âncoras e aliases
ali nunca são expandidos). Os limites são conferidos antes de qualquer varredura.

Contrato: funções puras e determinísticas; qualquer defeito levanta `ImportRejectedError` com um
`ItemError` (`kind` "data_yaml" ou "names", `line` quando houver); nenhuma outra exceção escapa.
Mensagens são fixas e não repetem o conteúdo enviado.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from inovalytics.imports.common import MAX_NAME_CHARS, name_has_bad_chars, name_is_blank
from inovalytics.imports.errors import ImportRejectedError, ItemError

K_YAML = "data_yaml"
K_NAMES = "names"
MAX_INDEX_DIGITS = 9  # ids de classe: até 9 dígitos, como o índice nas linhas de rótulo
EXTRA_YAML_LINES = 200

_BAD_START = "&*!|>%@`"  # âncora, alias, tag, bloco literal/dobrado, diretiva, reservados
_NULLS = frozenset({"~", "null", "Null", "NULL"})


@dataclass(frozen=True)
class TxtLimits:
    """Limites da importação de rótulos txt (todos conferidos antes de montar estruturas)."""

    max_json_bytes: int = 16 * 1024 * 1024
    max_images: int = 50_000
    max_annotations: int = 300_000
    max_categories: int = 1_000
    max_errors: int = 100
    max_yaml_bytes: int = 64 * 1024


def _err(
    code: str, kind: str, message: str, line: int | None = None, index: int | None = None
) -> ImportRejectedError:
    return ImportRejectedError([ItemError(code, kind, None, index, message, line=line)], 1)


def _unsupported(line: int, what: str) -> ImportRejectedError:
    return _err("yaml_nao_suportado", K_YAML, f"data_yaml: {what} não é suportado", line)


def _invalid(line: int, what: str) -> ImportRejectedError:
    return _err("yaml_invalido", K_YAML, f"data_yaml inválido: {what}", line)


def _too_many(line: int | None = None, kind: str = K_YAML) -> ImportRejectedError:
    return _err("limite_excedido", kind, "limite excedido: max_categories", line)


# ---------------------------------------------------------------- escalares de uma linha


def _skip(s: str, i: int) -> int:
    n = len(s)
    while i < n and s[i] in " \t":
        i += 1
    return i


def _quoted(s: str, i: int, line: int) -> tuple[str, int]:
    """Escalar entre aspas a partir de `s[i]`; devolve (texto, índice após a aspa final)."""
    q = s[i]
    j = i + 1
    parts: list[str] = []
    if q == "'":
        while True:
            k = s.find("'", j)
            if k < 0:
                raise _invalid(line, "aspas não fechadas")
            parts.append(s[j:k])
            if s.startswith("''", k):
                parts.append("'")
                j = k + 2
            else:
                return "".join(parts), k + 1
    n = len(s)
    while j < n:
        c = s[j]
        if c == '"':
            return "".join(parts), j + 1
        if c == "\\":
            nxt = s[j + 1 : j + 2]
            if nxt not in ('"', "\\"):
                raise _invalid(line, "sequência de escape não suportada")
            parts.append(nxt)
            j += 2
            continue
        parts.append(c)
        j += 1
    raise _invalid(line, "aspas não fechadas")


def _plain(s: str, i: int, flow: bool, line: int) -> tuple[str, int]:
    """Escalar simples a partir de `s[i]`. Para em comentário (` #`), no fim da linha, em `:`
    seguido de espaço/fim e, em contexto de fluxo, em `,` `]` `}`. Devolve (texto, índice)."""
    n = len(s)
    j = i
    while j < n:
        c = s[j]
        if c in " \t":
            k = _skip(s, j)
            if k < n and s[k] == "#":
                j = k
                break
            j = k
            continue
        if flow and c in ",]}":
            break
        if flow and c in "[{":
            raise _unsupported(line, "aninhamento")
        if c == ":":
            nxt = s[j + 1 : j + 2]
            if nxt == "" or nxt in " \t" or (flow and nxt in ",]}"):
                break
        j += 1
    return s[i:j].rstrip(" \t"), j


def _scalar(s: str, i: int, flow: bool, line: int) -> tuple[str, int, bool]:
    """Um escalar a partir de `s[i]` (espaços já pulados): (texto, índice, entre aspas?)."""
    n = len(s)
    if i >= n or s[i] == "#":
        raise _invalid(line, "escalar vazio")
    c = s[i]
    if c in "'\"":
        v, j = _quoted(s, i, line)
        return v, j, True
    if c in _BAD_START:
        raise _unsupported(line, "âncora, alias, tag ou bloco")
    if c in "[{":
        raise _unsupported(line, "aninhamento")
    if flow and c in ",]}":
        raise _invalid(line, "escalar vazio")
    if c == "-" and s[i + 1 : i + 2] in ("", " ", "\t"):
        raise _unsupported(line, "aninhamento")
    v, j = _plain(s, i, flow, line)
    if v == "":
        raise _invalid(line, "escalar vazio")
    if v in _NULLS:
        raise _invalid(line, "valor nulo")
    if v == "<<":
        raise _unsupported(line, "chave de mesclagem")
    return v, j, False


def _end_of_line(s: str, j: int, line: int) -> None:
    """Depois do valor só pode haver espaço ou comentário."""
    j = _skip(s, j)
    if j >= len(s) or s[j] == "#":
        return
    if s[j] == ":":
        raise _unsupported(line, "aninhamento")
    raise _invalid(line, "texto após o valor")


def _canonical_index(text: str) -> int | None:
    """Inteiro decimal canônico (sem sinal, sem zeros à esquerda, ASCII) ou None."""
    if not text or len(text) > MAX_INDEX_DIGITS or not text.isascii() or not text.isdigit():
        return None
    if len(text) > 1 and text[0] == "0":
        return None
    return int(text)


# ---------------------------------------------------------------- coleções de `names`


Entry = tuple[int | None, str, int]  # (id da chave ou None para lista, nome, linha)


def _flow_list(s: str, i: int, line: int, max_items: int) -> list[Entry]:
    n = len(s)
    j = _skip(s, i + 1)
    out: list[Entry] = []
    while True:
        if j >= n:
            raise _unsupported(line, "lista de fluxo em várias linhas")
        if s[j] == "]":
            j += 1
            break
        v, j, _ = _scalar(s, j, True, line)
        out.append((None, v, line))
        if len(out) > max_items:
            raise _too_many(line)
        j = _skip(s, j)
        if j >= n:
            raise _unsupported(line, "lista de fluxo em várias linhas")
        if s[j] == ",":
            j = _skip(s, j + 1)
        elif s[j] == "]":
            j += 1
            break
        elif s[j] == ":":
            raise _unsupported(line, "aninhamento")
        else:
            raise _invalid(line, "separador esperado na lista")
    _end_of_line(s, j, line)
    return out


def _flow_map(s: str, i: int, line: int, max_items: int) -> list[Entry]:
    n = len(s)
    j = _skip(s, i + 1)
    out: list[Entry] = []
    seen: set[int] = set()
    while True:
        if j >= n:
            raise _unsupported(line, "mapa de fluxo em várias linhas")
        if s[j] == "}":
            j += 1
            break
        key, j, _ = _scalar(s, j, True, line)
        j = _skip(s, j)
        if j >= n or s[j] != ":":
            raise _invalid(line, "esperado 'id: nome'")
        idx = _canonical_index(key)
        if idx is None:
            raise _err("names_chave_invalida", K_YAML, "chave de names não é um inteiro", line)
        if idx in seen:
            raise _err("names_id_duplicado", K_YAML, "id de classe repetido", line)
        seen.add(idx)
        j = _skip(s, j + 1)
        v, j, _ = _scalar(s, j, True, line)
        out.append((idx, v, line))
        if len(out) > max_items:
            raise _too_many(line)
        j = _skip(s, j)
        if j >= n:
            raise _unsupported(line, "mapa de fluxo em várias linhas")
        if s[j] == ",":
            j = _skip(s, j + 1)
        elif s[j] == "}":
            j += 1
            break
        else:
            raise _invalid(line, "separador esperado no mapa")
    _end_of_line(s, j, line)
    return out


def _is_item_marker(stripped: str) -> bool:
    return stripped[0] == "-" and stripped[1:2] in ("", " ", "\t")


def _block(lines: list[str], p: int, ln0: int, max_items: int) -> tuple[list[Entry], int]:
    """Lista ou mapa de bloco a partir da linha `p` (0-based). Devolve (entradas, próxima linha)."""
    n = len(lines)
    out: list[Entry] = []
    seen: set[int] = set()
    mode = ""  # "-" lista, ":" mapa
    block_indent = -1
    while p < n:
        s = lines[p][:-1] if lines[p].endswith("\r") else lines[p]
        stripped = s.lstrip(" ")
        if stripped.strip(" \t") == "" or stripped[0] == "#":
            p += 1
            continue
        ln = p + 1
        indent = len(s) - len(stripped)
        if stripped[0] == "\t":
            raise _unsupported(ln, "tabulação na indentação")
        if block_indent < 0:
            block_indent = indent
            mode = "-" if _is_item_marker(stripped) else ":"
            if indent == 0 and mode != "-":
                break  # `names:` vazio seguido de outra chave raiz
        elif indent < block_indent or (
            indent == 0 and mode == "-" and not _is_item_marker(stripped)
        ):
            break
        elif indent > block_indent:
            raise _unsupported(ln, "indentação irregular ou aninhamento")
        p += 1
        if mode == "-":
            if not _is_item_marker(stripped):
                raise _unsupported(ln, "mistura de lista e mapa")
            j = _skip(s, indent + 1)
            v, j, _ = _scalar(s, j, False, ln)
            _end_of_line(s, j, ln)
            out.append((None, v, ln))
        else:
            if _is_item_marker(stripped):
                raise _unsupported(ln, "mistura de lista e mapa")
            if s[indent] in "'\"":
                key, j = _quoted(s, indent, ln)
            else:
                key, j = _plain(s, indent, False, ln)
            if j >= len(s) or s[j] != ":":
                raise _invalid(ln, "esperado 'id: nome'")
            idx = _canonical_index(key)
            if idx is None:
                raise _err("names_chave_invalida", K_YAML, "chave de names não é um inteiro", ln)
            if idx in seen:
                raise _err("names_id_duplicado", K_YAML, "id de classe repetido", ln)
            seen.add(idx)
            v, j, _ = _scalar(s, _skip(s, j + 1), False, ln)
            _end_of_line(s, j, ln)
            out.append((idx, v, ln))
        if len(out) > max_items:
            raise _too_many(ln)
    if not out:
        raise _invalid(ln0, "names sem valor")
    return out, p


def _names_entries(
    lines: list[str], p: int, s: str, i: int, ln: int, max_items: int
) -> tuple[list[Entry], int]:
    """Valor de `names` que começa em `s[i]` (linha `ln`); `p` é o índice da linha seguinte."""
    if i >= len(s) or s[i] == "#":
        return _block(lines, p, ln, max_items)
    c = s[i]
    if c == "[":
        return _flow_list(s, i, ln, max_items), p
    if c == "{":
        return _flow_map(s, i, ln, max_items), p
    if c in _BAD_START:
        raise _unsupported(ln, "âncora, alias, tag ou bloco")
    raise _unsupported(ln, "names que não seja lista ou mapa")


def _assemble(entries: list[Entry]) -> tuple[list[str], list[int]]:
    """Ordena por id (mapa) exigindo `0..n-1`; lista mantém a ordem."""
    if not entries or entries[0][0] is None:
        return [e[1] for e in entries], [e[2] for e in entries]
    n = len(entries)
    values: list[str] = [""] * n
    lns: list[int] = [0] * n
    for idx, v, ln in entries:
        if idx is None or idx >= n:
            raise _err("names_nao_contiguo", K_YAML, "ids de names devem ser 0..n-1", ln)
        values[idx] = v
        lns[idx] = ln
    return values, lns


# ---------------------------------------------------------------- validação dos nomes


def _clean(name: str) -> bool:
    """Sem controles, substitutos, Zl/Zp nem Cf (ZWJ/ZWNJ só entre caracteres visíveis)."""
    return not name_has_bad_chars(name)


def _validate(
    values: Sequence[Any], kind: str, lines: Sequence[int] | None, lim: TxtLimits
) -> list[str]:
    if len(values) > lim.max_categories:
        raise _too_many(None, kind)
    if not values:
        raise _err("names_ausente", kind, "names vazio", None)
    out: list[str] = []
    seen: set[str] = set()
    for i, v in enumerate(values):
        line = lines[i] if lines is not None else None
        if not isinstance(v, str):
            raise _err("names_tipo_invalido", kind, "nome de classe deve ser texto", line, i)
        if len(v) > MAX_NAME_CHARS or name_is_blank(v) or not _clean(v):
            msg = "nome de classe deve ser texto não vazio, sem controles, até 1024 caracteres"
            raise _err("nome_invalido", kind, msg, line, i)
        name = v.strip()
        if name in seen:
            raise _err("nome_duplicado", kind, "nome de classe repetido", line, i)
        seen.add(name)
        out.append(name)
    return out


def normalize_names(raw: Any, limits: TxtLimits) -> list[str]:
    """Nomes vindos de JSON: `list[str]` ou `dict[str, str]` com chaves canônicas `"0".."n-1"`."""
    try:
        return _normalize(raw, limits)
    except ImportRejectedError:
        raise
    except Exception:
        raise _err("names_tipo_invalido", K_NAMES, "names não pôde ser interpretado") from None


def _normalize(raw: Any, lim: TxtLimits) -> list[str]:
    if isinstance(raw, list):
        return _validate(raw, K_NAMES, None, lim)
    if not isinstance(raw, dict):
        raise _err("names_tipo_invalido", K_NAMES, "names deve ser lista ou objeto")
    if len(raw) > lim.max_categories:
        raise _too_many(None, K_NAMES)
    n = len(raw)
    values: list[Any] = [None] * n
    seen: set[int] = set()
    for k, v in raw.items():
        idx = _canonical_index(k) if isinstance(k, str) else None
        if idx is None:
            raise _err("names_chave_invalida", K_NAMES, "chave de names não é um inteiro")
        if idx >= n or idx in seen:
            raise _err("names_nao_contiguo", K_NAMES, "ids de names devem ser 0..n-1")
        seen.add(idx)
        values[idx] = v
    return _validate(values, K_NAMES, None, lim)


# ---------------------------------------------------------------- data.yaml


def parse_names_yaml(text: str, limits: TxtLimits) -> list[str]:
    """Extrai a lista de nomes (índice = id da classe) de um `data.yaml`.

    Aceito: chaves de nível raiz `names` (lista de fluxo `[a, b]`, mapa de fluxo `{0: a}`, lista de
    bloco `- a` ou mapa de bloco `0: a`) e `nc` (inteiro igual a `len(names)`); `---` inicial;
    comentários `#`; escalares simples ou entre aspas (`''` em aspas simples; `\\"` e `\\\\` em
    aspas duplas). As demais chaves de nível raiz e seus filhos são ignorados.
    """
    try:
        return _parse(text, limits)
    except ImportRejectedError:
        raise
    except Exception:
        raise _invalid(1, "não pôde ser interpretado") from None


def _parse(text: str, lim: TxtLimits) -> list[str]:
    if not isinstance(text, str):
        raise _invalid(1, "não é texto")
    if len(text) > lim.max_yaml_bytes or len(text.encode("utf-8", "surrogatepass")) > (
        lim.max_yaml_bytes
    ):
        raise _err("limite_excedido", K_YAML, "limite excedido: max_yaml_bytes")
    if text.count("\n") + 1 > lim.max_categories + EXTRA_YAML_LINES:
        raise _err("limite_excedido", K_YAML, "limite excedido: max_linhas_yaml")
    nul = text.find("\x00")
    if nul >= 0:
        raise _invalid(text.count("\n", 0, nul) + 1, "caractere NUL")
    lines = text.split("\n")
    if lines[0].startswith("﻿"):
        lines[0] = lines[0][1:]

    entries: list[Entry] | None = None
    nc: int | None = None
    nc_line = 0
    seen_doc = False
    seen_key = False
    p = 0
    total = len(lines)
    while p < total:
        ln = p + 1
        s = lines[p][:-1] if lines[p].endswith("\r") else lines[p]
        p += 1
        stripped = s.lstrip(" ")
        if stripped.strip(" \t") == "" or stripped[0] == "#":
            continue
        if stripped[0] == "\t":
            raise _unsupported(ln, "tabulação na indentação")
        if len(s) != len(stripped):
            if not seen_key:
                raise _unsupported(ln, "conteúdo indentado sem chave")
            continue  # filho de uma chave raiz (ignorado sem interpretação)
        if s.startswith("---") and s[3:4] in ("", " ", "\t"):
            if seen_doc or seen_key:
                raise _unsupported(ln, "segundo documento")
            tail = s[3:].strip(" \t")
            if tail and tail[0] != "#":
                raise _unsupported(ln, "conteúdo após '---'")
            seen_doc = True
            continue
        if s[0] in "'\"":
            key, j = _quoted(s, 0, ln)
        else:
            key, j = _plain(s, 0, False, ln)
        if j >= len(s) or s[j] != ":":
            raise _unsupported(ln, "linha que não é 'chave: valor'")
        seen_key = True
        i = _skip(s, j + 1)
        if key == "names":
            if entries is not None:
                raise _err("names_duplicado", K_YAML, "names repetido", ln)
            entries, p = _names_entries(lines, p, s, i, ln, lim.max_categories)
        elif key == "nc":
            if nc is not None:
                raise _err("nc_invalido", K_YAML, "nc repetido", ln)
            v, j2, quoted = _scalar(s, i, False, ln)
            _end_of_line(s, j2, ln)
            nc = None if quoted else _canonical_index(v)
            if nc is None:
                raise _err("nc_invalido", K_YAML, "nc deve ser um inteiro", ln)
            nc_line = ln
    if entries is None:
        raise _err("names_ausente", K_YAML, "names ausente no data_yaml")
    values, lns = _assemble(entries)
    names = _validate(values, K_YAML, lns, lim)
    if nc is not None and nc != len(names):
        raise _err("nc_divergente", K_YAML, "nc difere da quantidade de nomes", nc_line)
    return names

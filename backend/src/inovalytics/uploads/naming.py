"""Nome de arquivo sanitizado e chave S3 determinística por conteúdo."""

from __future__ import annotations

import re
import unicodedata

from inovalytics.imports.chartables import DROPPED_CHARS

_SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_EXTS = frozenset({"jpg", "png"})
_FALLBACK = "imagem"
MAX_NAME = 255
# Removidos do nome: controle, formatação invisível (bidi, ZWSP, BOM) e separadores de
# linha/parágrafo (Cc, Cf, Zl, Zp). `DROPPED_CHARS` é a tabela de pontos de código dessas
# categorias (calculada com o `unicodedata` do processo); `translate` remove em C.
_DROP_TABLE = dict.fromkeys(map(ord, DROPPED_CHARS))
_MAX_REPLACES = 8


def build_object_key(dataset_id: int, sha256: str, ext: str) -> str:
    """`datasets/{dataset_id}/{sha256}.{ext}`: o nome do cliente nunca entra na chave."""
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0:
        raise ValueError("dataset_id inválido")
    if not isinstance(sha256, str) or _SHA_RE.fullmatch(sha256) is None:
        raise ValueError("sha256 inválido")
    if not isinstance(ext, str) or ext not in _EXTS:
        raise ValueError("extensão inválida")
    return f"datasets/{dataset_id}/{sha256}.{ext}"


def _drop(s: str) -> str:
    """Remove os caracteres das categorias Cc, Cf, Zl e Zp, tudo em C.

    Poucos distintos (o caso comum): um `replace` por caractere presente, que é uma busca em C.
    Muitos distintos (hostil): uma única passada de `translate`, de custo limitado.
    """
    present = DROPPED_CHARS.intersection(s)
    if len(present) > _MAX_REPLACES:
        return s.translate(_DROP_TABLE)
    for ch in present:
        s = s.replace(ch, "")
    return s


def _once(name: str) -> str:
    s = unicodedata.normalize("NFC", name)
    s = re.split(r"[/\\]", s)[-1]
    if not s.isprintable():  # imprimível = nenhuma das categorias removidas
        s = _drop(s)
    s = s.strip()
    if s in ("", ".", ".."):
        return _FALLBACK
    if len(s) > MAX_NAME:
        dot = s.rfind(".")
        ext = s[dot:] if dot > 0 and len(s) - dot <= 16 else ""
        s = s[: MAX_NAME - len(ext)] + ext
        s = s.strip()
        if s in ("", ".", ".."):
            return _FALLBACK
    return s


def sanitize_file_name(name: str) -> str:
    """Último componente, NFC, sem controles, até 255 caracteres; idempotente."""
    # Atalho (nome já limpo, o caso comum): ASCII imprimível sem separador, sem borda em branco,
    # curto e não vazio é ponto fixo de `_once`.
    if (
        name.isascii()
        and name.isprintable()
        and "/" not in name
        and "\\" not in name
        and 0 < len(name) <= MAX_NAME
        and name == name.strip()
        and name not in (".", "..")
    ):
        return name
    s = _once(name)
    for _ in range(4):  # ponto fixo: garante idempotência mesmo após truncar/normalizar
        t = _once(s)
        if t == s:
            break
        s = t
    return s

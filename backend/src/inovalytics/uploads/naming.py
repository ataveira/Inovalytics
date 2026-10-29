"""Nome de arquivo sanitizado e chave S3 determinística por conteúdo."""

from __future__ import annotations

import re
import unicodedata

_SHA_RE = re.compile(r"[0-9a-f]{64}", re.ASCII)
_EXTS = frozenset({"jpg", "png"})
_FALLBACK = "imagem"
MAX_NAME = 255
# controle, formatação invisível (bidi, ZWSP, BOM) e separadores de linha/parágrafo
_DROPPED = frozenset({"Cc", "Cf", "Zl", "Zp"})


def build_object_key(dataset_id: int, sha256: str, ext: str) -> str:
    """`datasets/{dataset_id}/{sha256}.{ext}`: o nome do cliente nunca entra na chave."""
    if isinstance(dataset_id, bool) or not isinstance(dataset_id, int) or dataset_id <= 0:
        raise ValueError("dataset_id inválido")
    if not isinstance(sha256, str) or _SHA_RE.fullmatch(sha256) is None:
        raise ValueError("sha256 inválido")
    if not isinstance(ext, str) or ext not in _EXTS:
        raise ValueError("extensão inválida")
    return f"datasets/{dataset_id}/{sha256}.{ext}"


def _once(name: str) -> str:
    s = unicodedata.normalize("NFC", name)
    s = re.split(r"[/\\]", s)[-1]
    s = "".join(c for c in s if unicodedata.category(c) not in _DROPPED).strip()
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
    s = _once(name)
    for _ in range(4):  # ponto fixo: garante idempotência mesmo após truncar/normalizar
        t = _once(s)
        if t == s:
            break
        s = t
    return s

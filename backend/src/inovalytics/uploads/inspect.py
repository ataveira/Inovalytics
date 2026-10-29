"""Inspeção de JPEG e PNG só pelo cabeçalho (sem decodificar pixels, sem Pillow).

Escrito a partir das especificações públicas (ITU T.81 e PNG). Limite assumido: arquivo com
cabeçalho válido e corpo corrompido passa; detectar isso é a F1-10.
"""

from __future__ import annotations

from typing import NamedTuple

from inovalytics.uploads.errors import InvalidImageError, UnsupportedTypeError
from inovalytics.uploads.limits import HEADER_BYTES, UploadLimits

_PNG_SIG = b"\x89PNG\r\n\x1a\n"
_JPEG_SIG = b"\xff\xd8\xff"
# tipo de cor PNG -> profundidades de bit válidas
_PNG_DEPTHS = {0: {1, 2, 4, 8, 16}, 2: {8, 16}, 3: {1, 2, 4, 8}, 4: {8, 16}, 6: {8, 16}}
_SOF_NOT_FRAME = {0xC4, 0xC8, 0xCC}
_STANDALONE = {0x01, 0xD8, *range(0xD0, 0xD8)}


class ImageInfo(NamedTuple):
    content_type: str
    ext: str
    width: int
    height: int


def _png(h: bytes) -> tuple[int, int]:
    if len(h) < 8 + 8 + 13 or h[8:12] != b"\x00\x00\x00\x0d" or h[12:16] != b"IHDR":
        raise InvalidImageError("PNG inválido: bloco IHDR ausente ou malformado.")
    width = int.from_bytes(h[16:20], "big")
    height = int.from_bytes(h[20:24], "big")
    depth, color, comp, filt, interlace = h[24], h[25], h[26], h[27], h[28]
    if width == 0 or height == 0:
        raise InvalidImageError("PNG inválido: dimensão zero.")
    if depth not in _PNG_DEPTHS.get(color, set()):
        raise InvalidImageError("PNG inválido: tipo de cor ou profundidade inválidos.")
    if comp != 0 or filt != 0 or interlace not in (0, 1):
        raise InvalidImageError("PNG inválido: parâmetros do IHDR inválidos.")
    return width, height


def _jpeg(h: bytes) -> tuple[int, int]:
    n = len(h)
    pos = 2
    while True:
        # próximo marcador: FF + byte != 00 (FFs extras são preenchimento)
        if pos >= n or h[pos] != 0xFF:
            raise InvalidImageError("JPEG inválido: marcador esperado não encontrado.")
        while pos < n and h[pos] == 0xFF:
            pos += 1
        if pos >= n:
            raise InvalidImageError("JPEG truncado antes do quadro (SOF).")
        marker = h[pos]
        pos += 1
        if marker == 0x00:
            raise InvalidImageError("JPEG inválido: marcador inesperado.")
        if marker in _STANDALONE:
            continue
        if marker in (0xD9, 0xDA):  # EOI ou início de varredura sem SOF antes
            raise InvalidImageError("JPEG inválido: quadro (SOF) ausente.")
        if pos + 2 > n:
            raise InvalidImageError("JPEG truncado antes do quadro (SOF).")
        length = int.from_bytes(h[pos : pos + 2], "big")
        if length < 2:
            raise InvalidImageError("JPEG inválido: segmento com comprimento inválido.")
        if pos + length > n:
            raise InvalidImageError("JPEG truncado ou segmento além do fim do cabeçalho.")
        if 0xC0 <= marker <= 0xCF and marker not in _SOF_NOT_FRAME:
            if length < 8:
                raise InvalidImageError("JPEG inválido: segmento SOF curto demais.")
            height = int.from_bytes(h[pos + 3 : pos + 5], "big")
            width = int.from_bytes(h[pos + 5 : pos + 7], "big")
            if width == 0 or height == 0:
                raise InvalidImageError("JPEG inválido: dimensão zero.")
            return width, height
        pos += length


def inspect_image(header: bytes, limits: UploadLimits | None = None) -> ImageInfo:
    """Detecta o tipo pela assinatura e lê as dimensões.

    Levanta `UnsupportedTypeError` (415) para tipo desconhecido e `InvalidImageError` (422) para
    arquivo vazio, estrutura inválida ou dimensões fora dos limites.
    """
    lim = limits or UploadLimits()
    h = bytes(header[:HEADER_BYTES])
    if not h:
        raise InvalidImageError("Arquivo vazio.")
    if h.startswith(_PNG_SIG):
        content_type, ext = "image/png", "png"
        width, height = _png(h)
    elif h.startswith(_JPEG_SIG):
        content_type, ext = "image/jpeg", "jpg"
        width, height = _jpeg(h)
    elif h in (_JPEG_SIG[:1], _JPEG_SIG[:2]):
        raise InvalidImageError("JPEG truncado.")
    else:
        raise UnsupportedTypeError("Tipo de arquivo não suportado: envie JPEG ou PNG.")
    if width > lim.max_dimension or height > lim.max_dimension:
        raise InvalidImageError("Imagem com dimensão acima do limite permitido.")
    if width * height > lim.max_pixels:
        raise InvalidImageError("Imagem com total de pixels acima do limite permitido.")
    return ImageInfo(content_type, ext, width, height)

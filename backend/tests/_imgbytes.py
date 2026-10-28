"""Bytes mínimos de JPEG/PNG montados à mão (sem Pillow, sem arquivos de dados)."""

import struct
import zlib


def png(w: int = 4, h: int = 3, depth: int = 8, color: int = 2, *, ihdr_len: int = 13) -> bytes:
    data = struct.pack(">IIBBBBB", w, h, depth, color, 0, 0, 0)
    ihdr = b"\x00" * 0 + struct.pack(">I", ihdr_len) + b"IHDR" + data
    ihdr += struct.pack(">I", zlib.crc32(b"IHDR" + data))
    return b"\x89PNG\r\n\x1a\n" + ihdr + b"\x00\x00\x00\x00IEND\xaeB`\x82"


def jpeg(w: int = 4, h: int = 3, sof: int = 0xC0, exif: int = 0) -> bytes:
    out = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    if exif:
        out += b"\xff\xe1" + struct.pack(">H", exif + 2) + b"E" * exif
    out += bytes([0xFF, sof]) + struct.pack(">HBHHB", 11, 8, h, w, 1) + b"\x01\x11\x00"
    return out + b"\xff\xda\x00\x02" + b"\x00" * 8 + b"\xff\xd9"

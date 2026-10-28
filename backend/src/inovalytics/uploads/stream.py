"""Leitura em blocos com SHA-256 e limite de tamanho (memória limitada)."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
from dataclasses import dataclass
from typing import Any

from inovalytics.uploads.errors import TooLargeError
from inovalytics.uploads.limits import HEADER_BYTES, READ_CHUNK


@dataclass(frozen=True)
class ReadResult:
    sha256: str
    size: int
    header: bytes  # primeiros 1 MiB (ou o arquivo todo, se menor)


async def read_limited(stream: Any, max_bytes: int, chunk: int = READ_CHUNK) -> ReadResult:
    """Lê `stream.read(n)` (síncrono em thread, ou assíncrono) até o fim, sem passar de `max_bytes`.

    Pede no máximo `chunk` bytes por vez e nunca mais que `max_bytes + 1` no total: ao chegar no
    byte `max_bytes + 1` levanta `TooLargeError` sem consumir o restante.
    """
    digest = hashlib.sha256()
    header = bytearray()
    total = 0
    while True:
        want = min(chunk, max_bytes + 1 - total)
        if inspect.iscoroutinefunction(stream.read):
            data = await stream.read(want)
        else:
            # Arquivo síncrono (spool do multipart, possivelmente em disco): fora do event loop.
            data = await asyncio.to_thread(stream.read, want)
            if inspect.isawaitable(data):
                data = await data
        if not data:
            break
        total += len(data)
        if total > max_bytes:
            raise TooLargeError("Arquivo acima do tamanho máximo permitido.")
        digest.update(data)
        if len(header) < HEADER_BYTES:
            header += data[: HEADER_BYTES - len(header)]
    return ReadResult(digest.hexdigest(), total, bytes(header))

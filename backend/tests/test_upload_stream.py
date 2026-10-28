"""F1-05a critério 8: leitura em fluxo com hash e limite."""

import asyncio
import hashlib
import io

import pytest

from inovalytics.uploads.errors import TooLargeError
from inovalytics.uploads.stream import read_limited


class CountingSource:
    """Fonte falsa de 1 GiB simulados: conta bytes entregues e o maior bloco pedido."""

    def __init__(self, total: int) -> None:
        self.total, self.given, self.max_req = total, 0, 0

    async def read(self, n: int) -> bytes:
        self.max_req = max(self.max_req, n)
        k = min(n, self.total - self.given)
        self.given += k
        return b"\x01" * k


def test_hash_size_and_header_match_hashlib() -> None:
    data = bytes(range(256)) * 9000  # ~2.3 MB, maior que 1 MiB
    r = asyncio.run(read_limited(io.BytesIO(data), len(data)))  # fonte síncrona
    assert r.sha256 == hashlib.sha256(data).hexdigest()
    assert r.size == len(data)
    assert r.header == data[: 1024 * 1024]


def test_small_and_empty() -> None:
    r = asyncio.run(read_limited(io.BytesIO(b""), 10))
    assert r.size == 0 and r.header == b"" and r.sha256 == hashlib.sha256(b"").hexdigest()
    assert asyncio.run(read_limited(io.BytesIO(b"x" * 10), 10)).size == 10  # no limite exato


def test_limit_plus_one_raises_without_consuming_rest() -> None:
    src = CountingSource(1024**3)
    with pytest.raises(TooLargeError) as ei:
        asyncio.run(read_limited(src, 100_000, chunk=65536))
    assert ei.value.status == 413
    assert src.given <= 100_000 + 65536
    assert src.max_req <= 65536


def test_exact_boundary_byte() -> None:
    with pytest.raises(TooLargeError):
        asyncio.run(read_limited(io.BytesIO(b"x" * 11), 10))

"""Protocolo `ObjectStore` e implementação sobre um cliente boto3 injetado (sem ACL pública)."""

from __future__ import annotations

import asyncio
from typing import Any, BinaryIO, Protocol


class ObjectStore(Protocol):
    async def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None: ...
    async def delete(self, key: str) -> None: ...


class Boto3ObjectStore:
    """boto3 é síncrono: toda chamada vai por `asyncio.to_thread`. Sem `ACL` (bucket privado)."""

    def __init__(self, client: Any, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    async def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=key,
            Body=fileobj,
            ContentType=content_type,
        )

    async def delete(self, key: str) -> None:
        await asyncio.to_thread(self._client.delete_object, Bucket=self._bucket, Key=key)

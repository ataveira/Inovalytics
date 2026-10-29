"""Serviço de upload atômico: valida tudo, grava no S3 e depois no banco, com compensação.

Ordem: dataset existe -> lê/valida todo o lote (nada é gravado se algo falhar) -> para cada
arquivo: deduplica por `(dataset_id, sha256)` -> PUT no S3 -> `insert_image`. Se o insert falhar
depois do PUT e não houver linha para o sha, o objeto é apagado (compensação). Falha no meio do
lote deixa os anteriores gravados; repetir o lote é seguro (voltam com `created=False`).
Janela residual: processo morto entre PUT e insert deixa objeto órfão (limpeza futura).
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, BinaryIO, Protocol

from inovalytics.uploads.errors import (
    InvalidImageError,
    StorageUnavailableError,
    TooManyFilesError,
    UploadError,
)
from inovalytics.uploads.inspect import ImageInfo, inspect_image
from inovalytics.uploads.limits import UploadLimits
from inovalytics.uploads.naming import build_object_key, sanitize_file_name
from inovalytics.uploads.store import ObjectStore
from inovalytics.uploads.stream import ReadResult, read_limited

if TYPE_CHECKING:
    from inovalytics.db.repository import Dataset, Image

log = logging.getLogger(__name__)


class ImageRepository(Protocol):
    """Repositório já ligado a uma conexão. `get_dataset` levanta `NotFoundError`."""

    async def get_dataset(self, dataset_id: int) -> Dataset: ...
    async def get_image_by_sha(self, dataset_id: int, sha256: str) -> Image | None: ...
    async def insert_image(
        self,
        dataset_id: int,
        *,
        object_key: str,
        file_name: str,
        sha256: str,
        size_bytes: int,
        width_px: int,
        height_px: int,
        split: str | None = None,
    ) -> tuple[Image, bool]: ...


@dataclass(frozen=True)
class UploadItem:
    file_name: str
    file: BinaryIO  # seekable; o serviço relê do início para enviar ao S3


@dataclass(frozen=True)
class UploadResult:
    image: Image
    created: bool


@dataclass(frozen=True)
class _Prepared:
    item: UploadItem
    read: ReadResult
    info: ImageInfo
    key: str
    name: str


async def _prepare(index: int, item: UploadItem, dataset_id: int, lim: UploadLimits) -> _Prepared:
    try:
        item.file.seek(0)
        read = await read_limited(item.file, lim.max_bytes)
        info = inspect_image(read.header, lim)
        key = build_object_key(dataset_id, read.sha256, info.ext)
    except UploadError as exc:
        exc.file_index = index
        raise
    return _Prepared(item, read, info, key, sanitize_file_name(item.file_name))


async def _compensate(repo: ImageRepository, store: ObjectStore, p: _Prepared, ds: int) -> None:
    try:
        if await repo.get_image_by_sha(ds, p.read.sha256) is not None:
            return  # corrida: o objeto é referenciado por uma linha; nunca apagar
        await store.delete(p.key)
    except Exception as exc:
        log.error("falha ao compensar upload; possível objeto órfão key=%s tipo=%s",
                  p.key, type(exc).__name__)  # fmt: skip


def _safe_error_label(exc: BaseException) -> str:
    """Tipo da exceção (+ código S3, se houver); nunca a mensagem, que pode ter endpoint/segredo."""
    label = type(exc).__name__
    response = getattr(exc, "response", None)
    if isinstance(response, dict):
        code = response.get("Error", {}).get("Code")
        if isinstance(code, str) and code.isalnum():
            label += f"/{code}"
    return label


async def _store_one(
    repo: ImageRepository, store: ObjectStore, p: _Prepared, ds: int, split: str | None
) -> UploadResult:
    existing = await repo.get_image_by_sha(ds, p.read.sha256)
    if existing is not None:
        return UploadResult(existing, False)
    put_failed = False
    put_error = ""
    try:
        p.item.file.seek(0)
        await store.put(p.key, p.item.file, p.info.content_type)
    except Exception as exc:
        put_failed = True
        put_error = _safe_error_label(exc)
    if put_failed:  # fora do except: o erro original não vira __context__
        log.error("falha ao gravar objeto key=%s erro=%s", p.key, put_error)
        raise StorageUnavailableError("Armazenamento de imagens indisponível.")
    try:
        image, created = await repo.insert_image(
            ds,
            object_key=p.key,
            file_name=p.name,
            sha256=p.read.sha256,
            size_bytes=p.read.size,
            width_px=p.info.width,
            height_px=p.info.height,
            split=split,
        )
    except Exception as exc:
        log.error("falha ao registrar imagem key=%s tipo=%s", p.key, type(exc).__name__)
        await _compensate(repo, store, p, ds)
        raise
    return UploadResult(image, created)


async def upload_images(
    repo: ImageRepository,
    store: ObjectStore,
    dataset_id: int,
    items: Sequence[UploadItem],
    limits: UploadLimits | None = None,
    *,
    split: str | None = None,
) -> list[UploadResult]:
    lim = limits or UploadLimits()
    if not items:
        raise InvalidImageError("Nenhum arquivo enviado.")
    if len(items) > lim.max_files:
        raise TooManyFilesError("Quantidade de arquivos acima do limite permitido.")
    await repo.get_dataset(dataset_id)  # NotFoundError antes de qualquer PUT
    prepared: list[_Prepared] = []
    for i, item in enumerate(items):
        prepared.append(await _prepare(i, item, dataset_id, lim))
    results: list[UploadResult] = []
    for p in prepared:
        results.append(await _store_one(repo, store, p, dataset_id, split))
    return results


__all__ = ["ImageRepository", "UploadItem", "UploadResult", "upload_images"]

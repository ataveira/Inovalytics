"""F1-05b critérios 11 a 18: serviço de upload atômico com S3 e repositório falsos."""

import asyncio
import datetime as dt
import hashlib
import io
import logging
from typing import Any, BinaryIO

import pytest

from _imgbytes import jpeg, png
from inovalytics.db.repository import Dataset, Image, NotFoundError
from inovalytics.uploads.errors import (
    InvalidImageError,
    StorageUnavailableError,
    TooManyFilesError,
    UnsupportedTypeError,
)
from inovalytics.uploads.limits import UploadLimits
from inovalytics.uploads.service import UploadItem, upload_images
from inovalytics.uploads.store import Boto3ObjectStore

pytestmark = pytest.mark.usefixtures("no_external_network")
NOW = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
SECRETS = ["S3SECRET-xyz", "S3ACCESS-xyz", "PGPASS-xyz", "postgresql://u:PGPASS-xyz@h/db"]


class FakeStore:
    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.fail_put_at: int | None = None
        self.fail_delete = False
        self.puts = 0

    async def put(self, key: str, fileobj: BinaryIO, content_type: str) -> None:
        self.puts += 1
        self.calls.append("put")
        await asyncio.sleep(0)
        if self.fail_put_at == self.puts:
            raise RuntimeError("boom " + SECRETS[0] + " http://storage:8333 /srv/x")
        self.objects[key] = (fileobj.read(), content_type)

    async def delete(self, key: str) -> None:
        self.calls.append("delete")
        if self.fail_delete:
            raise RuntimeError("delete falhou " + SECRETS[1])
        self.objects.pop(key, None)


class FakeRepo:
    """Reproduz o ON CONFLICT (dataset_id, sha256) do banco."""

    def __init__(self, calls: list[str]) -> None:
        self.calls = calls
        self.datasets = {1, 2}
        self.rows: dict[tuple[int, str], Image] = {}
        self.inserts: list[dict[str, Any]] = []
        self.fail_insert = False
        self.hide_rows_on_reread = False
        self.race_row: Image | None = None

    async def get_dataset(self, dataset_id: int) -> Dataset:
        self.calls.append("get_dataset")
        if dataset_id not in self.datasets:
            raise NotFoundError("dataset não encontrado")
        return Dataset(dataset_id, 1, "d", NOW)

    async def get_image_by_sha(self, dataset_id: int, sha256: str) -> Image | None:
        self.calls.append("get_by_sha")
        await asyncio.sleep(0)
        return self.rows.get((dataset_id, sha256))

    async def insert_image(self, dataset_id: int, **kw: Any) -> tuple[Image, bool]:
        self.calls.append("insert")
        await asyncio.sleep(0)
        if self.fail_insert:
            if self.race_row is not None:
                self.rows[(dataset_id, kw["sha256"])] = self.race_row
            raise RuntimeError("db down " + SECRETS[3])
        self.inserts.append({"dataset_id": dataset_id, **kw})
        k = (dataset_id, kw["sha256"])
        if k in self.rows:
            return self.rows[k], False
        img = Image(len(self.rows) + 1, dataset_id, 1, kw["object_key"], kw["file_name"],
                    kw["sha256"], kw["size_bytes"], kw["width_px"], kw["height_px"],
                    kw["split"], NOW)  # fmt: skip
        self.rows[k] = img
        return img, True


@pytest.fixture
def env() -> tuple[FakeRepo, FakeStore, list[str]]:
    calls: list[str] = []
    return FakeRepo(calls), FakeStore(calls), calls


def item(data: bytes, name: str = "foto.jpg") -> UploadItem:
    return UploadItem(name, io.BytesIO(data))


def run(coro: Any) -> Any:
    return asyncio.run(coro)


@pytest.mark.parametrize(
    "data,ext,ctype", [(jpeg(8, 6), "jpg", "image/jpeg"), (png(8, 6), "png", "image/png")]
)
def test_happy_path(env: Any, data: bytes, ext: str, ctype: str) -> None:
    repo, store, calls = env
    [res] = run(upload_images(repo, store, 1, [item(data, "../../x/Foto.JPG")]))
    sha = hashlib.sha256(data).hexdigest()
    key = f"datasets/1/{sha}.{ext}"
    assert res.created and store.objects == {key: (data, ctype)}
    assert repo.inserts == [{
        "dataset_id": 1, "object_key": key, "file_name": "Foto.JPG", "sha256": sha,
        "size_bytes": len(data), "width_px": 8, "height_px": 6, "split": None,
    }]  # fmt: skip
    assert calls.index("put") < calls.index("insert")


def test_dedup_same_dataset_and_other_dataset(env: Any) -> None:
    repo, store, _ = env
    data = jpeg()
    first = run(upload_images(repo, store, 1, [item(data, "a.jpg")]))
    again = run(upload_images(repo, store, 1, [item(data, "b.jpg")]))
    assert store.puts == 1 and len(repo.inserts) == 1
    assert not again[0].created and again[0].image == first[0].image
    other = run(upload_images(repo, store, 2, [item(data)]))
    assert other[0].created and store.puts == 2
    assert len(store.objects) == 2


def test_put_failure_means_no_insert_and_503(env: Any) -> None:
    repo, store, _ = env
    store.fail_put_at = 1
    with pytest.raises(StorageUnavailableError) as ei:
        run(upload_images(repo, store, 1, [item(jpeg())]))
    assert ei.value.status == 503 and not repo.inserts
    assert not any(s in str(ei.value) + repr(ei.value.__context__) for s in SECRETS)


def test_insert_failure_compensates(env: Any) -> None:
    repo, store, calls = env
    repo.fail_insert = True
    with pytest.raises(RuntimeError, match="db down"):
        run(upload_images(repo, store, 1, [item(jpeg())]))
    assert store.objects == {} and calls[-1] == "delete"


def test_insert_failure_with_existing_row_does_not_delete(env: Any) -> None:
    repo, store, calls = env
    data = jpeg()
    sha = hashlib.sha256(data).hexdigest()
    repo.fail_insert = True
    repo.race_row = Image(9, 1, 1, f"datasets/1/{sha}.jpg", "n", sha, 1, 1, 1, None, NOW)
    with pytest.raises(RuntimeError):
        run(upload_images(repo, store, 1, [item(data)]))
    assert "delete" not in calls and len(store.objects) == 1


def test_compensation_failure_propagates_original_and_logs_key_only(
    env: Any, caplog: pytest.LogCaptureFixture
) -> None:
    repo, store, _ = env
    repo.fail_insert = True
    store.fail_delete = True
    with caplog.at_level(logging.DEBUG), pytest.raises(RuntimeError, match="db down"):
        run(upload_images(repo, store, 1, [item(jpeg())]))
    text = caplog.text
    assert "datasets/1/" in text and "RuntimeError" in text
    assert not any(s in text for s in SECRETS)
    assert "storage:8333" not in text and "/srv/x" not in text


def test_unknown_dataset_before_any_put(env: Any) -> None:
    repo, store, _ = env
    with pytest.raises(NotFoundError):
        run(upload_images(repo, store, 99, [item(jpeg())]))
    assert store.puts == 0 and not store.objects


@pytest.mark.parametrize("bad,exc", [(b"texto", UnsupportedTypeError), (b"", InvalidImageError)])
def test_batch_with_invalid_second_writes_nothing(env: Any, bad: bytes, exc: type) -> None:
    repo, store, _ = env
    items = [item(jpeg(1, 1)), item(bad, "x.jpg"), item(png(2, 2))]
    with pytest.raises(exc) as ei:
        run(upload_images(repo, store, 1, items))
    assert ei.value.file_index == 1  # type: ignore[attr-defined]
    assert store.puts == 0 and not repo.inserts


def test_valid_batch_and_repeat(env: Any) -> None:
    repo, store, _ = env
    datas = [jpeg(1, 1), png(2, 2), jpeg(3, 3)]
    res = run(upload_images(repo, store, 1, [item(d) for d in datas]))
    assert [r.created for r in res] == [True] * 3
    assert store.puts == 3 and len(repo.inserts) == 3
    res = run(upload_images(repo, store, 1, [item(d) for d in datas]))
    assert [r.created for r in res] == [False] * 3 and store.puts == 3


def test_batch_put_failure_in_second_keeps_first_and_retry_is_safe(env: Any) -> None:
    repo, store, _ = env
    datas = [jpeg(1, 1), png(2, 2), jpeg(3, 3)]
    store.fail_put_at = 2
    with pytest.raises(StorageUnavailableError):
        run(upload_images(repo, store, 1, [item(d) for d in datas]))
    assert len(repo.inserts) == 1
    res = run(upload_images(repo, store, 1, [item(d) for d in datas]))
    assert [r.created for r in res] == [False, True, True]


def test_duplicate_inside_one_batch(env: Any) -> None:
    repo, store, _ = env
    res = run(upload_images(repo, store, 1, [item(jpeg()), item(jpeg(), "outro.jpg")]))
    assert [r.created for r in res] == [True, False] and store.puts == 1


def test_file_count_limits(env: Any) -> None:
    repo, store, _ = env
    lim = UploadLimits(max_files=1)
    with pytest.raises(TooManyFilesError):
        run(upload_images(repo, store, 1, [item(jpeg(1, 1)), item(jpeg(2, 2))], lim))
    with pytest.raises(InvalidImageError):
        run(upload_images(repo, store, 1, []))
    assert store.puts == 0


def test_too_large_reports_index(env: Any) -> None:
    from inovalytics.uploads.errors import TooLargeError

    repo, store, _ = env
    with pytest.raises(TooLargeError) as ei:
        run(upload_images(repo, store, 1, [item(jpeg()), item(jpeg(exif=500))],
                          UploadLimits(max_bytes=200)))  # fmt: skip
    assert ei.value.file_index == 1 and store.puts == 0


def test_concurrent_same_bytes(env: Any) -> None:
    repo, store, _ = env

    async def both() -> list[Any]:
        data = jpeg(5, 5)
        return await asyncio.gather(
            upload_images(repo, store, 1, [item(data, "a.jpg")]),
            upload_images(repo, store, 1, [item(data, "b.jpg")]),
        )

    a, b = run(both())
    assert sum(r[0].created for r in (a, b)) <= 1
    assert len(repo.rows) == 1 and len(store.objects) == 1
    assert a[0].image.object_key == b[0].image.object_key


def test_split_is_passed_through(env: Any) -> None:
    repo, store, _ = env
    run(upload_images(repo, store, 1, [item(jpeg())], split="val"))
    assert repo.inserts[0]["split"] == "val"


class FakeBoto:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    def put_object(self, **kw: Any) -> None:
        self.calls.append(("put", {**kw, "Body": kw["Body"].read()}))

    def delete_object(self, **kw: Any) -> None:
        self.calls.append(("delete", kw))


def test_boto3_store_arguments() -> None:
    client = FakeBoto()
    store = Boto3ObjectStore(client, "bkt")
    run(store.put("datasets/1/x.png", io.BytesIO(b"abc"), "image/png"))
    run(store.delete("datasets/1/x.png"))
    assert client.calls[0] == (
        "put",
        {"Bucket": "bkt", "Key": "datasets/1/x.png", "Body": b"abc", "ContentType": "image/png"},
    )
    assert client.calls[1] == ("delete", {"Bucket": "bkt", "Key": "datasets/1/x.png"})
    assert "ACL" not in client.calls[0][1]


def test_errors_do_not_leak(env: Any, caplog: pytest.LogCaptureFixture) -> None:
    repo, store, _ = env
    store.fail_put_at = 1
    with caplog.at_level(logging.DEBUG), pytest.raises(StorageUnavailableError) as ei:
        run(upload_images(repo, store, 1, [item(jpeg(), "/home/u/x.jpg")]))
    blob = caplog.text + str(ei.value) + repr(ei.value.__cause__) + repr(ei.value.__context__)
    for s in [*SECRETS, "storage:8333", "/srv/x", "/home/u"]:
        assert s not in blob

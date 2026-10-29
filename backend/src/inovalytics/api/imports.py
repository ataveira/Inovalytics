"""Rotas de importação COCO (F1-06c, ADR-008) e de rótulos txt (F1-07d, ADR-009): corpo JSON,
limite por rota, tudo ou nada.

Segundo (e último) módulo da camada `api` que importa `inovalytics.db`; `main.py` só registra a
rota. Reaproveita o pool preguiçoso, o mapeamento de erros de banco e o formato de corpo de erro da
rota de upload. A rota não usa S3. O corpo é lido em fluxo, com contagem de bytes, e descartado
após o parse. Mensagens são fixas, em português, sem DSN, endpoint, segredo, caminho nem trecho do
JSON enviado (as mensagens por item vêm do parser/serviço e já não ecoam o conteúdo).
"""

from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any

import asyncpg  # type: ignore[import-untyped]
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from starlette.requests import ClientDisconnect

from inovalytics.api.config import Settings
from inovalytics.api.images import (
    _DB_ERRORS,
    DB_ACQUIRE_TIMEOUT,
    DbBusyError,
    DbProvider,
    DbUnavailableError,
    RequestBodyLimitMiddleware,
    _parse_dataset_id,
    error_body,
    error_response,
)
from inovalytics.db import repository as repo
from inovalytics.imports.coco_parser import CocoLimits, parse_coco
from inovalytics.imports.coco_service import ImportReport, import_coco
from inovalytics.imports.errors import ImportRejectedError, ItemError
from inovalytics.imports.import_core import ImportReport as TxtReport
from inovalytics.imports.names_yaml import TxtLimits
from inovalytics.imports.txt_labels_parser import parse_txt_labels
from inovalytics.imports.txt_labels_service import import_txt_labels

log = logging.getLogger(__name__)

_PATH_RE = re.compile(r"^/datasets/[^/]+/imports/coco/?$")
TXT_ROUTE = "/datasets/{dataset_id}/imports/yolo-txt"  # único lugar com o nome do formato
_TXT_PATH_RE = re.compile("^" + TXT_ROUTE.replace("{dataset_id}", "[^/]+") + "/?$")
_TRUE = frozenset({"true", "1"})
_FALSE = frozenset({"false", "0"})
MAX_MESSAGE_CHARS = 300  # defesa em profundidade: nenhuma mensagem de item passa disso


class ConnCocoRepository:
    """Liga as funções do repositório a uma conexão do pool (satisfaz `CocoRepository`)."""

    def __init__(self, conn: Any) -> None:
        self._conn = conn

    async def get_dataset(self, dataset_id: int) -> repo.Dataset:
        return await repo.get_dataset(self._conn, dataset_id)

    async def list_images_for_import(self, dataset_id: int, limit: int) -> list[repo.ImportImage]:
        return await repo.list_images_for_import(self._conn, dataset_id, limit)

    async def list_classes(self, project_id: int) -> list[repo.LabelClass]:
        return await repo.list_classes(self._conn, project_id)

    async def list_annotations_for_images(self, image_ids: Sequence[int]) -> list[repo.Annotation]:
        return await repo.list_annotations_for_images(self._conn, image_ids)

    def transaction(self) -> AbstractAsyncContextManager[Any]:
        return self._conn.transaction()  # type: ignore[no-any-return]

    async def lock_project_for_import(self, project_id: int) -> None:
        await repo.lock_project_for_import(self._conn, project_id)

    async def create_class(self, project_id: int, name: str, position: int) -> repo.LabelClass:
        return await repo.create_class(self._conn, project_id, name, position)

    async def insert_annotation_batch(
        self, project_id: int, rows: Sequence[tuple[int, repo.NewAnnotation]]
    ) -> int:
        return await repo.insert_annotation_batch(self._conn, project_id, rows)


def limits_from_settings(s: Settings) -> CocoLimits:
    return CocoLimits(
        max_json_bytes=s.import_max_json_bytes,
        max_images=s.import_max_images,
        max_annotations=s.import_max_annotations,
        max_categories=s.import_max_categories,
        max_depth=s.import_max_depth,
        max_errors=s.import_max_errors,
        max_dimension=s.upload_max_dimension,
    )


def _item_json(e: ItemError) -> dict[str, Any]:
    out: dict[str, Any] = {
        "code": e.code,
        "kind": e.kind,
        "id": e.id,
        "index": e.index,
        "message": e.message[:MAX_MESSAGE_CHARS],
    }
    # Omitidos quando nulos: a resposta COCO continua idêntica byte a byte.
    if e.file is not None:
        out["arquivo"] = e.file
    if e.line is not None:
        out["linha"] = e.line
    return out


def _report_json(r: ImportReport) -> dict[str, Any]:
    return {
        "status": r.status,
        "dataset_id": r.dataset_id,
        "imagens": {"declaradas": r.images_declared, "casadas": r.images_matched},
        "classes": {
            "declaradas": r.classes_declared,
            "criadas": r.classes_created,
            "existentes": r.classes_existing,
        },
        "anotacoes": {
            "no_arquivo": r.annotations_in_file,
            "importadas": r.annotations_imported,
            "ja_existentes": r.annotations_already_existing,
            "ignoradas_crowd": r.crowd_ignored,
        },
        "segmentacoes_ignoradas": r.segmentations_ignored,
        "avisos": [_item_json(w) for w in r.warnings],
        "avisos_total": r.warnings_total,
        "avisos_truncados": r.warnings_truncated,
    }


def _txt_report_json(r: TxtReport) -> dict[str, Any]:
    return {
        "status": r.status,
        "dataset_id": r.dataset_id,
        "imagens": {"declaradas": r.images_declared, "casadas": r.images_matched},
        "classes": {
            "declaradas": r.classes_declared,
            "criadas": r.classes_created,
            "existentes": r.classes_existing,
        },
        "anotacoes": {
            "no_arquivo": r.annotations_in_file,
            "importadas": r.annotations_imported,
            "ja_existentes": r.annotations_already_existing,
        },
        "imagens_sem_caixas": r.images_without_boxes,
        "avisos": [_item_json(w) for w in r.warnings],
        "avisos_total": r.warnings_total,
        "avisos_truncados": r.warnings_truncated,
    }


def _invalid_response(exc: ImportRejectedError) -> JSONResponse:
    # Conflito de estado (as imagens mudaram entre o plano e o bloqueio), não defeito do arquivo:
    # 409 mantendo o código `dataset_alterado`; o cliente repete a mesma chamada.
    changed = any(e.code == "dataset_alterado" for e in exc.errors)
    body = error_body(
        f"Importação recusada: {exc.total} erro(s) no arquivo.",
        "dataset_alterado" if changed else "importacao_invalida",
    )
    body["erros"] = [_item_json(e) for e in exc.errors]
    body["erros_total"] = exc.total
    body["erros_truncados"] = exc.truncated
    return JSONResponse(status_code=409 if changed else 422, content=body)


def _parse_flag(request: Request, name: str, default: bool) -> bool | None:
    """Valor booleano estrito da query (true/false/1/0); `None` se inválido ou repetido."""
    values = request.query_params.getlist(name)
    if not values:
        return default
    if len(values) > 1:
        return None
    v = values[0].strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    return None


def _content_type_ok(header: str | None) -> bool:
    if not header:
        return False
    media, *params = header.split(";")
    if media.strip().lower() != "application/json":
        return False
    for p in params:
        key, _, value = p.partition("=")
        if key.strip().lower() == "charset" and value.strip().strip('"').lower() != "utf-8":
            return False
    return True


def _too_large() -> JSONResponse:
    return error_response(
        413, "Corpo da requisição acima do tamanho máximo permitido.", "requisicao_grande_demais"
    )


async def _read_body(request: Request, limit: int) -> bytes | None:
    """Lê o corpo em fluxo; `None` se passar de `limit` (aborta antes de acumular o resto)."""
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > limit:
            return None
        chunks.append(chunk)
    return b"".join(chunks)


def _db_down() -> JSONResponse:
    return error_response(503, "Banco de dados indisponível.", "banco_indisponivel")


async def _with_repo(provider: DbProvider, fn: Any) -> Any:
    """Executa `fn(repo)` numa conexão do pool; devolve o resultado ou uma `JSONResponse`."""
    try:
        async with AsyncExitStack() as stack:
            try:
                async with asyncio.timeout(DB_ACQUIRE_TIMEOUT) as deadline:
                    conn = await stack.enter_async_context(provider.acquire())
            except Exception as exc:
                if deadline.expired():  # nosso prazo, não um TimeoutError do driver
                    log.error("timeout ao obter conexão do banco")
                    raise DbBusyError from None
                log.error("banco indisponível: %s", type(exc).__name__)
                raise DbUnavailableError from None
            return await fn(ConnCocoRepository(conn))
    except DbBusyError:
        return error_response(
            503, "Banco de dados ocupado; tente novamente.", "banco_timeout_conexao"
        )
    except DbUnavailableError:
        return _db_down()
    except ImportRejectedError as exc:
        return _invalid_response(exc)
    except repo.NotFoundError:
        return error_response(404, "Dataset não encontrado.", "dataset_nao_encontrado")
    except repo.LockTimeoutError:
        log.error("bloqueio do projeto não obtido na importação")
        return error_response(
            503,
            "Outra importação do projeto está em andamento; tente novamente.",
            "importacao_em_andamento",
        )
    except (repo.RepositoryError, asyncpg.IntegrityConstraintViolationError) as exc:
        # Violação de restrição não é indisponibilidade: só o tipo vai ao log (sem mensagem).
        log.error("violação de integridade na importação: %s", type(exc).__name__)
        return error_response(
            409, "Conflito ao registrar as anotações no banco.", "conflito_integridade"
        )
    except _DB_ERRORS as exc:
        log.error("erro de banco na importação: %s", type(exc).__name__)
        return _db_down()
    except Exception as exc:
        log.error("erro inesperado na importação: %s", type(exc).__name__)
        return error_response(500, "Erro interno no servidor.", "erro_interno")


async def _receive(
    request: Request, dataset_id: str, provider: DbProvider, limit: int, what: str
) -> JSONResponse | tuple[int, bool, bool, bytes]:
    """Valida id, query, tipo e tamanho, confere o dataset e lê o corpo em fluxo.

    Devolve uma resposta de erro pronta ou `(dataset_id, dry_run, create_classes, corpo)`.
    """
    ds = _parse_dataset_id(dataset_id)
    if ds is None:
        return error_response(422, "Identificador de dataset inválido.", "dataset_id_invalido")
    dry_run = _parse_flag(request, "dry_run", False)
    if dry_run is None:
        return error_response(422, "Parâmetro dry_run inválido.", "query_invalida")
    create_classes = _parse_flag(request, "create_classes", True)
    if create_classes is None:
        return error_response(422, "Parâmetro create_classes inválido.", "query_invalida")
    if not _content_type_ok(request.headers.get("content-type")):
        return error_response(
            415, f"Envie {what} como application/json (UTF-8).", "tipo_nao_suportado"
        )
    declared = request.headers.get("content-length")
    if (
        declared is not None
        and declared.isascii()  # '²' passa em isdigit() e quebra int()
        and declared.isdigit()
        and int(declared) > limit
    ):
        return _too_large()

    # Dataset inexistente é recusado antes de ler o corpo grande (conexão solta em seguida,
    # para um cliente lento não segurar o pool).
    async def check(r: ConnCocoRepository) -> None:
        await r.get_dataset(ds)

    failed = await _with_repo(provider, check)
    if isinstance(failed, JSONResponse):
        return failed
    try:
        body = await _read_body(request, limit)
    except ClientDisconnect:
        return error_response(400, "Requisição interrompida.", "requisicao_interrompida")
    if body is None:
        return _too_large()
    return ds, dry_run, create_classes, body


def register_import(app: FastAPI, settings: Settings, provider: DbProvider) -> None:
    """Acrescenta `POST /datasets/{dataset_id}/imports/coco` e o limite de corpo da rota."""
    limits = limits_from_settings(settings)
    app.add_middleware(RequestBodyLimitMiddleware, limit=limits.max_json_bytes, path=_PATH_RE)

    @app.post("/datasets/{dataset_id}/imports/coco")
    async def import_coco_route(dataset_id: str, request: Request) -> JSONResponse:
        got = await _receive(request, dataset_id, provider, limits.max_json_bytes, "o arquivo COCO")
        if isinstance(got, JSONResponse):
            return got
        ds, dry_run, create_classes, body = got
        try:
            parsed = await asyncio.to_thread(parse_coco, body, limits)
        except ImportRejectedError as exc:
            return _invalid_response(exc)
        except Exception as exc:  # o parser promete não vazar; defesa extra
            log.error("erro inesperado no parser COCO: %s", type(exc).__name__)
            return error_response(500, "Erro interno no servidor.", "erro_interno")
        finally:
            del body

        async def run(r: ConnCocoRepository) -> ImportReport:
            return await import_coco(
                r, ds, parsed, create_classes=create_classes, dry_run=dry_run, limits=limits
            )

        result = await _with_repo(provider, run)
        if isinstance(result, JSONResponse):
            return result
        status = 201 if result.status == "importado" else 200
        return JSONResponse(status_code=status, content=_report_json(result))


def txt_limits_from_settings(s: Settings) -> TxtLimits:
    return TxtLimits(
        max_json_bytes=s.import_max_json_bytes,
        max_images=s.import_max_images,
        max_annotations=s.import_max_annotations,
        max_categories=s.import_max_categories,
        max_errors=s.import_max_errors,
        max_yaml_bytes=s.import_max_yaml_bytes,
    )


def register_import_txt(app: FastAPI, settings: Settings, provider: DbProvider) -> None:
    """Acrescenta a rota de rótulos txt (ADR-009) e uma segunda instância do limite de corpo."""
    limits = txt_limits_from_settings(settings)
    app.add_middleware(RequestBodyLimitMiddleware, limit=limits.max_json_bytes, path=_TXT_PATH_RE)

    @app.post(TXT_ROUTE)
    async def import_txt_route(dataset_id: str, request: Request) -> JSONResponse:
        got = await _receive(
            request, dataset_id, provider, limits.max_json_bytes, "o corpo dos rótulos"
        )
        if isinstance(got, JSONResponse):
            return got
        ds, dry_run, create_classes, body = got
        try:
            parsed = await asyncio.to_thread(parse_txt_labels, body, limits, collect_errors=True)
        except ImportRejectedError as exc:
            return _invalid_response(exc)
        except Exception as exc:  # o parser promete não vazar; defesa extra
            log.error("erro inesperado no parser de rótulos: %s", type(exc).__name__)
            return error_response(500, "Erro interno no servidor.", "erro_interno")
        finally:
            del body

        async def run(r: ConnCocoRepository) -> TxtReport:
            return await import_txt_labels(
                r, ds, parsed, create_classes=create_classes, dry_run=dry_run, limits=limits
            )

        result = await _with_repo(provider, run)
        if isinstance(result, JSONResponse):
            return result
        status = 201 if result.status == "importado" else 200
        return JSONResponse(status_code=status, content=_txt_report_json(result))

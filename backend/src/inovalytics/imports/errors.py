"""Erros por item das importações (COCO e rótulos txt). Mensagens fixas em português, sem eco do
conteúdo do usuário além de nomes truncados."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class ItemError:
    """Um defeito localizado. `kind` agrupa (arquivo, imagem, categoria, anotacao); `index` é a
    posição na lista de origem e `id` o identificador declarado (ambos podem faltar)."""

    code: str
    kind: str
    id: int | None
    index: int | None
    message: str
    file: str | None = None  # arquivo de rótulo (formatos com vários arquivos)
    line: int | None = None  # linha 1-based dentro do arquivo
    # Posição do arquivo de origem, para ordenar itens que têm `index` relativo ao arquivo (VOC).
    # Não é exposto no relatório; 0 nos formatos em que `index` já é global.
    group: int = 0

    def sort_key(self) -> tuple[str, int, int, int]:
        return (
            self.kind,
            self.group,
            -1 if self.index is None else self.index,
            -1 if self.line is None else self.line,
        )


class ImportRejectedError(Exception):
    """Importação recusada. `errors` vem ordenada por `(kind, index, line)` e limitada; `total` é
    o número real de defeitos e `truncated` indica que a lista foi cortada."""

    def __init__(self, errors: list[ItemError], total: int | None = None) -> None:
        errs = sorted(errors, key=ItemError.sort_key)
        self.total = len(errs) if total is None else total
        self.errors: tuple[ItemError, ...] = tuple(errs)
        self.truncated = self.total > len(errs)
        first = errs[0].message if errs else "importação inválida"
        super().__init__(f"importação recusada ({self.total} erro(s)): {first}")


class CocoImportError(ImportRejectedError):
    """Recusa de uma importação COCO (a rota captura a base `ImportRejectedError`)."""


_MAX_REPORTED_ID = 2**63 - 1

LazyText = str | Callable[[], str]


class ErrorCollector:
    """Acumula defeitos guardando no máximo `cap` por tipo (a memória não cresce com o arquivo)."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._by_kind: dict[str, list[ItemError]] = {}
        self.total = 0

    def add(
        self,
        code: str,
        kind: str,
        id_: int | None,
        index: int | None,
        message: LazyText,
        *,
        file: LazyText | None = None,
        line: int | None = None,
        group: int = 0,
    ) -> None:
        """Registra um defeito. `message` e `file` podem ser funções sem argumentos: só são
        chamadas se o item for guardado (ADR-008/009: nenhum custo por item com o coletor cheio)."""
        self.total += 1
        if id_ is not None and not -_MAX_REPORTED_ID <= id_ <= _MAX_REPORTED_ID:
            id_ = None  # inteiro gigante do arquivo não é repetido no relatório (amplificação)
        bucket = self._by_kind.setdefault(kind, [])
        if len(bucket) < self._cap:
            msg = message if isinstance(message, str) else message()
            fname = file if file is None or isinstance(file, str) else file()
            bucket.append(ItemError(code, kind, id_, index, msg, fname, line, group))

    def absorb(self, items: Sequence[ItemError], total: int) -> None:
        """Incorpora defeitos já coletados por outra etapa (o `total` real pode ser maior)."""
        self.total += total
        for e in items:
            bucket = self._by_kind.setdefault(e.kind, [])
            if len(bucket) < self._cap:
                bucket.append(e)

    def items(self) -> list[ItemError]:
        merged = [e for bucket in self._by_kind.values() for e in bucket]
        merged.sort(key=ItemError.sort_key)
        return merged[: self._cap]

    def raise_if_any(self, exc: type[ImportRejectedError] = CocoImportError) -> None:
        if self.total:
            raise exc(self.items(), self.total)

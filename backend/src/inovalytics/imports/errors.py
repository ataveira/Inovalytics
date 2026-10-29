"""Erros por item das importações. Mensagens fixas em português, sem eco do conteúdo do usuário."""

from __future__ import annotations

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

    def sort_key(self) -> tuple[str, int]:
        return (self.kind, -1 if self.index is None else self.index)


class CocoImportError(Exception):
    """Importação recusada. `errors` vem ordenada por `(kind, index)` e limitada; `total` é o
    número real de defeitos e `truncated` indica que a lista foi cortada."""

    def __init__(self, errors: list[ItemError], total: int | None = None) -> None:
        errs = sorted(errors, key=ItemError.sort_key)
        self.total = len(errs) if total is None else total
        self.errors: tuple[ItemError, ...] = tuple(errs)
        self.truncated = self.total > len(errs)
        first = errs[0].message if errs else "importação inválida"
        super().__init__(f"importação recusada ({self.total} erro(s)): {first}")


class ErrorCollector:
    """Acumula defeitos guardando no máximo `cap` por tipo (a memória não cresce com o arquivo)."""

    def __init__(self, cap: int) -> None:
        self._cap = cap
        self._by_kind: dict[str, list[ItemError]] = {}
        self.total = 0

    def add(self, code: str, kind: str, id_: int | None, index: int | None, message: str) -> None:
        self.total += 1
        bucket = self._by_kind.setdefault(kind, [])
        if len(bucket) < self._cap:
            bucket.append(ItemError(code, kind, id_, index, message))

    def items(self) -> list[ItemError]:
        merged = [e for bucket in self._by_kind.values() for e in bucket]
        merged.sort(key=ItemError.sort_key)
        return merged[: self._cap]

    def raise_if_any(self) -> None:
        if self.total:
            raise CocoImportError(self.items(), self.total)

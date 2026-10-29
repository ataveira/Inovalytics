"""Medição de tempo em thread com `join(timeout)` (um laço lento não trava a suíte)."""

import threading
import time
from collections.abc import Callable
from typing import Any


def timed(fn: Callable[[], Any], timeout: float) -> tuple[float, Any]:
    """Executa `fn` numa thread e devolve (segundos, resultado ou exceção); falha se passar de
    `timeout` (a thread é daemon, então não segura o processo)."""
    box: dict[str, Any] = {}

    def work() -> None:
        t0 = time.perf_counter()
        try:
            box["result"] = fn()
        except BaseException as exc:  # noqa: BLE001 - devolvido ao chamador
            box["result"] = exc
        box["elapsed"] = time.perf_counter() - t0

    th = threading.Thread(target=work, daemon=True)
    th.start()
    th.join(timeout)
    assert not th.is_alive(), f"excedeu {timeout}s"
    return box["elapsed"], box["result"]

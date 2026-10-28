from pathlib import Path

import pytest


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


@pytest.fixture
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Qualquer tentativa de abrir socket falha: prova que o verificador roda offline."""
    import socket

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("acesso à rede proibido neste teste")

    monkeypatch.setattr(socket, "socket", _blocked)

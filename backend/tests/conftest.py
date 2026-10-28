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


@pytest.fixture
def no_external_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Como `no_network`, mas deixa criar sockets locais (socketpair).

    O event loop do asyncio (e portanto o TestClient/lifespan do FastAPI) cria um socketpair
    interno; bloquear `socket.socket` por inteiro o quebra. Aqui só conexão, envio e resolução
    de nomes falham.
    """
    import socket

    def _blocked(*args: object, **kwargs: object) -> None:
        raise AssertionError("acesso à rede proibido neste teste")

    for name in ("connect", "connect_ex", "sendto"):
        monkeypatch.setattr(socket.socket, name, _blocked)
    for name in ("create_connection", "getaddrinfo", "gethostbyname", "gethostbyname_ex"):
        monkeypatch.setattr(socket, name, _blocked)

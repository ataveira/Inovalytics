"""F1-08d critério 31: XXE de ponta a ponta pela rota (sem Docker).

Sem a fixture `no_external_network` de propósito: aqui existe um servidor TCP local de teste que
registra qualquer contato; se o importador tentasse resolver uma entidade de rede, o contador
deixaria de ser zero. Um arquivo sentinela fora do alcance (conteúdo único) nunca pode aparecer na
resposta nem nos logs, e nenhuma chamada de abertura de arquivo pode citá-lo.
"""

import builtins
import json
import logging
import os
import socket
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from test_api_coco_import import Env
from test_voc_parser import ann, obj

URL = "/datasets/1/imports/pascal-voc"
JSON_H = {"Content-Type": "application/json"}
LEAK_MARK = "CONTEUDO-UNICO-DO-ARQUIVO-8f3a1c"


class TripwireServer:
    """Servidor TCP local que falha o teste se alguém se conectar."""

    def __init__(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.listen(5)
        self.sock.settimeout(0.1)
        self.hits = 0
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    @property
    def port(self) -> int:
        return int(self.sock.getsockname()[1])

    def _serve(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _ = self.sock.accept()
            except (TimeoutError, OSError):
                continue
            self.hits += 1
            conn.close()

    def close(self) -> None:
        self._stop.set()
        self._thread.join(2)
        self.sock.close()


@pytest.fixture
def tripwire() -> Iterator[TripwireServer]:
    s = TripwireServer()
    yield s
    s.close()


@pytest.fixture
def sentinel(tmp_path: Path) -> Path:
    p = tmp_path / "segredo-fora-do-alcance.txt"
    p.write_text(LEAK_MARK, encoding="utf-8")
    return p


def _hostile(sentinel: Path, port: int) -> dict[str, str]:
    file_uri = sentinel.as_uri()
    net = f"http://127.0.0.1:{port}/x"
    return {
        "arquivo.xml": f'<!DOCTYPE annotation [<!ENTITY x SYSTEM "{file_uri}">]>'
        f"\n<annotation><filename>&x;</filename></annotation>",
        "rede.xml": f'<?xml version="1.0"?>\n<!DOCTYPE annotation [<!ENTITY x SYSTEM "{net}">]>'
        f"\n<annotation><object><name>&x;</name></object></annotation>",
        "parametrica.xml": f'<!DOCTYPE annotation [<!ENTITY % p SYSTEM "{net}"> %p;]>'
        "\n<annotation/>",
        "dtd_externo.xml": f'<!DOCTYPE annotation SYSTEM "{net}.dtd">\n<annotation/>',
        "xinclude.xml": '<annotation xmlns:xi="http://www.w3.org/2001/XInclude">\n'
        f'<xi:include href="{file_uri}" parse="text"/></annotation>',
    }


def test_xxe_end_to_end_is_refused_without_reading_files_or_touching_the_network(
    monkeypatch: pytest.MonkeyPatch,
    tripwire: TripwireServer,
    sentinel: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    opened: list[str] = []
    real_open, real_os_open = builtins.open, os.open

    def spy_open(file: Any, *a: Any, **k: Any) -> Any:
        opened.append(str(file))
        return real_open(file, *a, **k)

    def spy_os_open(path: Any, *a: Any, **k: Any) -> Any:
        opened.append(str(path))
        return real_os_open(path, *a, **k)

    env = Env(monkeypatch)
    files = _hostile(sentinel, tripwire.port)
    files["ok.xml"] = ann(obj("pessoa"))  # imagem inexistente: erro à parte, não importa aqui
    data = json.dumps({"annotations": files}).encode()
    with caplog.at_level(logging.DEBUG), env.client() as c:
        monkeypatch.setattr(builtins, "open", spy_open)
        monkeypatch.setattr(os, "open", spy_os_open)
        r = c.post(URL, content=data, headers=JSON_H)
        monkeypatch.undo()
    j = r.json()
    assert r.status_code == 422 and j["code"] == "importacao_invalida"
    by_file = {e["arquivo"]: e for e in j["erros"] if e["kind"] == "anotacao" and "arquivo" in e}
    for name in ("arquivo.xml", "rede.xml", "parametrica.xml", "dtd_externo.xml"):
        assert by_file[name]["code"] == "doctype_proibido", name
        assert by_file[name]["linha"] >= 1
    assert "xinclude.xml" in by_file  # elemento desconhecido tolerado; falha só por casamento
    assert by_file["xinclude.xml"]["code"] == "imagem_nao_encontrada"
    assert env.repo.writes() == [] and "BEGIN" not in env.repo.calls
    assert tripwire.hits == 0
    assert not [p for p in opened if sentinel.name in p]
    blob = r.text + caplog.text
    assert LEAK_MARK not in blob and sentinel.name not in blob
    assert str(tripwire.port) not in blob and "127.0.0.1" not in blob
    for err in j["erros"]:
        assert len(err["message"]) <= 200
        assert "ENTITY" not in err["message"] and "SYSTEM" not in err["message"]


def test_xxe_alone_in_a_batch_leaves_everything_untouched(
    monkeypatch: pytest.MonkeyPatch, tripwire: TripwireServer, sentinel: Path
) -> None:
    env = Env(monkeypatch)
    f = {
        "a.xml": f'<!DOCTYPE annotation [<!ENTITY x SYSTEM "http://127.0.0.1:{tripwire.port}/">]>'
        "<annotation><object><name>&x;</name></object></annotation>",
    }
    with env.client() as c:
        r = c.post(URL, content=json.dumps({"annotations": f}).encode(), headers=JSON_H)
    assert r.status_code == 422
    assert [e["code"] for e in r.json()["erros"]] == ["doctype_proibido"]
    assert env.repo.classes == [] and env.repo.annotations == [] and tripwire.hits == 0
    assert env.repo.writes() == [] and LEAK_MARK not in r.text


def test_tripwire_really_detects_a_connection(tripwire: TripwireServer) -> None:
    """Valida o próprio instrumento: uma conexão real é contada."""
    with socket.create_connection(("127.0.0.1", tripwire.port), timeout=2):
        pass
    for _ in range(50):
        if tripwire.hits:
            break
        threading.Event().wait(0.05)
    assert tripwire.hits == 1

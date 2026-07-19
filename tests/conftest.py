from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    def fail_connect(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("Testes nao podem acessar rede real.")

    monkeypatch.setattr(socket, "create_connection", fail_connect)
    monkeypatch.setattr(socket.socket, "connect", fail_connect)
    yield

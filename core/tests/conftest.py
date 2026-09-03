# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Shared fixtures: a built panel on disk, an environment, an HTTP client and the flows.

Fixtures compartilhadas: painel construído em disco, ambiente, cliente HTTP e os fluxos.
"""

from pathlib import Path

import pytest

from iphub.ambiente import Ambiente
from iphub.app import criar_app

INDEX_HTML = '<!doctype html><title>Tuya IP Hub</title><div id="root"></div>\n'
APP_JS = 'console.log("painel");\n'
SENHA = "senha-de-teste"


@pytest.fixture
def dir_painel(tmp_path: Path) -> Path:
    painel = tmp_path / "painel"
    (painel / "assets").mkdir(parents=True)
    (painel / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (painel / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")
    return painel


@pytest.fixture
def amb(tmp_path: Path, dir_painel: Path) -> Ambiente:
    return Ambiente(bind="127.0.0.1", porta=8080, dir_data=tmp_path / "data", dir_painel=dir_painel)


@pytest.fixture
async def cliente(aiohttp_client, amb: Ambiente):
    return await aiohttp_client(criar_app(amb))


@pytest.fixture
def fabrica_cliente(aiohttp_client, amb: Ambiente):
    """Builds a client over an app with pieces of its own: a clock, a configuration.

    Constrói um cliente sobre um app com peças próprias: um relógio, uma configuração.
    """

    async def criar(**pecas):
        return await aiohttp_client(criar_app(amb, **pecas))

    return criar


@pytest.fixture
def senha() -> str:
    return SENHA


@pytest.fixture
def bearer():
    def montar(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    return montar


@pytest.fixture
def posse(senha: str):
    """Takes ownership over a client already built and returns the session token.

    Toma posse sobre um cliente já construído e devolve o token de sessão.
    """

    async def tomar(cliente, com_senha: str = "") -> str:
        resposta = await cliente.post("/api/posse", json={"senha": com_senha or senha})
        assert resposta.status == 200, await resposta.text()
        return (await resposta.json())["token"]

    return tomar


@pytest.fixture
def relogio():
    """A clock the test moves by hand, for session validity and for the rate limit.

    Um relógio que o teste move na mão, para validade de sessão e para o limite.
    """

    class Relogio:
        def __init__(self) -> None:
            self.agora = 1_700_000_000.0

        def __call__(self) -> float:
            return self.agora

        def avancar(self, segundos: float) -> None:
            self.agora += segundos

    return Relogio()

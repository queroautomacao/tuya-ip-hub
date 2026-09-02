# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The container healthcheck (python -m iphub.saude) is the milestone 0 exit gate.

O healthcheck do container (python -m iphub.saude) é o portão de saída do marco 0.
"""

import asyncio
import socket

import pytest
from aiohttp import web

from iphub.app import criar_app
from iphub.saude import alvo_da_sonda, main, verificar


@pytest.mark.parametrize(
    ("bind", "esperado"),
    [
        ("0.0.0.0", "http://127.0.0.1:8080/health"),
        ("", "http://127.0.0.1:8080/health"),
        ("::", "http://127.0.0.1:8080/health"),
        ("127.0.0.1", "http://127.0.0.1:8080/health"),
        ("192.0.2.10", "http://192.0.2.10:8080/health"),
        ("::1", "http://[::1]:8080/health"),
    ],
)
def test_a_sonda_segue_o_endereco_em_que_o_daemon_escuta(bind, esperado):
    assert alvo_da_sonda(bind, 8080) == esperado


async def test_daemon_de_pe_e_saudavel(aiohttp_server, amb):
    servidor = await aiohttp_server(criar_app(amb))
    assert await asyncio.to_thread(verificar, f"http://127.0.0.1:{servidor.port}/health")


async def test_rota_errada_nao_e_saudavel(aiohttp_server, amb):
    servidor = await aiohttp_server(criar_app(amb))
    assert not await asyncio.to_thread(verificar, f"http://127.0.0.1:{servidor.port}/nao-existe")


@pytest.mark.parametrize(
    ("status", "corpo"),
    [
        (200, '{"ok": false, "code": "x"}'),
        # Why: 201 is the only way to reach the status check; urllib raises on 4xx and 5xx.
        # Por que: 201 é o único jeito de chegar na checagem de status; urllib estoura em 4xx e 5xx.
        (201, '{"ok": true}'),
        (202, '{"ok": true}'),
        (503, '{"ok": true}'),
        (200, "nao e json"),
        (200, "[]"),
        (200, '{"ok": "true"}'),
    ],
)
async def test_corpo_que_nao_confirma_nao_e_saudavel(aiohttp_server, status, corpo):
    async def falso(request):
        return web.Response(status=status, text=corpo, content_type="application/json")

    app = web.Application()
    app.router.add_get("/health", falso)
    servidor = await aiohttp_server(app)
    assert not await asyncio.to_thread(verificar, f"http://127.0.0.1:{servidor.port}/health")


def test_porta_fechada_nao_e_saudavel():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        porta = sock.getsockname()[1]
    assert not verificar(f"http://127.0.0.1:{porta}/health", timeout_s=1)


async def test_main_devolve_0_com_o_daemon_de_pe_e_1_sem_ele(aiohttp_server, amb, monkeypatch):
    servidor = await aiohttp_server(criar_app(amb))
    monkeypatch.setenv("IPHUB_PORTA", str(servidor.port))
    assert await asyncio.to_thread(main) == 0
    await servidor.close()
    assert await asyncio.to_thread(main) == 1


async def test_main_respeita_o_iphub_bind(aiohttp_server, amb, monkeypatch):
    servidor = await aiohttp_server(criar_app(amb))
    monkeypatch.setenv("IPHUB_PORTA", str(servidor.port))
    monkeypatch.setenv("IPHUB_BIND", "127.0.0.1")
    assert await asyncio.to_thread(main) == 0
    monkeypatch.setenv("IPHUB_BIND", "192.0.2.10")
    assert await asyncio.to_thread(main) == 1

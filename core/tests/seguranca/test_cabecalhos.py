# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
import asyncio
import json
import logging
from dataclasses import replace

import pytest
from aiohttp import web

from iphub.app import criar_app
from iphub.portao import CABECALHOS, SERVIDOR


def _confere(resposta):
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome
    assert resposta.headers.get("Server") == SERVIDOR


@pytest.mark.parametrize(
    ("metodo", "caminho", "host", "status"),
    [
        ("GET", "/health", None, 200),
        ("GET", "/", None, 200),
        ("GET", "/assets/app.js", None, 200),
        ("GET", "/nao-existe", None, 404),
        ("GET", "/assets/nao-existe.js", None, 404),
        ("POST", "/health", None, 405),
        ("GET", "/health", "evil.example.com", 421),
        ("GET", "/nao-existe", "evil.example.com", 421),
        ("GET", "/api/estado", None, 200),
        ("POST", "/api/posse", None, 400),
        ("POST", "/api/entrar", None, 400),
        ("POST", "/api/sair", None, 401),
        ("GET", "/api/sessao", None, 401),
        ("POST", "/api/senha", None, 401),
        ("POST", "/api/instalacao", None, 401),
        ("POST", "/api/reiniciar", None, 401),
        ("GET", "/api/atualizacao", None, 401),
        ("GET", "/api/estado", "evil.example.com", 421),
    ],
)
async def test_toda_resposta_carrega_os_cabecalhos(cliente, metodo, caminho, host, status):
    cabecalhos = {"Host": host} if host else {}
    resposta = await cliente.request(metodo, caminho, headers=cabecalhos)
    assert resposta.status == status
    _confere(resposta)


async def test_503_do_painel_ausente_tambem(aiohttp_client, amb, tmp_path):
    cliente = await aiohttp_client(criar_app(replace(amb, dir_painel=tmp_path / "vazio")))
    resposta = await cliente.get("/")
    assert resposta.status == 503
    _confere(resposta)


async def test_head_tambem(cliente):
    resposta = await cliente.head("/health")
    assert resposta.status == 200
    _confere(resposta)


@pytest.fixture
async def cliente_com_falhas(aiohttp_client, amb):
    app = criar_app(amb)

    async def quebra(request):
        raise RuntimeError("boom")

    async def proibido_em_json(request):
        raise web.HTTPForbidden(
            text=json.dumps({"ok": False, "code": "auth_pendente"}), content_type="application/json"
        )

    app.router.add_get("/quebra/{resto:(?s:.*)}", quebra)
    app.router.add_get("/proibido", proibido_em_json)
    return await aiohttp_client(app)


async def test_excecao_nao_prevista_vira_500_json_com_cabecalhos(cliente_com_falhas, caplog):
    caplog.set_level(logging.ERROR, logger="iphub.portao")
    resposta = await cliente_com_falhas.get("/quebra/x")
    assert resposta.status == 500
    assert await resposta.json() == {"ok": False, "code": "erro_interno"}
    _confere(resposta)
    assert "boom" not in await resposta.text()
    assert "unhandled error on GET /quebra/x" in caplog.text
    assert "RuntimeError: boom" in caplog.text


async def test_excecao_http_ja_em_json_atravessa_com_cabecalhos(cliente_com_falhas):
    resposta = await cliente_com_falhas.get("/proibido")
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "auth_pendente"}
    _confere(resposta)


@pytest.mark.parametrize(
    ("metodo", "caminho"),
    [
        ("GET", "/health"),
        ("GET", "/"),
        ("GET", "/assets/app.js"),
        # Why: a path with no route and a method with no route used to escape to aiohttp,
        # which answered in plain text, with its own Server banner and no headers.
        # Por que: um caminho sem rota e um método sem rota escapavam para o aiohttp, que
        # respondia em texto puro, com o banner de Server dele e sem cabeçalho nenhum.
        ("GET", "/nao-existe"),
        ("GET", "/health/x"),
        ("POST", "/health"),
        ("DELETE", "/"),
        ("GET", "/a%0Ab"),
        ("GET", "/a%00b"),
        ("GET", "/api/estado"),
        ("POST", "/api/posse"),
    ],
)
async def test_expect_desconhecido_e_417_json_com_cabecalhos(cliente, metodo, caminho):
    resposta = await cliente.request(metodo, caminho, headers={"Expect": "foo"})
    assert resposta.status == 417
    assert await resposta.json() == {"ok": False, "code": "erro_http"}
    _confere(resposta)


async def test_expect_com_host_hostil_e_421_antes_do_417(cliente):
    for expect in ("foo", "100-continue"):
        resposta = await cliente.get(
            "/health", headers={"Host": "evil.example.com", "Expect": expect}
        )
        assert resposta.status == 421, expect
        assert await resposta.json() == {"ok": False, "code": "host_nao_permitido"}
        _confere(resposta)


async def test_log_do_500_nao_aceita_quebra_de_linha_do_cliente(cliente_com_falhas, caplog):
    caplog.set_level(logging.ERROR, logger="iphub.portao")
    resposta = await cliente_com_falhas.get("/quebra/a%0AERROR%20forjado")
    assert resposta.status == 500
    mensagens = [r.getMessage() for r in caplog.records]
    assert mensagens, "the 500 must be logged"
    assert not any("\n" in m for m in mensagens), mensagens
    assert any("%0A" in m for m in mensagens), mensagens


async def test_expect_100_continue_segue_para_a_rota(cliente):
    # Why: the client library does not model an interim 100, so the exchange goes over a socket.
    # Por que: a biblioteca cliente não modela um 100 intermediário, então a troca vai por socket.
    leitor, escritor = await asyncio.open_connection(cliente.host, cliente.port)
    escritor.write(
        b"GET /health HTTP/1.1\r\nHost: localhost\r\nExpect: 100-continue\r\n"
        b"Connection: close\r\n\r\n"
    )
    await escritor.drain()
    bruto = await asyncio.wait_for(leitor.read(), timeout=5)
    escritor.close()
    await escritor.wait_closed()
    assert bruto.startswith(b"HTTP/1.1 100 Continue\r\n\r\n")
    assert b"HTTP/1.1 200 OK" in bruto
    assert b"X-Frame-Options: DENY" in bruto
    assert b'"ok": true' in bruto

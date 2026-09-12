# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
import json

import pytest
from aiohttp import web

from iphub.portao import middleware_erros_json


async def test_rota_desconhecida_e_404_json(cliente):
    resposta = await cliente.get("/nao-existe")
    assert resposta.status == 404
    assert resposta.content_type == "application/json"
    assert await resposta.json() == {"ok": False, "code": "nao_encontrado"}


async def test_caminho_vizinho_de_rota_real_e_404_json(cliente):
    for caminho in ("/api", "/api/setup", "/api/estado/x", "/dpbus/x", "/index.html"):
        resposta = await cliente.get(caminho)
        assert resposta.status == 404, caminho
        assert await resposta.json() == {"ok": False, "code": "nao_encontrado"}


async def test_metodo_errado_e_405_json_com_allow(cliente):
    resposta = await cliente.post("/health")
    assert resposta.status == 405
    assert await resposta.json() == {"ok": False, "code": "metodo_nao_permitido"}
    assert "GET" in resposta.headers["Allow"]


async def test_asset_ausente_e_404_json(cliente):
    resposta = await cliente.get("/assets/nao-existe.js")
    assert resposta.status == 404
    assert await resposta.json() == {"ok": False, "code": "nao_encontrado"}


async def test_erro_nunca_e_a_frase_do_aiohttp(cliente):
    for caminho, metodo in (("/nao-existe", "GET"), ("/health", "DELETE")):
        texto = await (await cliente.request(metodo, caminho)).text()
        assert "Not Found" not in texto and "Method Not Allowed" not in texto


@pytest.fixture
async def cliente_cru(aiohttp_client):
    async def proibido(request):
        raise web.HTTPForbidden()

    async def ja_em_json(request):
        raise web.HTTPForbidden(
            text=json.dumps({"ok": False, "code": "auth_pendente"}), content_type="application/json"
        )

    async def redireciona(request):
        raise web.HTTPFound("/health")

    app = web.Application(middlewares=[middleware_erros_json])
    app.router.add_get("/proibido", proibido)
    app.router.add_get("/json", ja_em_json)
    app.router.add_get("/redireciona", redireciona)
    return await aiohttp_client(app)


async def test_codigo_desconhecido_vira_erro_http(cliente_cru):
    resposta = await cliente_cru.get("/proibido")
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "erro_http"}


async def test_excecao_ja_em_json_passa_intacta(cliente_cru):
    resposta = await cliente_cru.get("/json")
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "auth_pendente"}


async def test_cabecalho_com_significado_sobrevive(cliente_cru):
    resposta = await cliente_cru.get("/redireciona", allow_redirects=False)
    assert resposta.status == 302
    assert resposta.headers["Location"] == "/health"
    assert resposta.content_type == "application/json"


async def test_curinga_nao_engole_o_405_nem_o_allow(cliente):
    for metodo in ("POST", "PUT", "DELETE", "PATCH"):
        resposta = await cliente.request(metodo, "/health")
        assert resposta.status == 405, metodo
        assert await resposta.json() == {"ok": False, "code": "metodo_nao_permitido"}
        assert "GET" in resposta.headers["Allow"]
    resposta = await cliente.post("/")
    assert resposta.status == 405
    assert "GET" in resposta.headers["Allow"]


async def test_curinga_devolve_404_para_caminho_sem_rota(cliente):
    for caminho in ("/nao-existe", "/a/b/c", "/health/x", "/assets"):
        resposta = await cliente.get(caminho)
        assert resposta.status == 404, caminho
        assert await resposta.json() == {"ok": False, "code": "nao_encontrado"}

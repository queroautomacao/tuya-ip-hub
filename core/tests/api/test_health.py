# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
import time

from iphub.api.health import INICIO
from iphub.app import criar_app
from iphub.versao import SCHEMA_VERSION, VERSAO


async def test_health_responde_o_contrato(cliente):
    resposta = await cliente.get("/health")
    assert resposta.status == 200
    assert resposta.content_type == "application/json"
    corpo = await resposta.json()
    assert corpo == {
        "ok": True,
        "code": None,
        "versao": VERSAO,
        "schema_version": SCHEMA_VERSION,
        "uptime_s": corpo["uptime_s"],
    }
    assert type(corpo["uptime_s"]) is int
    assert corpo["uptime_s"] >= 0


async def test_uptime_nao_anda_para_tras(cliente):
    primeiro = (await (await cliente.get("/health")).json())["uptime_s"]
    segundo = (await (await cliente.get("/health")).json())["uptime_s"]
    assert segundo >= primeiro


async def test_health_nao_e_prefixo_de_outras_rotas(cliente):
    assert (await cliente.get("/health/x")).status == 404
    assert (await cliente.get("/healthz")).status == 404


async def test_uptime_conta_a_partir_do_inicio_registrado(aiohttp_client, amb):
    app = criar_app(amb)
    app[INICIO] = time.monotonic() - 100
    cliente = await aiohttp_client(app)
    corpo = await (await cliente.get("/health")).json()
    assert 100 <= corpo["uptime_s"] < 110

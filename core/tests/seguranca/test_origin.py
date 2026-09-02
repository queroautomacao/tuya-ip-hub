# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: an Origin that is not this host is 403 on /api/ and on /dpbus (closes CSRF).

Seção 9: um Origin que não é este host é 403 em /api/ e em /dpbus (fecha CSRF).
"""

import pytest

from iphub.portao import CABECALHOS

ALHEIA = "http://evil.example.com"
RECUSADA = {"ok": False, "code": "origem_nao_permitida"}
PROTEGIDAS = [
    ("GET", "/api/estado"),
    ("POST", "/api/posse"),
    ("POST", "/api/entrar"),
    ("POST", "/api/sair"),
    ("GET", "/api/sessao"),
    ("POST", "/api/senha"),
    ("GET", "/dpbus"),
]


def _propria(cliente) -> str:
    return f"http://{cliente.host}:{cliente.port}"


@pytest.mark.parametrize(("metodo", "caminho"), PROTEGIDAS)
async def test_origin_de_outro_site_e_403(cliente, metodo, caminho):
    resposta = await cliente.request(metodo, caminho, headers={"Origin": ALHEIA})
    assert resposta.status == 403
    assert await resposta.json() == RECUSADA
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome


async def test_a_acao_nao_acontece_por_tras_do_403(cliente, codigo, senha):
    # Why: the page of the attacker knows the ownership code only if the owner pasted it
    # somewhere, and even then the browser must not be able to spend it here.
    # Por que: a página do atacante só sabe o código de posse se o dono colou em algum lugar,
    # e mesmo assim o navegador não pode conseguir gastá-lo aqui.
    resposta = await cliente.post(
        "/api/posse", json={"codigo": codigo, "senha": senha}, headers={"Origin": ALHEIA}
    )
    assert resposta.status == 403
    assert (await (await cliente.get("/api/estado")).json())["configurado"] is False


@pytest.mark.parametrize("origem", ["null", "", "http://", "evil.example.com", "file://"])
async def test_origin_sem_a_autoridade_deste_host_e_403(cliente, origem):
    resposta = await cliente.get("/api/estado", headers={"Origin": origem})
    assert resposta.status == 403
    assert await resposta.json() == RECUSADA


async def test_origin_de_outra_porta_do_mesmo_nome_e_403(cliente):
    # Why: the browser treats another port as another origin, and so does the gate.
    # Por que: o navegador trata outra porta como outra origem, e o portão também.
    outra = f"http://{cliente.host}:{cliente.port + 1}"
    assert (await cliente.get("/api/estado", headers={"Origin": outra})).status == 403


async def test_sem_origin_passa(cliente):
    # Why: a tool sends no Origin and a browser always sends one, so the absent header is not
    # the attack; refusing it would only break curl and the DP-bus client.
    # Por que: uma ferramenta não manda Origin e um navegador sempre manda, então o cabeçalho
    # ausente não é o ataque; recusá-lo só quebraria o curl e o cliente do DP-bus.
    assert (await cliente.get("/api/estado")).status == 200


async def test_origin_do_proprio_host_passa(cliente, codigo, senha):
    origem = _propria(cliente)
    assert (await cliente.get("/api/estado", headers={"Origin": origem})).status == 200
    resposta = await cliente.post(
        "/api/posse", json={"codigo": codigo, "senha": senha}, headers={"Origin": origem}
    )
    assert resposta.status == 200


async def test_a_comparacao_ignora_a_caixa(cliente):
    host = f"localhost:{cliente.port}"
    resposta = await cliente.get(
        "/api/estado", headers={"Host": host, "Origin": f"http://LOCALHOST:{cliente.port}"}
    )
    assert resposta.status == 200


async def test_o_painel_e_o_health_nao_dependem_do_origin(cliente):
    # Why: the gate guards what changes state or hands out data of the installation; the
    # panel files and the liveness probe are neither.
    # Por que: o portão guarda o que muda estado ou entrega dados da instalação; os arquivos
    # do painel e a sonda de vitalidade não são nem um nem outro.
    for caminho in ("/", "/health", "/assets/app.js"):
        assert (await cliente.get(caminho, headers={"Origin": ALHEIA})).status == 200, caminho


async def test_host_hostil_e_recusado_antes_do_origin(cliente):
    resposta = await cliente.get(
        "/api/estado", headers={"Host": "evil.example.com", "Origin": ALHEIA}
    )
    assert resposta.status == 421
    assert await resposta.json() == {"ok": False, "code": "host_nao_permitido"}

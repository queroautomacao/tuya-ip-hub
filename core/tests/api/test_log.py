# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The route of the diary: what the panel reads to diagnose a driver, and who may read it.

A rota do log: o que o painel lê para diagnosticar um driver, e quem pode lê-la.
"""

import logging

import pytest

from iphub.api.comum import LOG


@pytest.fixture
async def hub(fabrica_cliente, posse, bearer):
    cliente = await fabrica_cliente()
    return cliente, bearer(await posse(cliente))


async def _log(cliente, auth) -> dict:
    resposta = await cliente.get("/api/log", headers=auth)
    assert resposta.status == 200, await resposta.text()
    return await resposta.json()


async def test_o_log_exige_sessao(hub):
    """The lines carry addresses, identities and every command that crossed the installation,
    which is a map of the house; section 9 keeps that behind a session.

    As linhas levam endereços, identidades e todo comando que atravessou a instalação, que é um
    mapa da casa; a seção 9 mantém isso atrás de uma sessão.
    """
    cliente, _ = hub
    resposta = await cliente.get("/api/log")
    assert resposta.status == 401


async def test_o_log_traz_o_que_o_daemon_escreveu_com_a_origem_de_cada_linha(hub):
    """The three stories of a diagnosis are the driver, the bus of the platform and the panel,
    and each line says which one it is so the screen can group them.

    As três histórias de um diagnóstico são o driver, o barramento da plataforma e o painel, e
    cada linha diz qual é para a tela poder agrupá-las.
    """
    cliente, auth = hub
    logging.getLogger("iphub.drivers.nativos.linkplay").debug("setPlayerCmd:vol:30")
    logging.getLogger("iphub.dpbus.socket").info("set dp 121 = 30")
    corpo = await _log(cliente, auth)
    assert corpo["ok"] is True
    assert corpo["teto"] > 0
    assert corpo["descartadas"] == 0
    escritas = [(linha["origem"], linha["texto"]) for linha in corpo["linhas"]]
    assert ("driver", "setPlayerCmd:vol:30") in escritas
    assert ("tuya", "set dp 121 = 30") in escritas
    # Why: the order is the whole value of a log, and the panel appends to the bottom.
    # Por que: a ordem é todo o valor de um log, e o painel acrescenta embaixo.
    instantes = [linha["t"] for linha in corpo["linhas"]]
    assert instantes == sorted(instantes)


async def test_uma_acao_do_painel_e_um_comando_do_driver_aparecem_no_log(hub):
    """A press on the panel of an equipment that is not there still tells the story: what was
    asked, of whom, and the stable code that came back.

    Uma apertada no painel num equipamento que não está lá conta a história mesmo assim: o que
    foi pedido, de quem, e o código estável que voltou.
    """
    cliente, auth = hub
    resposta = await cliente.post(
        "/api/equipamentos/nao-existe/acao", json={"acao": "tocar", "valor": None}, headers=auth
    )
    assert resposta.status == 404
    textos = [linha["texto"] for linha in (await _log(cliente, auth))["linhas"]]
    assert any("nao-existe" in texto and "tocar" in texto for texto in textos)


async def test_o_log_tem_teto_e_diz_quantas_linhas_soltou(hub):
    """A hub runs for months: what the panel reads is the last lines, and the count of what
    was dropped is what keeps a hole from reading as silence.

    Um hub roda por meses: o que o painel lê são as últimas linhas, e a conta do que foi
    descartado é o que impede um buraco de ser lido como silêncio.
    """
    cliente, auth = hub
    log = cliente.app[LOG]
    teto = log._linhas.maxlen or 0
    for numero in range(teto + 5):
        logging.getLogger("iphub").info("linha %d", numero)
    corpo = await _log(cliente, auth)
    assert len(corpo["linhas"]) == teto
    assert corpo["descartadas"] >= 5
    assert corpo["linhas"][-1]["texto"] == f"linha {teto + 4}"

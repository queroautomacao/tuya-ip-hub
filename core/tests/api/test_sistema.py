# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The routes of the appliance: restarting the daemon and checking for a newer image.

As rotas do appliance: reiniciar o daemon e conferir se há imagem mais nova.
"""

import asyncio

import pytest

from iphub.api import sistema
from iphub.versao import VERSAO


@pytest.fixture
def pecas(fabrica_cliente, posse, bearer):
    """A client whose stop and whose internet are both fakes the test can read.

    Um cliente cuja parada e cuja internet são dublês que o teste consegue ler.
    """

    async def montar(ultima: str | None = None):
        chamadas = {"encerrar": 0, "buscar": 0}

        def encerrar() -> None:
            chamadas["encerrar"] += 1

        async def buscar() -> str | None:
            chamadas["buscar"] += 1
            return ultima

        cliente = await fabrica_cliente(encerrar=encerrar, buscar_versao=buscar)
        token = await posse(cliente)
        return cliente, bearer(token), chamadas

    return montar


@pytest.mark.parametrize(
    ("metodo", "rota"), [("POST", "/api/reiniciar"), ("GET", "/api/atualizacao")]
)
async def test_as_rotas_do_appliance_exigem_sessao(cliente, metodo, rota):
    resposta = await cliente.request(metodo, rota)
    assert resposta.status == 401
    assert (await resposta.json())["code"] == "nao_autenticado"


async def test_reiniciar_responde_antes_de_parar_e_depois_para(pecas, monkeypatch):
    """The answer leaves before the process goes, so the panel reads an ok and not a
    connection that dropped; then the stop really happens.

    A resposta sai antes de o processo ir, então o painel lê um ok e não uma conexão que
    caiu; depois a parada acontece de verdade.
    """
    monkeypatch.setattr(sistema, "ATRASO_REINICIO_S", 0.02)
    cliente, auth, chamadas = await pecas()
    resposta = await cliente.post("/api/reiniciar", headers=auth)
    assert resposta.status == 200
    assert await resposta.json() == {"ok": True, "code": None}
    assert chamadas["encerrar"] == 0
    await asyncio.sleep(0.1)
    assert chamadas["encerrar"] == 1


async def test_o_processo_de_verdade_recebe_um_sigterm(monkeypatch):
    # Why: SIGTERM is what run_app turns into a clean stop, draining the routes and the bus;
    # a SIGKILL would drop a config write halfway.
    # Por que: SIGTERM é o que o run_app transforma em parada limpa, esvaziando as rotas e o
    # barramento; um SIGKILL derrubaria uma escrita de config pela metade.
    recebidos: list[tuple[int, int]] = []
    monkeypatch.setattr(sistema.os, "kill", lambda pid, sinal: recebidos.append((pid, sinal)))
    sistema.encerrar_processo()
    assert recebidos == [(sistema.os.getpid(), sistema.signal.SIGTERM)]


@pytest.mark.parametrize(
    ("ultima", "disponivel", "verificada"),
    [("9.9.9", True, True), (VERSAO, False, True), ("0.0.1", False, True), (None, False, False)],
)
async def test_atualizacao_diz_a_versao_atual_a_ultima_e_se_ha_mais_nova(
    pecas, ultima, disponivel, verificada
):
    cliente, auth, _chamadas = await pecas(ultima)
    resposta = await cliente.get("/api/atualizacao", headers=auth)
    assert resposta.status == 200
    assert await resposta.json() == {
        "ok": True,
        "code": None,
        "atual": VERSAO,
        "ultima": ultima,
        "disponivel": disponivel,
        "verificada": verificada,
    }


async def test_a_internet_e_perguntada_uma_vez_por_janela_e_nao_por_pagina(pecas):
    """A hub on a customer LAN asks once every ten minutes at most, whoever is looking.

    Um hub na LAN de um cliente pergunta no máximo uma vez a cada dez minutos, quem quer que
    esteja olhando.
    """
    cliente, auth, chamadas = await pecas("9.9.9")
    for _ in range(3):
        assert (await cliente.get("/api/atualizacao", headers=auth)).status == 200
    assert chamadas["buscar"] == 1


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("1.2.3", (1, 2, 3)),
        ("v1.2.3", (1, 2, 3)),
        (" 0.10.0 ", (0, 10, 0)),
        ("1.2", None),
        ("1.2.3.4", None),
        ("v1.2.3-rc1", None),
        ("", None),
        (None, None),
        (123, None),
        ("1." + "9" * 5 + ".0", None),
    ],
)
def test_uma_versao_e_tres_numeros_e_nada_mais(texto, esperado):
    assert sistema.partes_de(texto) == esperado


@pytest.mark.parametrize(
    ("atual", "ultima", "esperado"),
    [
        ("0.1.0", "0.1.1", True),
        ("0.1.0", "0.2.0", True),
        ("0.9.9", "1.0.0", True),
        ("0.1.0", "0.1.0", False),
        ("0.2.0", "0.1.9", False),
        ("0.1.0", None, False),
        ("0.1.0", "lixo", False),
    ],
)
def test_so_uma_versao_maior_conta_como_mais_nova(atual, ultima, esperado):
    assert sistema.ha_mais_nova(atual, ultima) is esperado


async def test_um_projeto_ainda_sem_release_e_verificado_e_nao_sem_internet(pecas):
    """A 404 from the releases is the internet answering that nothing was published yet, and
    the panel must not tell the operator to check the link.

    Um 404 das releases é a internet respondendo que nada foi publicado ainda, e o painel não
    pode mandar o operador conferir o link.
    """
    cliente, auth, _chamadas = await pecas(sistema.SEM_RELEASE)
    corpo = await (await cliente.get("/api/atualizacao", headers=auth)).json()
    assert corpo["verificada"] is True
    assert corpo["ultima"] is None
    assert corpo["disponivel"] is False

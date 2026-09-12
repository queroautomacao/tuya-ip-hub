# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""What one atualizar of the projector costs and what it lets into the state.

 attacked from the device side: a projector is free to stall and free to answer
anything at all. Neither may cost the hub the poll slot of every other device, and nothing a
device said may land in the state, where the API and the panel would show it.
"""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.base import DETALHES
from iphub.drivers.nativos import pjlink
from iphub.drivers.nativos.pjlink import PJLink, entrada_valida, entradas_de
from iphub.drivers.simulado import ServidorLinha

PRAZO_DE_TESTE_S = 0.3
ORCAMENTO_DE_TESTE_S = 0.45
FOLGA_S = 0.2
# The four questions one poll asks: power, the input list, the current input and the mute.
PERGUNTAS_DO_POLL = 4

SAUDACAO_ABERTA = b"PJLINK 0\r"
POLL_LIGADO = {
    b"%1POWR ?": b"%1POWR=1\r",
    b"%1INST ?": b"%1INST=11 31 32\r",
    b"%1INPT ?": b"%1INPT=31\r",
    b"%1AVMT ?": b"%1AVMT=30\r",
}
# The answer a hostile device gives to the input question: control bytes and 900 characters.
RUIDO = b"\x00\x01\x02\x1b[31m" + b"Z" * 900


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "uuid-do-projetor"
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)


@pytest.fixture(autouse=True)
def prazo_curto(monkeypatch):
    """A device that ignores a line is answered by the deadline, and no suite waits 6 s."""
    monkeypatch.setattr(pjlink, "TEMPO_LIMITE_S", PRAZO_DE_TESTE_S)
    monkeypatch.setattr(pjlink, "ORCAMENTO_DO_POLL_S", ORCAMENTO_DE_TESTE_S)


def _driver(aparelho: ServidorLinha) -> PJLink:
    anfitriao, porta = aparelho.endereco
    return PJLink(_Cadastro(ip=anfitriao, campos={"porta": str(porta)}))


async def test_o_poll_de_projetor_apagado_nao_pergunta_o_resto():
    """Generalized: an off device answers ERR3 to everything, and it is not a fault."""
    async with ServidorLinha({b"%1POWR ?": b"%1POWR=0\r"}, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
        assert aparelho.recebidas == [b"%1POWR ?"]
    assert driver.estado().online is True
    assert driver.estado().ligado is False
    assert driver.estado().detalhe == ""


async def test_o_poll_aprende_as_fontes_uma_unica_vez():
    async with ServidorLinha(POLL_LIGADO, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
        await driver.atualizar()
    assert aparelho.recebidas.count(b"%1INST ?") == 1
    assert driver.estado().fontes == ("11", "31", "32")


async def test_o_poll_de_aparelho_calado_marca_offline_com_o_codigo_no_detalhe():
    async with ServidorLinha({}, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_um_poll_inteiro_cabe_no_orcamento_por_mais_calado_que_o_projetor_fique():
    """A deadline per exchange lets one wedged projector hold its slot for four deadlines.

    The device greets, answers the power question and then ignores every other line, which is
    what a projector with a wedged network stack does, and what anybody parked on the
    registered port can do on purpose.
    """
    laco = asyncio.get_running_loop()
    async with ServidorLinha({b"%1POWR ?": b"%1POWR=1\r"}, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        inicio = laco.time()
        await driver.atualizar()
        decorrido = laco.time() - inicio
    assert decorrido < ORCAMENTO_DE_TESTE_S + FOLGA_S
    assert aparelho.conexoes < PERGUNTAS_DO_POLL
    assert driver.estado().online is True
    assert driver.estado().ligado is True


@pytest.mark.parametrize(
    "resposta", [b"%1INPT=" + RUIDO + b"\r", b"%1INPT=OK\r", b"%1INPT=\r", b"%1INPT=311\r"]
)
async def test_a_entrada_respondida_fora_do_protocolo_nao_entra_no_estado(resposta):
    """An input of class 1 is two characters, and the state carries nothing else,."""
    respostas = {**POLL_LIGADO, b"%1INPT ?": resposta}
    async with ServidorLinha(respostas, saudacao=SAUDACAO_ABERTA) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.fonte is None
    assert "Z" not in repr(estado)
    # The rest of the poll goes on: one answer out of the protocol is not a dead device.
    assert estado.online is True
    assert estado.fontes == ("11", "31", "32")
    assert estado.mudo is False


@pytest.mark.parametrize(
    ("saudacao", "respostas"),
    [
        (SAUDACAO_ABERTA, {}),
        (SAUDACAO_ABERTA, {b"%1POWR ?": b"PJLINK ERRA\r"}),
        (SAUDACAO_ABERTA, {b"%1POWR ?": b"%1POWR=ERR4\r"}),
        (b"NAO SOU UM PROJETOR, SOU OUTRA COISA\r", {b"%1POWR ?": b"%1POWR=1\r"}),
    ],
)
async def test_o_detalhe_de_um_poll_que_falhou_e_um_codigo_e_nunca_uma_frase(saudacao, respostas):
    """The daemon never answers a phrase, so detalhe is one code of DETALHES."""
    async with ServidorLinha(respostas, saudacao=saudacao) as aparelho:
        driver = _driver(aparelho)
        await driver.atualizar()
    detalhe = driver.estado().detalhe
    assert detalhe in DETALHES
    assert "PROJETOR" not in detalhe


def test_todo_codigo_que_o_poll_pode_por_no_detalhe_esta_no_vocabulario():
    do_driver = {
        pjlink.EQ_OFFLINE,
        pjlink.INVALID_VALUE,
        pjlink.AUTH_PENDENTE,
        pjlink.ERRO_APARELHO,
    }
    assert do_driver <= set(DETALHES)


@pytest.mark.parametrize(
    ("valor", "valida"),
    [("11", True), ("59", True), ("10", False), ("61", False), ("1", False), ("111", False)],
)
def test_entrada_valida_segue_os_dois_digitos_do_protocolo(valor, valida):
    assert entrada_valida(valor) is valida


def test_a_lista_de_entradas_guarda_so_o_que_o_protocolo_permite():
    assert entradas_de("11 21 XX 99 3 31") == ("11", "21", "31")
    assert entradas_de("") == ()

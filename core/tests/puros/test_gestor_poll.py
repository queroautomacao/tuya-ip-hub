# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The polling loop under attack: one task, staggered, and two failures make a device offline.

O laço de poll sob ataque: uma tarefa, escalonada, e duas falhas fazem um aparelho offline.
"""

import asyncio
import logging

import pytest

from iphub.config import Cadastro
from iphub.drivers.base import DETALHES, Driver
from iphub.drivers.gestor import EQ_OFFLINE, FALHAS_ATE_OFFLINE, INTERVALO_S, Gestor
from iphub.drivers.manifesto import Estado, Manifesto


# Why: something outside Exception, which is what a device library raising SystemExit or a
# KeyboardInterrupt deep inside a socket call looks like from here.
# Por que: algo fora de Exception, que é o que uma biblioteca de aparelho estourando SystemExit
# ou um KeyboardInterrupt no fundo de uma chamada de socket parece daqui.
class Explosao(BaseException):
    pass


def _manifesto(tipo: str) -> Manifesto:
    textos = {"descricao": "Exemplo"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Exemplo", "en": "Example"},
        categoria="outro",
        capacidades=("ligar",),
        textos={"pt": dict(textos), "en": dict(textos)},
    )


def _cadastro(identidade: str = "uuid-1", tipo: str = "exemplo") -> Cadastro:
    return Cadastro(identidade=identidade, tipo=tipo, nome="Sala", ip="192.0.2.10")


def _fabrica(tipo: str = "exemplo", **comportamento: object) -> type[Driver]:
    """A driver that counts its polls and fails on command.

    Um driver que conta os polls dele e falha sob comando.
    """

    class Falso(Driver):
        MANIFESTO = _manifesto(tipo)
        instancias: list["Falso"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.atualizacoes = 0
            self.iniciado = False
            self.parado = False
            self.falhar = bool(comportamento.get("falhar"))
            self.travar = bool(comportamento.get("travar"))
            self.explodir = bool(comportamento.get("explodir"))
            self.estacionar = False
            self.no_poll = asyncio.Event()
            self.liberar = asyncio.Event()
            type(self).instancias.append(self)

        async def iniciar(self) -> None:
            self.iniciado = True

        async def parar(self) -> None:
            self.parado = True

        async def atualizar(self) -> None:
            self.atualizacoes += 1
            self.no_poll.set()
            if self.travar:
                await asyncio.Event().wait()
            if self.estacionar:
                await self.liberar.wait()
            if self.explodir:
                raise Explosao("fora de Exception")
            if self.falhar:
                raise TimeoutError(comportamento.get("mensagem", "sem resposta"))
            self._defina(online=True, detalhe="")

    Falso.instancias = []
    return Falso


class Bomba:
    """A clock and a sleep the test drives by hand, one poll at a time.

    Um relógio e um sleep que o teste conduz na mão, um poll por vez.
    """

    def __init__(self) -> None:
        self.agora = 0.0
        self.dormidas: list[float] = []
        self._dormindo = asyncio.Event()
        self._seguir = asyncio.Event()

    def __call__(self) -> float:
        return self.agora

    async def dormir(self, segundos: float) -> None:
        self.dormidas.append(segundos)
        self.agora += segundos
        self._dormindo.set()
        await self._seguir.wait()
        self._seguir.clear()

    async def passo(self, quantos: int = 1) -> None:
        """Each step runs one sleep and one poll, and parks the loop on the next sleep.

        Cada passo roda um sleep e um poll, e estaciona o laço no sleep seguinte.
        """
        for _ in range(quantos):
            await self._dormindo.wait()
            self._dormindo.clear()
            self._seguir.set()
            await self._dormindo.wait()


@pytest.fixture
async def monta():
    """Builds a gestor driven by the pump and guarantees it is stopped when the test ends.

    Constrói um gestor conduzido pela bomba e garante que ele para quando o teste termina.
    """
    vivos: list[Gestor] = []

    async def criar(catalogo: dict, cadastros, bomba: Bomba, intervalo_s: float = 10.0, **pecas):
        gestor = Gestor(
            catalogo, cadastros, intervalo_s=intervalo_s, agora=bomba, dormir=bomba.dormir, **pecas
        )
        vivos.append(gestor)
        await gestor.iniciar()
        return gestor

    yield criar
    for gestor in vivos:
        await gestor.parar()


async def test_uma_falha_nao_derruba_duas_derrubam_e_um_sucesso_levanta(monta):
    """Section 14 generalized: one lost answer is a hiccup, two in a row are an offline device.

    Seção 14 generalizada: uma resposta perdida é um soluço, duas seguidas são aparelho offline.
    """
    classe = _fabrica()
    bomba = Bomba()
    gestor = await monta({"exemplo": classe}, [_cadastro()], bomba)
    await bomba.passo()
    assert gestor.estados()["uuid-1"].online is True
    driver = classe.instancias[0]
    driver.falhar = True
    await bomba.passo(FALHAS_ATE_OFFLINE - 1)
    assert gestor.estados()["uuid-1"].online is True
    await bomba.passo()
    estado = gestor.estados()["uuid-1"]
    assert estado.online is False
    assert estado.detalhe == EQ_OFFLINE
    driver.falhar = False
    await bomba.passo()
    assert gestor.estados()["uuid-1"] == Estado(online=True, detalhe="")


async def test_o_que_o_aparelho_disse_nunca_vira_detalhe(monta):
    """Section 11: detalhe is one code of DETALHES, so a device line never reaches the screen.

    Seção 11: o detalhe é um código de DETALHES, então uma linha do aparelho nunca chega à tela.
    """
    classe = _fabrica(falhar=True, mensagem="linha\r\nde\x00lixo " + "a" * 400)
    bomba = Bomba()
    gestor = await monta({"exemplo": classe}, [_cadastro()], bomba)
    await bomba.passo(FALHAS_ATE_OFFLINE)
    detalhe = gestor.estados()["uuid-1"].detalhe
    assert detalhe == EQ_OFFLINE
    assert detalhe in DETALHES
    assert "lixo" not in detalhe and "a" * 40 not in detalhe


async def test_um_driver_que_estoura_no_poll_nao_para_o_poll_do_outro(monta):
    ruim = _fabrica("ruim", falhar=True)
    bom = _fabrica("bom")
    bomba = Bomba()
    gestor = await monta(
        {"ruim": ruim, "bom": bom}, [_cadastro("uuid-1", "ruim"), _cadastro("uuid-2", "bom")], bomba
    )
    await bomba.passo(4)
    assert ruim.instancias[0].atualizacoes == 2
    assert bom.instancias[0].atualizacoes == 2
    assert gestor.estados()["uuid-2"].online is True


async def test_o_poll_e_escalonado_dentro_do_intervalo(monta):
    """Two drivers never hit the network in the same instant: each one gets half the interval.

    Dois drivers nunca batem na rede no mesmo instante: cada um recebe metade do intervalo.
    """
    um = _fabrica("um")
    dois = _fabrica("dois")
    bomba = Bomba()
    await monta(
        {"um": um, "dois": dois}, [_cadastro("uuid-1", "um"), _cadastro("uuid-2", "dois")], bomba
    )
    await bomba.passo()
    assert (um.instancias[0].atualizacoes, dois.instancias[0].atualizacoes) == (1, 0)
    await bomba.passo(3)
    assert (um.instancias[0].atualizacoes, dois.instancias[0].atualizacoes) == (2, 2)
    # Why: one more sleep than steps, because a step parks the loop on the next one.
    # Por que: um sleep a mais que passos, porque um passo estaciona o laço no seguinte.
    assert bomba.dormidas == [5.0] * 5


async def test_o_hub_funciona_com_zero_equipamentos(monta):
    """Section 6: no equipment is a normal state, and the loop keeps its own pace.

    Seção 6: nenhum equipamento é estado normal, e o laço mantém o próprio ritmo.
    """
    bomba = Bomba()
    gestor = await monta({}, [], bomba, intervalo_s=INTERVALO_S)
    assert gestor.estados() == {}
    assert gestor.cadastros == ()
    await bomba.passo(2)
    assert bomba.dormidas == [INTERVALO_S] * 3


async def test_cadastrar_e_remover_com_o_laco_rodando(monta):
    classe = _fabrica()
    bomba = Bomba()
    gestor = await monta({"exemplo": classe}, [], bomba)
    assert await gestor.cadastrar(_cadastro()) == (_cadastro(),)
    await bomba.passo(2)
    driver = classe.instancias[0]
    assert driver.iniciado is True
    # Why: three polls, because the registration is visited on the spot and then twice by the
    # loop; waiting for the schedule would show a fresh registration offline for two intervals.
    # Por que: três polls, porque o cadastro é visitado na hora e depois duas vezes pelo laço;
    # esperar o agendamento mostraria um cadastro novo offline por dois intervalos.
    assert driver.atualizacoes == 3
    assert await gestor.remover("uuid-1") == ()
    assert driver.parado is True
    await bomba.passo(2)
    assert driver.atualizacoes == 3
    assert gestor.estados() == {}


async def test_parar_encerra_a_tarefa_de_poll():
    classe = _fabrica()
    bomba = Bomba()
    gestor = Gestor(
        {"exemplo": classe}, [_cadastro()], intervalo_s=10.0, agora=bomba, dormir=bomba.dormir
    )
    await gestor.iniciar()
    await bomba.passo()
    await gestor.parar()
    antes = classe.instancias[0].atualizacoes
    await asyncio.sleep(0)
    assert classe.instancias[0].atualizacoes == antes


async def test_driver_travado_no_poll_nao_segura_o_poll_do_proximo(monta):
    """A device that accepts the connection and goes quiet holds only its own slot.

    Um aparelho que aceita a conexão e emudece segura só a vaga dele.
    """
    travado = _fabrica("travado", travar=True)
    bom = _fabrica("bom")
    bomba = Bomba()
    gestor = await monta(
        {"travado": travado, "bom": bom},
        [_cadastro("uuid-1", "travado"), _cadastro("uuid-2", "bom")],
        bomba,
        limite_s=0.05,
    )
    # Why: without the deadline the pump never comes back, so the test hangs instead of
    # failing; the ceiling turns that into a verdict.
    # Por que: sem o prazo a bomba nunca volta, então o teste trava em vez de falhar; o teto
    # transforma isso num veredito.
    async with asyncio.timeout(5):
        await bomba.passo(2 * FALHAS_ATE_OFFLINE)
    assert bom.instancias[0].atualizacoes == FALHAS_ATE_OFFLINE
    assert gestor.estados()["uuid-2"].online is True
    estado = gestor.estados()["uuid-1"]
    assert estado.online is False
    assert estado.detalhe == EQ_OFFLINE


async def test_driver_que_estoura_fora_de_exception_nao_encerra_o_poll(monta):
    """One BaseException would end the poll task for good and freeze every screen.

    Uma BaseException encerraria a tarefa de poll de vez e congelaria toda tela.
    """
    ruim = _fabrica("ruim", explodir=True)
    bom = _fabrica("bom")
    bomba = Bomba()
    gestor = await monta(
        {"ruim": ruim, "bom": bom},
        [_cadastro("uuid-1", "ruim"), _cadastro("uuid-2", "bom")],
        bomba,
    )
    async with asyncio.timeout(5):
        await bomba.passo(2 * FALHAS_ATE_OFFLINE)
    assert ruim.instancias[0].atualizacoes == FALHAS_ATE_OFFLINE
    assert bom.instancias[0].atualizacoes == FALHAS_ATE_OFFLINE
    assert gestor.estados()["uuid-2"].online is True
    assert gestor.estados()["uuid-1"].detalhe == EQ_OFFLINE


async def test_tarefa_de_poll_que_morre_com_o_gestor_rodando_grita_no_log(caplog):
    """A hub that stopped polling in silence is the failure nobody notices.

    Um hub que parou de fazer poll em silêncio é a falha que ninguém percebe.
    """

    async def dormir(_segundos: float) -> None:
        raise RuntimeError("o relogio quebrou")

    gestor = Gestor({}, [], dormir=dormir)
    with caplog.at_level(logging.ERROR, logger="iphub.drivers.gestor"):
        await gestor.iniciar()
        for _ in range(3):
            await asyncio.sleep(0)
        await gestor.parar()
    assert [r for r in caplog.records if r.levelno == logging.ERROR]


async def test_poll_em_voo_nao_derruba_o_cadastro_refeito_por_baixo(monta):
    """Bookkeeping of a poll that outlived its registration would sink the new driver.

    A contabilidade de um poll que sobreviveu ao cadastro dele afundaria o driver novo.
    """
    classe = _fabrica()
    bomba = Bomba()
    gestor = await monta({"exemplo": classe}, [_cadastro()], bomba)
    velho = classe.instancias[0]
    velho.estacionar = True
    velho.falhar = True
    gestor.visitar_agora("uuid-1")
    async with asyncio.timeout(5):
        await velho.no_poll.wait()
    await gestor.atualizar_cadastro(_cadastro())
    novo = classe.instancias[1]
    velho.liberar.set()
    await asyncio.sleep(0.01)
    novo.falhar = True
    await bomba.passo()
    estado = gestor.estados()["uuid-1"]
    assert novo.atualizacoes >= FALHAS_ATE_OFFLINE
    assert estado.online is True
    assert estado.detalhe == ""


async def test_cadastro_novo_e_visitado_na_hora(monta):
    """Two intervals of offline right after a registration read as a registration that failed.

    Dois intervalos de offline logo após um cadastro parecem um cadastro que não funcionou.
    """
    classe = _fabrica()
    bomba = Bomba()
    gestor = await monta({"exemplo": classe}, [], bomba)
    await gestor.cadastrar(_cadastro())
    driver = classe.instancias[0]
    async with asyncio.timeout(5):
        await driver.no_poll.wait()
    assert driver.atualizacoes == 1
    assert bomba.dormidas == [INTERVALO_S]
    assert gestor.estados()["uuid-1"].online is True


async def test_cadastro_corrigido_e_visitado_na_hora(monta):
    classe = _fabrica()
    bomba = Bomba()
    gestor = await monta({"exemplo": classe}, [_cadastro()], bomba)
    await gestor.atualizar_cadastro(_cadastro())
    novo = classe.instancias[1]
    async with asyncio.timeout(5):
        await novo.no_poll.wait()
    assert novo.atualizacoes == 1

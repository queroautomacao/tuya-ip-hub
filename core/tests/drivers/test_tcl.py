# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The TCL driver against a simulated television that answers nothing, as the real one does."""

import asyncio
from contextlib import suppress
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import Auth, validar
from iphub.drivers.nativos import tcl
from iphub.drivers.nativos.tcl import Tcl

IP = "127.0.0.1"


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Tv:
    """A television that files what arrived and never answers, which is this protocol."""

    def __init__(self) -> None:
        self.recebidas: list[str] = []
        self.conexoes = 0
        self.endereco: tuple[str, int] = ("", 0)
        self._servidor: asyncio.Server | None = None
        self._tarefas: set[asyncio.Task] = set()

    async def iniciar(self) -> tuple[str, int]:
        self._servidor = await asyncio.start_server(self._atender, IP, 0)
        self.endereco = self._servidor.sockets[0].getsockname()[:2]
        return self.endereco

    async def parar(self) -> None:
        servidor, self._servidor = self._servidor, None
        for tarefa in self._tarefas:
            tarefa.cancel()
        if servidor is None:
            return
        servidor.close()
        with suppress(TimeoutError):
            async with asyncio.timeout(2.0):
                await servidor.wait_closed()

    async def _atender(self, leitor: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        tarefa = asyncio.current_task()
        if tarefa is not None:
            self._tarefas.add(tarefa)
        self.conexoes += 1
        try:
            bruto = await leitor.read()
            if bruto:
                self.recebidas.append(bruto.decode("utf-8"))
        except (ConnectionError, asyncio.CancelledError):
            pass
        finally:
            if tarefa is not None:
                self._tarefas.discard(tarefa)
            escritor.close()


@pytest.fixture
async def tv(monkeypatch):
    """A television already listening, with the driver aimed at the port it took."""
    ligadas: list[_Tv] = []

    async def abrir():
        servidor = _Tv()
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(tcl, "PORTA", servidor.endereco[1])
        return servidor, Tcl(_Cadastro())

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the television to read what was written to it, on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido_e_nao_promete_o_que_nao_da_para_ler():
    """Nothing comes back on this wire, so a level, a mute and an input would be lies."""
    validar(Tcl.MANIFESTO)
    assert Tcl.MANIFESTO.categoria == "tv"
    assert Tcl.MANIFESTO.auth is Auth.NENHUMA
    assert "volume" not in Tcl.MANIFESTO.capacidades
    assert "mudo" not in Tcl.MANIFESTO.capacidades
    assert "fonte" not in Tcl.MANIFESTO.capacidades
    # One key does play and pause, so it is a word of the remote and not two capabilities.
    assert "tocar" not in Tcl.MANIFESTO.capacidades
    assert "play_pause" in Tcl.MANIFESTO.teclas


async def test_um_poll_e_a_propria_conexao(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.volume is None
    # The connection is accepted on the time of the loop, not of this line.
    await _ate(lambda: servidor.conexoes == 1)
    assert servidor.recebidas == []


async def test_uma_tecla_e_uma_mensagem_xml_com_a_palavra_do_controle(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    await _ate(lambda: servidor.recebidas)
    mensagem = servidor.recebidas[-1]
    assert 'name="setKey"' in mensagem
    assert 'eventAction="TR_KEY_HOME"' in mensagem
    assert 'keyCode="TR_KEY_HOME"' in mensagem
    assert await driver.executar("tecla", "nao_existe") == "invalid_value"
    assert len(servidor.recebidas) == 1


async def test_o_transporte_que_existe_e_faixa_para_frente_e_para_tras(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("proxima") is None
    await _ate(lambda: servidor.recebidas)
    assert 'keyCode="TR_KEY_NEXT"' in servidor.recebidas[-1]
    assert await driver.executar("anterior") is None
    await _ate(lambda: len(servidor.recebidas) == 2)
    assert 'keyCode="TR_KEY_PREVIOUS"' in servidor.recebidas[-1]
    assert await driver.executar("pausar") == "nao_suportado"


async def test_desligar_e_a_tecla_de_espera(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("desligar") is None
    await _ate(lambda: servidor.recebidas)
    assert 'keyCode="TR_KEY_SUSPEND"' in servidor.recebidas[-1]
    assert driver.estado().ligado is False


async def test_ligar_e_o_pacote_magico_e_sem_mac_nao_ha_para_onde(tv, monkeypatch):
    enviados: list[bytes] = []
    monkeypatch.setattr(tcl, "_soprar", enviados.append)
    servidor, driver = await tv()
    assert await driver.executar("ligar") == "invalid_value"
    driver.cadastro.campos[tcl.CAMPO_MAC] = "AA:BB:CC:DD:EE:FF"
    assert await driver.executar("ligar") is None
    assert enviados == [bytes.fromhex("aabbccddeeff")]


async def test_uma_tv_que_nao_responde_e_offline_depois_de_dois_polls(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"

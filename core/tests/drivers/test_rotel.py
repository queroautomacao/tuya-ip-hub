# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Rotel driver against a simulated unit, one exclamation mark at a time."""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import rotel
from iphub.drivers.nativos.rotel import Rotel
from iphub.drivers.simulado import ServidorLinha

IP = "127.0.0.1"

RESPOSTAS = {
    b"power?": b"power=on$",
    b"volume?": b"volume=48$",
    b"mute?": b"mute=off$",
    b"source?": b"source=coax1$",
    b"mac?": b"mac=0CEFAF90125E$",
}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "amplificador-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


@pytest.fixture
async def unidade(monkeypatch):
    """A unit already listening, with the driver aimed at the port it took."""
    ligadas: list[ServidorLinha] = []

    async def abrir(respostas=None, **campos):
        servidor = ServidorLinha(
            dict(RESPOSTAS if respostas is None else respostas), terminador=b"!"
        )
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(rotel, "PORTA", servidor.endereco[1])
        return servidor, Rotel(_Cadastro(campos=campos))

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the unit to read what was written to it, which it does on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido():
    validar(Rotel.MANIFESTO)
    assert Rotel.MANIFESTO.categoria == "amplificador"
    assert set(Rotel.MANIFESTO.teclas) == {"mais", "menos"}


async def test_um_poll_le_as_quatro_respostas_pelo_nome_de_cada_uma(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # The unit counts to 96 and the contract counts to 100.
    assert estado.volume == 50
    assert estado.mudo is False
    assert estado.fonte == "coax1"
    await driver.parar()


async def test_o_standby_da_unidade_e_desligado(unidade):
    servidor, driver = await unidade({**RESPOSTAS, b"power?": b"power=standby$"})
    await driver.atualizar()
    assert driver.estado().ligado is False
    await driver.parar()


async def test_uma_resposta_da_geracao_anterior_termina_em_exclamacao(unidade):
    """The older protocol ends an answer with the same character that ends a command."""
    antigas = {chave: valor.replace(b"$", b"!") for chave, valor in RESPOSTAS.items()}
    servidor, driver = await unidade(antigas)
    await driver.atualizar()
    assert driver.estado().ligado is True
    assert driver.estado().volume == 50
    await driver.parar()


async def test_uma_linha_que_a_unidade_empurra_sozinha_nao_atrapalha(unidade):
    """Somebody touching the front panel writes a line that arrives with no question."""
    servidor, driver = await unidade({**RESPOSTAS, b"power?": b"dimmer=3$power=on$"})
    await driver.atualizar()
    assert driver.estado().ligado is True
    await driver.parar()


async def test_o_volume_vai_na_escala_da_unidade_e_o_teto_e_do_cadastro(unidade):
    servidor, driver = await unidade(volume_maximo="86")
    await driver.atualizar()
    # 48 of 86 is 56 on the contract, and 50 of 100 is 43 on the unit.
    assert driver.estado().volume == 56
    assert await driver.executar("volume", 50) is None
    await _ate(lambda: b"vol_43" in servidor.recebidas)
    assert await driver.executar("volume", 101) == "invalid_value"
    await driver.parar()


async def test_um_teto_torto_no_cadastro_volta_para_o_da_maioria(unidade):
    servidor, driver = await unidade(volume_maximo="zero")
    await driver.atualizar()
    assert driver.estado().volume == 50
    await driver.parar()


async def test_ligar_desligar_e_mudo_mandam_a_palavra_do_protocolo(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert await driver.executar("desligar") is None
    assert await driver.executar("mudo", True) is None
    assert await driver.executar("mudo", False) is None
    await _ate(
        lambda: (
            [b"power_on", b"power_off", b"mute_on", b"mute_off"]
            == [
                linha
                for linha in servidor.recebidas
                if linha in (b"power_on", b"power_off", b"mute_on", b"mute_off")
            ]
        )
    )
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_a_entrada_e_a_palavra_da_unidade(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    assert await driver.executar("fonte", "bal_xlr") is None
    await _ate(lambda: b"bal_xlr" in servidor.recebidas)
    assert driver.estado().fonte == "bal_xlr"
    await driver.parar()


@pytest.mark.parametrize("valor", ["power_on!", "COAX 1", "", "x" * 40, 3, "opt1;drop"])
async def test_uma_entrada_que_nao_e_palavra_da_unidade_e_recusada(unidade, valor):
    """The word is the whole frame, so anything that is not one is refused before it travels."""
    servidor, driver = await unidade()
    await driver.atualizar()
    quantas = len(servidor.recebidas)
    assert await driver.executar("fonte", valor) == "invalid_value"
    assert len(servidor.recebidas) == quantas
    await driver.parar()


async def test_o_transporte_e_as_teclas_de_volume_sao_do_protocolo(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    assert await driver.executar("tocar") is None
    assert driver.estado().reproduzindo is True
    assert await driver.executar("pausar") is None
    assert driver.estado().reproduzindo is False
    # A track skip says nothing about whether the source plays, so the state stays unknown.
    assert await driver.executar("proxima") is None
    assert driver.estado().reproduzindo is None
    assert await driver.executar("tecla", "mais") is None
    await _ate(lambda: {b"play", b"pause", b"trkf", b"vol_up"} <= set(servidor.recebidas))
    assert await driver.executar("tecla", "inicio") == "invalid_value"
    await driver.parar()


async def test_uma_unidade_que_nao_responde_e_offline_depois_de_dois_polls(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_o_identificar_da_o_mac_que_a_unidade_responde(unidade):
    servidor, driver = await unidade()
    assert await Rotel.identificar(IP) == "0cefaf90125e"
    assert await Rotel.identificar("nao-e-um-ip") is None
    await driver.parar()

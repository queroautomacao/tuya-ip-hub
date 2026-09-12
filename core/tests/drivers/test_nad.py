# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The NAD driver against the two protocols the line speaks: text and binary."""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import nad
from iphub.drivers.nativos.nad import Nad
from iphub.drivers.simulado import ServidorBytes, ServidorLinha

IP = "127.0.0.1"

DE_TEXTO = {
    b"Main.Power?": b"Main.Power=On\r",
    b"Main.Volume?": b"Main.Volume=-40\r",
    b"Main.Mute?": b"Main.Mute=Off\r",
    b"Main.Source?": b"Main.Source=Coaxial 1\r",
}


def _resposta(pergunta: bytes, valor: int) -> bytes:
    """An answer of the binary protocol: ten bytes whose last one carries the value."""
    return pergunta + bytes([0] * (nad.BIN_RESPOSTA - len(pergunta) - 1)) + bytes([valor])


BINARIAS = {
    nad.BIN_PERGUNTA_VOLUME: _resposta(nad.BIN_PERGUNTA_VOLUME, 100),
    nad.BIN_PERGUNTA_ENERGIA: _resposta(nad.BIN_PERGUNTA_ENERGIA, 1),
    nad.BIN_PERGUNTA_MUDO: _resposta(nad.BIN_PERGUNTA_MUDO, 0),
    nad.BIN_PERGUNTA_FONTE: _resposta(nad.BIN_PERGUNTA_FONTE, 7),
    nad.BIN_LIGAR: b"",
    nad.BIN_ECONOMIA + nad.BIN_DESLIGAR: b"",
    nad.BIN_MUDO: b"",
    nad.BIN_SEM_MUDO: b"",
    nad.BIN_VOLUME + bytes([100]): b"",
    nad.BIN_FONTE + bytes([2]): b"",
}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "amplificador-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


@pytest.fixture
async def amplificador(monkeypatch):
    """An amplifier already listening, speaking whichever protocol the test asked for."""
    ligados: list[ServidorLinha | ServidorBytes] = []

    async def abrir(respostas=None, **campos):
        binario = campos.get(nad.CAMPO_PROTOCOLO) == nad.PROTOCOLO_BINARIO
        if binario:
            servidor = ServidorBytes(dict(BINARIAS if respostas is None else respostas))
        else:
            servidor = ServidorLinha(
                dict(DE_TEXTO if respostas is None else respostas), terminador=b"\r"
            )
        await servidor.iniciar()
        ligados.append(servidor)
        alvo = "PORTA_BINARIA" if binario else "PORTA_TEXTO"
        monkeypatch.setattr(nad, alvo, servidor.endereco[1])
        monkeypatch.setattr(nad, "RESPOSTA_S", 0.3)
        return servidor, Nad(_Cadastro(campos=campos))

    yield abrir
    for servidor in ligados:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the amplifier to read what was written to it, on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido():
    validar(Nad.MANIFESTO)
    assert Nad.MANIFESTO.categoria == "amplificador"


async def test_o_protocolo_de_texto_le_o_estado_pela_frase_que_o_aparelho_repete(amplificador):
    servidor, driver = await amplificador()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # -40 dB of a scale that goes from -80 to 0 is 50 on the contract.
    assert estado.volume == 50
    assert estado.mudo is False
    assert estado.fonte == "Coaxial 1"
    await driver.parar()


async def test_a_faixa_de_decibeis_do_cadastro_muda_a_conversao(amplificador):
    servidor, driver = await amplificador(db_minimo="-60", db_maximo="10")
    await driver.atualizar()
    # -40 dB of a scale that goes from -60 to 10 is 29 on the contract.
    assert driver.estado().volume == 29
    assert await driver.executar("volume", 50) is None
    await _ate(lambda: b"Main.Volume=-25" in servidor.recebidas)
    await driver.parar()


async def test_uma_faixa_torta_no_cadastro_volta_para_a_padrao(amplificador):
    servidor, driver = await amplificador(db_minimo="alto", db_maximo="-90")
    await driver.atualizar()
    assert driver.estado().volume == 50
    await driver.parar()


async def test_o_protocolo_de_texto_manda_a_frase_do_protocolo(amplificador):
    servidor, driver = await amplificador()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert await driver.executar("desligar") is None
    assert await driver.executar("mudo", True) is None
    assert await driver.executar("fonte", "Optical 1") is None
    await _ate(
        lambda: (
            {
                b"Main.Power=On",
                b"Main.Power=Off",
                b"Main.Mute=On",
                b"Main.Source=Optical 1",
            }
            <= set(servidor.recebidas)
        )
    )
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("mudo", "sim") == "invalid_value"
    assert await driver.executar("fonte", "Optical 1;drop") == "invalid_value"
    await driver.parar()


async def test_o_protocolo_binario_le_quatro_respostas_de_dez_bytes(amplificador):
    servidor, driver = await amplificador(protocolo="binario")
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # The binary line counts to two hundred and the contract counts to a hundred.
    assert estado.volume == 50
    assert estado.mudo is False
    assert estado.fonte == "Bluetooth"
    assert "Coaxial 1" in estado.fontes
    await driver.parar()


async def test_o_protocolo_binario_manda_os_bytes_do_protocolo(amplificador):
    servidor, driver = await amplificador(protocolo="binario")
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    await _ate(lambda: nad.BIN_LIGAR in servidor.recebidas)
    # Turning it off without the power saving message in front hangs the amplifier.
    assert await driver.executar("desligar") is None
    await _ate(lambda: (nad.BIN_ECONOMIA + nad.BIN_DESLIGAR) in servidor.recebidas)
    assert await driver.executar("volume", 50) is None
    await _ate(lambda: (nad.BIN_VOLUME + bytes([100])) in servidor.recebidas)
    assert await driver.executar("fonte", "Optical 1") is None
    await _ate(lambda: (nad.BIN_FONTE + bytes([2])) in servidor.recebidas)
    assert driver.estado().fonte == "Optical 1"
    await driver.parar()


async def test_o_protocolo_binario_so_aceita_os_nomes_da_tabela_daquela_linha(amplificador):
    servidor, driver = await amplificador(protocolo="binario")
    await driver.atualizar()
    quantas = len(servidor.recebidas)
    assert await driver.executar("fonte", "HDMI 1") == "invalid_value"
    assert await driver.executar("fonte", 2) == "invalid_value"
    assert len(servidor.recebidas) == quantas
    await driver.parar()


async def test_um_protocolo_que_nao_existe_no_cadastro_vira_o_de_texto(amplificador):
    servidor, driver = await amplificador(protocolo="serial")
    await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().fonte == "Coaxial 1"
    await driver.parar()


async def test_um_amplificador_que_nao_responde_e_offline_depois_de_dois_polls(amplificador):
    servidor, driver = await amplificador()
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_um_amplificador_mudo_no_texto_e_erro_do_aparelho(amplificador):
    servidor, driver = await amplificador({b"Main.Volume?": b"Main.Volume=-40\r"})
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"
    await driver.parar()

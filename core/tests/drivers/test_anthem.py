# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Anthem driver against a simulated unit, one semicolon at a time."""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import anthem
from iphub.drivers.nativos.anthem import Anthem
from iphub.drivers.simulado import ServidorLinha

IP = "127.0.0.1"

# A current model, which answers the volume as a percentage and names three inputs.
RESPOSTAS = {
    b"Z1POW?": b"Z1POW1;",
    b"Z1PVOL?": b"Z1PVOL42;",
    b"Z1VOL?": b"Z1VOL-38;",
    b"Z1MUT?": b"Z1MUT0;",
    b"Z1INP?": b"Z1INP2;",
    b"ICN?": b"ICN3;",
    b"ISN01?": b"ISN01Blu-ray;",
    b"ISN02?": b"ISN02TV;",
    b"ISN03?": b"ISN03Vinil;",
    b"IDN?": b"IDN00:11:22:33:44:55;",
}

# An older model, which answers no percentage at all.
SEM_PORCENTO = {chave: valor for chave, valor in RESPOSTAS.items() if chave != b"Z1PVOL?"}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "processador-da-sala"
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
            dict(RESPOSTAS if respostas is None else respostas), terminador=b";"
        )
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(anthem, "PORTA", servidor.endereco[1])
        monkeypatch.setattr(anthem, "RESPOSTA_S", 0.3)
        return servidor, Anthem(_Cadastro(campos=campos))

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the unit to read what was written to it, which it does on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido():
    validar(Anthem.MANIFESTO)
    assert Anthem.MANIFESTO.categoria == "receiver"


async def test_um_poll_le_a_zona_e_os_nomes_que_a_unidade_deu_as_entradas(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.volume == 42
    assert estado.mudo is False
    assert estado.fonte == "TV"
    assert set(estado.fontes) == {"Blu-ray", "TV", "Vinil"}
    await driver.parar()


async def test_um_modelo_sem_porcentagem_tem_o_volume_convertido_dos_decibeis(unidade):
    """The older models answer only the attenuation, which is converted from a floor of -90."""
    servidor, driver = await unidade(SEM_PORCENTO)
    await driver.atualizar()
    # -38 dB of a scale that goes from -90 to 0 is 58 on the contract.
    assert driver.estado().volume == 58
    assert await driver.executar("volume", 50) is None
    await _ate(lambda: b"Z1VOL-45" in servidor.recebidas)
    assert b"Z1PVOL50" not in servidor.recebidas
    await driver.parar()


async def test_um_modelo_com_porcentagem_manda_a_porcentagem(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    assert await driver.executar("volume", 50) is None
    await _ate(lambda: b"Z1PVOL50" in servidor.recebidas)
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("volume", "alto") == "invalid_value"
    await driver.parar()


async def test_ligar_desligar_e_mudo_mandam_a_palavra_do_protocolo(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert await driver.executar("desligar") is None
    assert await driver.executar("mudo", True) is None
    assert await driver.executar("mudo", False) is None
    await _ate(lambda: {b"Z1POW1", b"Z1POW0", b"Z1MUT1", b"Z1MUT0"} <= set(servidor.recebidas))
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_a_entrada_viaja_pelo_nome_que_a_unidade_deu_ou_pelo_numero(unidade):
    servidor, driver = await unidade()
    await driver.atualizar()
    assert await driver.executar("fonte", "Vinil") is None
    await _ate(lambda: b"Z1INP3" in servidor.recebidas)
    assert driver.estado().fonte == "Vinil"
    assert await driver.executar("fonte", "1") is None
    await _ate(lambda: b"Z1INP1" in servidor.recebidas)
    assert driver.estado().fonte == "Blu-ray"
    assert await driver.executar("fonte", "nao_existe") == "invalid_value"
    assert await driver.executar("fonte", "99") == "invalid_value"
    await driver.parar()


async def test_a_zona_do_cadastro_e_o_z_de_todo_comando(unidade):
    servidor, driver = await unidade(
        {
            b"Z2POW?": b"Z2POW1;",
            b"Z2PVOL?": b"Z2PVOL10;",
            b"Z2VOL?": b"Z2VOL-70;",
            b"Z2MUT?": b"Z2MUT0;",
            b"Z2INP?": b"Z2INP1;",
        },
        zona="2",
    )
    await driver.atualizar()
    assert driver.estado().volume == 10
    assert await driver.executar("ligar") is None
    await _ate(lambda: b"Z2POW1" in servidor.recebidas)
    await driver.parar()


async def test_uma_zona_que_nao_existe_no_cadastro_vira_a_primeira(unidade):
    servidor, driver = await unidade(zona="9")
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.parar()


async def test_um_datagrama_que_a_unidade_empurra_sozinha_nao_e_lido_como_resposta(unidade):
    """A command nobody asked is the unit reporting a change, and there is nothing to file
    it as: what matters is that it does not become the answer of another question."""
    servidor, driver = await unidade({**RESPOSTAS, b"Z1POW?": b"Z1ALM01;Z1POW1;"})
    await driver.atualizar()
    assert driver.estado().ligado is True
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


async def test_uma_unidade_muda_no_comando_que_todo_modelo_tem_e_erro_do_aparelho(unidade):
    servidor, driver = await unidade({b"Z1PVOL?": b"Z1PVOL42;"})
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"
    await driver.parar()


async def test_o_identificar_da_o_mac_que_a_unidade_responde(unidade):
    servidor, driver = await unidade()
    assert await Anthem.identificar(IP) == "00:11:22:33:44:55"
    assert await Anthem.identificar("nao-e-um-ip") is None
    await driver.parar()

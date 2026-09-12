# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Pioneer driver against a simulated receiver, one line at a time."""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import pioneer
from iphub.drivers.nativos.pioneer import Pioneer
from iphub.drivers.simulado import ServidorLinha

IP = "127.0.0.1"

# What a receiver with four inputs answers to the burst of questions about its inputs. The
# flag between the number and the name is a character the protocol carries and nobody reads.
ENTRADAS = {
    b"?RGB25": b"RGB25FBD\r\n",
    b"?RGB05": b"RGB05FTV\r\n",
    b"?RGB26": b"RGB26FNETWORK\r\n",
    b"?RGB04": b"RGB04FDVD\r\n",
}

RESPOSTAS = {
    b"?P": b"PWR0\r\n",
    b"?V": b"VOL100\r\n",
    b"?M": b"MUT1\r\n",
    b"?F": b"FN25\r\n",
    **ENTRADAS,
}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "receiver-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


@pytest.fixture
async def receiver(monkeypatch):
    """A receiver already listening, with the driver aimed at the port it took."""
    ligados: list[ServidorLinha] = []

    async def abrir(respostas=None, **campos):
        servidor = ServidorLinha(
            dict(RESPOSTAS if respostas is None else respostas), terminador=b"\r"
        )
        await servidor.iniciar()
        ligados.append(servidor)
        monkeypatch.setattr(pioneer, "PORTAS", (servidor.endereco[1],))
        return servidor, Pioneer(_Cadastro(campos=campos))

    yield abrir
    for servidor in ligados:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the receiver to read what was written to it, which it does on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido():
    validar(Pioneer.MANIFESTO)
    assert Pioneer.MANIFESTO.categoria == "receiver"
    assert set(Pioneer.MANIFESTO.teclas) == {"mais", "menos"}


async def test_um_poll_le_energia_volume_mudo_e_a_entrada_em_frente(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # The receiver counts to 185 and the contract counts to 100.
    assert estado.volume == 54
    assert estado.mudo is False
    assert estado.fonte == "BD"
    assert set(estado.fontes) == {"BD", "TV", "NETWORK", "DVD"}
    await driver.parar()


async def test_a_energia_e_o_mudo_leem_o_protocolo_ao_contrario(receiver):
    """PWR0 is on and MUT0 is muted, which reads backwards and is what the protocol says."""
    servidor, driver = await receiver({**RESPOSTAS, b"?P": b"PWR1\r\n", b"?M": b"MUT0\r\n"})
    await driver.atualizar()
    assert driver.estado().ligado is False
    assert driver.estado().mudo is True
    await driver.parar()


async def test_uma_linha_que_o_receiver_empurra_sozinho_nao_e_a_resposta(receiver):
    """Somebody touching the front panel writes a line the driver must not read as an answer."""
    servidor, driver = await receiver({**RESPOSTAS, b"?P": b"FL0000\r\nPWR0\r\n"})
    await driver.atualizar()
    assert driver.estado().ligado is True
    await driver.parar()


async def test_o_volume_vai_na_escala_do_receiver_e_o_teto_e_do_cadastro(receiver):
    servidor, driver = await receiver(volume_maximo="161")
    await driver.atualizar()
    # 100 of 161 is 62 on the scale of the contract, and 50 of 100 is 80 on the receiver.
    assert driver.estado().volume == 62
    assert await driver.executar("volume", 50) is None
    await _ate(lambda: b"080VL" in servidor.recebidas)
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("volume", "alto") == "invalid_value"
    await driver.parar()


async def test_um_teto_torto_no_cadastro_volta_para_o_da_maioria(receiver):
    servidor, driver = await receiver(volume_maximo="zero")
    await driver.atualizar()
    assert driver.estado().volume == 54
    await driver.parar()


async def test_ligar_desligar_e_mudo_mandam_a_palavra_do_protocolo(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert await driver.executar("desligar") is None
    assert await driver.executar("mudo", True) is None
    assert await driver.executar("mudo", False) is None
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await _ate(
        lambda: (
            [b"PO", b"PF", b"MO", b"MF"]
            == [linha for linha in servidor.recebidas if linha in (b"PO", b"PF", b"MO", b"MF")]
        )
    )
    await driver.parar()


async def test_a_entrada_viaja_pelo_nome_que_o_receiver_deu_e_pelo_numero(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("fonte", "NETWORK") is None
    await _ate(lambda: b"26FN" in servidor.recebidas)
    assert driver.estado().fonte == "NETWORK"
    # A registration made before the first poll carries the number, and it still works.
    assert await driver.executar("fonte", "5") is None
    await _ate(lambda: b"05FN" in servidor.recebidas)
    assert driver.estado().fonte == "TV"
    assert await driver.executar("fonte", "nao_existe") == "invalid_value"
    assert await driver.executar("fonte", 25) == "invalid_value"
    await driver.parar()


async def test_as_teclas_de_volume_sao_as_do_protocolo(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("tecla", "mais") is None
    assert await driver.executar("tecla", "menos") is None
    await _ate(lambda: b"VU" in servidor.recebidas and b"VD" in servidor.recebidas)
    assert await driver.executar("tecla", "inicio") == "invalid_value"
    await driver.parar()


async def test_um_erro_do_receiver_e_erro_do_aparelho_e_nao_estado(receiver):
    servidor, driver = await receiver({**RESPOSTAS, b"?P": b"E06\r\n"})
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"
    await driver.parar()


async def test_um_receiver_que_nao_responde_e_offline_depois_de_dois_polls(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_uma_acao_que_o_driver_nao_declara_diz_isso(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("tocar") == "nao_suportado"
    await driver.parar()

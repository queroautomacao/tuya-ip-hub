# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Arcam driver against a simulated receiver, one frame of bytes at a time."""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import arcam
from iphub.drivers.nativos.arcam import Arcam
from iphub.drivers.simulado import ServidorBytes

IP = "127.0.0.1"


def _pedido(comando: int, dados: bytes = bytes([arcam.PERGUNTA]), zona: int = 1) -> bytes:
    """One command frame, whole: the closing byte is part of it and not a separator, because
    0x0D is also the command of the volume."""
    return bytes([arcam.ABRE, zona, comando, len(dados), *dados, arcam.FECHA])


def _resposta(comando: int, dados: bytes, *, zona: int = 1, ac: int = 0x00) -> bytes:
    """One answer frame, which carries the answer code the command frame does not."""
    return bytes([arcam.ABRE, zona, comando, ac, len(dados), *dados, arcam.FECHA])


RESPOSTAS = {
    _pedido(arcam.CMD_ENERGIA): _resposta(arcam.CMD_ENERGIA, bytes([0x01])),
    _pedido(arcam.CMD_VOLUME): _resposta(arcam.CMD_VOLUME, bytes([50])),
    _pedido(arcam.CMD_MUDO): _resposta(arcam.CMD_MUDO, bytes([0x01])),
    _pedido(arcam.CMD_FONTE): _resposta(arcam.CMD_FONTE, bytes([0x02])),
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
    ligados: list[ServidorBytes] = []

    async def abrir(respostas=None, **campos):
        servidor = ServidorBytes(dict(RESPOSTAS if respostas is None else respostas))
        await servidor.iniciar()
        ligados.append(servidor)
        monkeypatch.setattr(arcam, "PORTA", servidor.endereco[1])
        monkeypatch.setattr(arcam, "RESPOSTA_S", 0.3)
        return servidor, Arcam(_Cadastro(campos=campos))

    yield abrir
    for servidor in ligados:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the receiver to read what was written to it, on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido():
    validar(Arcam.MANIFESTO)
    assert Arcam.MANIFESTO.categoria == "receiver"


async def test_um_poll_le_as_quatro_perguntas_da_zona(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # The receiver counts to ninety nine and the contract counts to a hundred.
    assert estado.volume == 51
    # One is NOT muted on this command, which is the byte that reads backwards.
    assert estado.mudo is False
    assert estado.fonte == "BD"
    assert "NET" in estado.fontes
    await driver.parar()


async def test_o_mudo_do_protocolo_le_zero_como_mudo(receiver):
    servidor, driver = await receiver(
        {**RESPOSTAS, _pedido(arcam.CMD_MUDO): _resposta(arcam.CMD_MUDO, bytes([0x00]))}
    )
    await driver.atualizar()
    assert driver.estado().mudo is True
    await driver.parar()


async def test_ligar_desligar_volume_e_mudo_mandam_o_byte_do_protocolo(receiver):
    servidor, driver = await receiver(
        {
            **RESPOSTAS,
            _pedido(arcam.CMD_ENERGIA, bytes([0x01])): _resposta(arcam.CMD_ENERGIA, bytes([0x01])),
            _pedido(arcam.CMD_ENERGIA, bytes([0x00])): _resposta(arcam.CMD_ENERGIA, bytes([0x00])),
            _pedido(arcam.CMD_VOLUME, bytes([50])): _resposta(arcam.CMD_VOLUME, bytes([50])),
            _pedido(arcam.CMD_MUDO, bytes([0x00])): _resposta(arcam.CMD_MUDO, bytes([0x00])),
        }
    )
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert await driver.executar("desligar") is None
    assert await driver.executar("volume", 50) is None
    assert await driver.executar("mudo", True) is None
    await _ate(
        lambda: (
            {
                _pedido(arcam.CMD_ENERGIA, bytes([0x01])),
                _pedido(arcam.CMD_ENERGIA, bytes([0x00])),
                _pedido(arcam.CMD_VOLUME, bytes([50])),
                _pedido(arcam.CMD_MUDO, bytes([0x00])),
            }
            <= set(servidor.recebidas)
        )
    )
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_a_entrada_viaja_pelo_nome_da_tabela_do_modelo(receiver):
    servidor, driver = await receiver(
        {
            **RESPOSTAS,
            _pedido(arcam.CMD_FONTE, bytes([0x0E])): _resposta(arcam.CMD_FONTE, bytes([0x0E])),
        }
    )
    await driver.atualizar()
    assert await driver.executar("fonte", "net") is None
    await _ate(lambda: _pedido(arcam.CMD_FONTE, bytes([0x0E])) in servidor.recebidas)
    assert driver.estado().fonte == "NET"
    assert await driver.executar("fonte", "HDMI 1") == "invalid_value"
    assert await driver.executar("fonte", 2) == "invalid_value"
    await driver.parar()


async def test_uma_recusa_do_receiver_diz_valor_invalido_e_nao_aparelho_quebrado(receiver):
    """The receiver names why it refused, and none of those reasons is a broken device."""
    servidor, driver = await receiver(
        {
            **RESPOSTAS,
            _pedido(arcam.CMD_FONTE, bytes([0x0E])): _resposta(arcam.CMD_FONTE, b"", ac=0x85),
        }
    )
    await driver.atualizar()
    assert await driver.executar("fonte", "NET") == "invalid_value"
    assert driver.estado().online is True
    await driver.parar()


async def test_um_quadro_de_outra_zona_nao_e_lido_como_resposta(receiver):
    """The receiver pushes the state of another zone when the front panel moves."""
    outra = _resposta(arcam.CMD_ENERGIA, bytes([0x00]), zona=2)
    servidor, driver = await receiver(
        {
            **RESPOSTAS,
            _pedido(arcam.CMD_ENERGIA): outra + _resposta(arcam.CMD_ENERGIA, bytes([0x01])),
        }
    )
    await driver.atualizar()
    assert driver.estado().ligado is True
    await driver.parar()


async def test_a_zona_do_cadastro_e_o_byte_de_todo_quadro(receiver):
    servidor, driver = await receiver(
        {
            _pedido(arcam.CMD_ENERGIA, zona=2): _resposta(arcam.CMD_ENERGIA, bytes([0x01]), zona=2),
            _pedido(arcam.CMD_VOLUME, zona=2): _resposta(arcam.CMD_VOLUME, bytes([25]), zona=2),
            _pedido(arcam.CMD_MUDO, zona=2): _resposta(arcam.CMD_MUDO, bytes([0x01]), zona=2),
            _pedido(arcam.CMD_FONTE, zona=2): _resposta(arcam.CMD_FONTE, bytes([0x01]), zona=2),
        },
        zona="2",
    )
    await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().fonte == "CD"
    await driver.parar()


async def test_uma_zona_que_nao_existe_no_cadastro_vira_a_primeira(receiver):
    servidor, driver = await receiver(zona="7")
    await driver.atualizar()
    assert driver.estado().online is True
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


async def test_um_receiver_mudo_no_quadro_e_erro_do_aparelho(receiver):
    servidor, driver = await receiver({})
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"
    await driver.parar()

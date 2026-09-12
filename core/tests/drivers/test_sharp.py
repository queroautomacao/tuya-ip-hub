# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Sharp AQUOS driver against a simulated television, eight characters at a time."""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import sharp
from iphub.drivers.nativos.sharp import Sharp
from iphub.drivers.simulado import ServidorLinha

IP = "127.0.0.1"

# The television answers OK to every command it accepted, and the value to every question.
COMANDOS = {
    f"{nome}{parametro}".ljust(sharp.LARGURA).encode("ascii"): b"OK\r"
    for nome, parametro in (
        ("POWR", "1"),
        ("POWR", "0"),
        ("VOLM", "40"),
        ("VOLM", "18"),
        ("MUTE", "1"),
        ("MUTE", "2"),
        ("IAVD", "3"),
        ("ITVD", "0"),
        ("CHUP", "0"),
        ("CHDW", "0"),
    )
}

RESPOSTAS = {
    b"POWR????": b"1\r",
    b"VOLM????": b"30\r",
    b"MUTE????": b"2\r",
    b"IAVD????": b"2\r",
    **COMANDOS,
}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


@pytest.fixture
async def tv(monkeypatch):
    """A television already listening, with the driver aimed at the port it took."""
    ligadas: list[ServidorLinha] = []

    async def abrir(respostas=None, segredos=None, **campos):
        servidor = ServidorLinha(
            dict(RESPOSTAS if respostas is None else respostas), terminador=b"\r"
        )
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(sharp, "PORTA", servidor.endereco[1])
        monkeypatch.setattr(sharp, "RESPOSTA_S", 0.3)
        return servidor, Sharp(_Cadastro(campos=campos, segredos=segredos or {}))

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for the television to read what was written to it, on its own time."""
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def test_o_manifesto_e_valido():
    validar(Sharp.MANIFESTO)
    assert Sharp.MANIFESTO.categoria == "tv"


async def test_um_poll_le_energia_volume_mudo_e_a_entrada(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # The television counts to sixty and the contract counts to a hundred.
    assert estado.volume == 50
    assert estado.mudo is False
    assert estado.fonte == "2"
    await driver.parar()


async def test_uma_tv_desligada_nao_e_perguntada_o_resto(tv):
    """A television that is off answers nothing else, so nothing else is asked."""
    servidor, driver = await tv({**RESPOSTAS, b"POWR????": b"0\r"})
    await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().ligado is False
    assert b"VOLM????" not in servidor.recebidas
    await driver.parar()


async def test_a_tv_no_sintonizador_recusa_a_pergunta_da_entrada(tv):
    """An error to the input question is how this protocol says the tuner is in front."""
    servidor, driver = await tv({**RESPOSTAS, b"IAVD????": b"ERR\r"})
    await driver.atualizar()
    assert driver.estado().fonte == "tv"
    await driver.parar()


async def test_o_comando_tem_oito_caracteres_com_o_parametro_a_direita(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    await _ate(lambda: b"POWR1   " in servidor.recebidas)
    assert await driver.executar("desligar") is None
    await _ate(lambda: b"POWR0   " in servidor.recebidas)
    await driver.parar()


async def test_o_volume_vai_na_escala_da_tv_e_o_teto_e_do_cadastro(tv):
    servidor, driver = await tv(volume_maximo="100")
    await driver.atualizar()
    assert driver.estado().volume == 30
    assert await driver.executar("volume", 40) is None
    await _ate(lambda: b"VOLM40  " in servidor.recebidas)
    assert await driver.executar("volume", 101) == "invalid_value"
    await driver.parar()


async def test_o_mudo_e_um_para_ligado_e_dois_para_desligado(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("mudo", True) is None
    await _ate(lambda: b"MUTE1   " in servidor.recebidas)
    assert await driver.executar("mudo", False) is None
    await _ate(lambda: b"MUTE2   " in servidor.recebidas)
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_a_entrada_e_um_numero_ou_a_palavra_do_sintonizador(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("fonte", "3") is None
    await _ate(lambda: b"IAVD3   " in servidor.recebidas)
    assert await driver.executar("fonte", "tv") is None
    await _ate(lambda: b"ITVD0   " in servidor.recebidas)
    assert await driver.executar("fonte", "0") == "invalid_value"
    assert await driver.executar("fonte", "hdmi1") == "invalid_value"
    assert await driver.executar("fonte", 3) == "invalid_value"
    await driver.parar()


async def test_um_erro_da_tv_a_um_comando_e_valor_invalido(tv):
    servidor, driver = await tv({**RESPOSTAS, b"IAVD9   ": b"ERR\r"})
    await driver.atualizar()
    assert await driver.executar("fonte", "9") == "invalid_value"
    assert driver.estado().online is True
    await driver.parar()


async def test_o_usuario_e_a_senha_vao_uma_linha_cada_antes_do_primeiro_comando(tv):
    servidor, driver = await tv(
        {**RESPOSTAS, b"operador": b"OK\r", b"segredo": b"OK\r"},
        segredos={"senha": "segredo"},
        usuario="operador",
    )
    await driver.atualizar()
    assert servidor.recebidas[:2] == [b"operador", b"segredo"]
    assert driver.estado().online is True
    await driver.parar()


async def test_a_senha_nunca_aparece_na_transcricao(tv, caplog):
    """The log of this hub carries what was asked, never what proves who is asking."""
    caplog.set_level("DEBUG", logger="iphub.drivers.nativos.sharp")
    servidor, driver = await tv(
        {**RESPOSTAS, b"operador": b"OK\r", b"segredo": b"OK\r"},
        segredos={"senha": "segredo"},
        usuario="operador",
    )
    await driver.atualizar()
    assert "segredo" not in caplog.text
    assert "login" in caplog.text
    await driver.parar()


async def test_as_teclas_de_canal_sao_comandos_proprios(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("tecla", "canal_mais") is None
    await _ate(lambda: b"CHUP0   " in servidor.recebidas)
    assert await driver.executar("tecla", "inicio") == "invalid_value"
    await driver.parar()


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
    await driver.parar()

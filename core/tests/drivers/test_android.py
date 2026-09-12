# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Android TV driver against a simulated television, both ports and the whole pairing.

The television of this file computes the pairing code from the two certificates exactly as
the protocol says, so what the tests prove is the hash of the driver and not a string agreed
between two files.
"""

import asyncio
from dataclasses import dataclass, field

import pytest

from iphub.drivers import protobuf
from iphub.drivers.manifesto import Auth, validar
from iphub.drivers.nativos import android
from iphub.drivers.nativos.android import TvAndroid
from iphub.drivers.simulado import ServidorAndroid

IP = "127.0.0.1"


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


@pytest.fixture(scope="module")
def par():
    """One key and certificate for the whole file: generating an RSA key costs real time."""
    return android._gerar_certificado()


@pytest.fixture
async def tv(par, monkeypatch):
    """A television already listening, with the two ports of it aimed at the driver."""
    ligadas: list[ServidorAndroid] = []

    async def abrir(**extra):
        certificado, _chave = par
        servidor = ServidorAndroid(certificado, **extra)
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(android, "PORTA_REMOTO", servidor.endereco[1])
        monkeypatch.setattr(android, "PORTA_PAREAMENTO", servidor.endereco_pareamento[1])
        return servidor

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


def _driver(par, **campos) -> TvAndroid:
    certificado, chave = par
    return TvAndroid(
        _Cadastro(
            campos=campos,
            segredos={android.CAMPO_CERTIFICADO: certificado, android.CAMPO_CHAVE: chave},
        )
    )


async def _ate(condicao, prazo: float = 5.0) -> None:
    """Waits for what the other side of the socket does on its own time.

    A command is written and answered without the caller waiting for either, so a test that
    read right after the call would be reading before the television had a chance to answer.
    """
    async with asyncio.timeout(prazo):
        while not condicao():
            await asyncio.sleep(0.01)


def _mensagens(servidor: ServidorAndroid, numero: int) -> list[dict]:
    return [
        protobuf.mensagem(campos, numero)
        for campos in servidor.recebidas
        if protobuf.tem(campos, numero)
    ]


def test_o_manifesto_e_valido_e_nao_promete_nivel_de_volume():
    """The protocol reports a volume and has no way to set one, so the capability is absent.

    A bar the device refuses is the bug this project already paid for on another television:
    the panel draws the level only for a driver that declares it.
    """
    validar(TvAndroid.MANIFESTO)
    assert TvAndroid.MANIFESTO.auth == Auth.CODIGO
    assert "volume" not in TvAndroid.MANIFESTO.capacidades
    assert "mudo" in TvAndroid.MANIFESTO.capacidades
    assert set(TvAndroid.MANIFESTO.teclas) <= set(android.CODIGOS_DE_TECLA)


async def test_o_pareamento_inteiro_com_o_codigo_que_a_tv_calcula(par, tv):
    """Two presses of pair: the first brings the code to the screen, the second closes it."""
    servidor = await tv()
    driver = _driver(par)
    assert await driver.autenticar() == "aguardando"
    assert servidor.codigo, "the television did not see the certificate of the client"
    driver.cadastro.campos[android.CAMPO_CODIGO] = servidor.codigo
    assert await driver.autenticar() == "pareado"
    assert servidor.pareada is True
    credenciais = driver.credenciais_do_aparelho()
    assert set(credenciais) == {android.CAMPO_CERTIFICADO, android.CAMPO_CHAVE}
    await driver.parar()


async def test_um_codigo_errado_e_recusado_antes_de_ir_para_a_tv(par, tv):
    """The first two characters are a check byte of the secret, so a typo never travels."""
    servidor = await tv()
    driver = _driver(par)
    assert await driver.autenticar() == "aguardando"
    driver.cadastro.campos[android.CAMPO_CODIGO] = "00" + servidor.codigo[2:]
    assert await driver.autenticar() == "falhou"
    assert servidor.pareada is False
    assert not _mensagens(servidor, android.P_SEGREDO)
    await driver.parar()


@pytest.mark.parametrize("codigo", ["", "12345", "1234567", "zzzzzz"])
async def test_um_codigo_fora_do_formato_nao_abre_o_pareamento(par, tv, codigo):
    servidor = await tv()
    driver = _driver(par)
    assert await driver.autenticar() == "aguardando"
    driver.cadastro.campos[android.CAMPO_CODIGO] = codigo
    esperado = "aguardando" if codigo == "" else "falhou"
    assert await driver.autenticar() == esperado
    assert servidor.pareada is False
    await driver.parar()


async def test_o_poll_abre_a_conexao_e_a_tv_diz_se_esta_ligada(par, tv):
    await tv(ligada=True, volume=(18, 24, False))
    driver = _driver(par)
    await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().ligado is True
    await _ate(lambda: driver.estado().mudo is not None)
    assert driver.estado().mudo is False
    # The level travels for the record; 18 of 24 is 75 on the scale of the contract.
    assert driver.estado().volume == 75
    await driver.parar()


async def test_uma_tv_desligada_diz_que_esta_desligada(par, tv):
    await tv(ligada=False, volume=None)
    driver = _driver(par)
    await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().ligado is False
    await driver.parar()


async def test_a_tecla_de_energia_so_vai_quando_o_estado_pede(par, tv):
    """One key toggles the power, so asking for the state it is already in sends nothing."""
    servidor = await tv(ligada=False)
    driver = _driver(par)
    await driver.atualizar()
    assert await driver.executar("desligar") is None
    assert not _mensagens(servidor, android.R_TECLA)
    assert await driver.executar("ligar") is None
    await _ate(lambda: _mensagens(servidor, android.R_TECLA))
    (tecla,) = _mensagens(servidor, android.R_TECLA)
    assert protobuf.inteiro(tecla, android.R_CODIGO_DA_TECLA) == android.TECLA_ENERGIA
    assert protobuf.inteiro(tecla, android.R_DIRECAO) == android.TECLA_CURTA
    await driver.parar()


async def test_as_teclas_do_controle_viajam_com_o_codigo_do_protocolo(par, tv):
    servidor = await tv()
    driver = _driver(par)
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    assert await driver.executar("tecla", "ok") is None
    assert await driver.executar("pausar") is None
    assert await driver.executar("parar") is None
    await _ate(lambda: len(_mensagens(servidor, android.R_TECLA)) == 4)
    codigos = [
        protobuf.inteiro(tecla, android.R_CODIGO_DA_TECLA)
        for tecla in _mensagens(servidor, android.R_TECLA)
    ]
    assert codigos == [3, 23, 85, 86]
    assert await driver.executar("tecla", "nao_existe") == "invalid_value"
    assert await driver.executar("tecla", 7) == "invalid_value"
    await driver.parar()


async def test_o_mudo_e_uma_tecla_e_o_estado_decide_se_ela_vai(par, tv):
    servidor = await tv(volume=(10, 20, False))
    driver = _driver(par)
    await driver.atualizar()
    # The state of the mute comes in a message of its own, right after the one that says the
    # television is ready, and it is what decides whether the key travels at all.
    await _ate(lambda: driver.estado().mudo is not None)
    assert await driver.executar("mudo", False) is None
    assert not _mensagens(servidor, android.R_TECLA)
    assert await driver.executar("mudo", True) is None
    await _ate(lambda: _mensagens(servidor, android.R_TECLA))
    (tecla,) = _mensagens(servidor, android.R_TECLA)
    assert protobuf.inteiro(tecla, android.R_CODIGO_DA_TECLA) == android.TECLA_MUDO
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_um_atalho_abre_o_app_pelo_link(par, tv):
    servidor = await tv()
    driver = _driver(par)
    await driver.atualizar()
    assert await driver.executar("atalho", "market://launch?id=com.netflix.ninja") is None
    await _ate(lambda: _mensagens(servidor, android.R_ABRIR_APP))
    (pedido,) = _mensagens(servidor, android.R_ABRIR_APP)
    assert protobuf.texto(pedido, android.R_LINK) == "market://launch?id=com.netflix.ninja"
    assert await driver.executar("atalho", "  ") == "invalid_value"
    assert await driver.executar("atalho", 5) == "invalid_value"
    await driver.parar()


async def test_o_ping_da_tv_e_respondido_ou_ela_derruba_a_conexao(par, tv):
    servidor = await tv()
    driver = _driver(par)
    await driver.atualizar()
    await servidor.perguntar_o_ping(7)
    await _ate(lambda: _mensagens(servidor, android.R_PONG))
    (pong,) = _mensagens(servidor, android.R_PONG)
    assert protobuf.inteiro(pong, android.R_VAL1) == 7
    await driver.parar()


async def test_um_cadastro_sem_certificado_pede_pareamento_em_vez_de_conectar(tv):
    await tv()
    driver = TvAndroid(_Cadastro())
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"
    await driver.parar()


async def test_uma_tv_que_nao_responde_e_offline_depois_de_dois_polls(par, tv, monkeypatch):
    servidor = await tv()
    driver = _driver(par)
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    monkeypatch.setattr(android, "CONEXAO_S", 0.3)
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_o_identificar_da_a_impressao_do_certificado_da_tv(par, tv):
    """The certificate travels in the handshake, so an address is recognized before pairing."""
    await tv()
    primeira = await TvAndroid.identificar(IP)
    segunda = await TvAndroid.identificar(IP)
    assert primeira and primeira == segunda
    assert len(primeira) == 32
    assert await TvAndroid.identificar("nao-e-um-ip") is None

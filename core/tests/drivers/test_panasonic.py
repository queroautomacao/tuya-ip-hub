# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Panasonic Viera driver against a simulated television speaking SOAP on two paths."""

from dataclasses import dataclass, field

import pytest
from aiohttp import web

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import panasonic
from iphub.drivers.nativos.panasonic import Panasonic
from iphub.drivers.simulado import ServidorHttp

IP = "127.0.0.1"

CABECALHO = '<?xml version="1.0" encoding="utf-8"?>'
CORPO = (
    f'{CABECALHO}<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
    "<s:Body>{dentro}</s:Body></s:Envelope>"
)
DESCRICAO = (
    f"{CABECALHO}<root><device><UDN>uuid:4d454930-0100-1000-8001-aabbccddeeff</UDN>"
    "<friendlyName>VIERA TX-50</friendlyName></device></root>"
)
CIFRADA = (
    f"{CABECALHO}<s:Envelope><s:Body><s:Fault><detail>"
    "<errorCode>600</errorCode></detail></s:Fault></s:Body></s:Envelope>"
)


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Tv(ServidorHttp):
    """A television that answers the renderer, the key service and its own description."""

    def __init__(self, *, volume: int = 12, mudo: int = 0, cifrada: bool = False) -> None:
        super().__init__({})
        self.volume = volume
        self.mudo = mudo
        self.cifrada = cifrada
        self.teclas: list[str] = []
        self.acoes: list[str] = []

    async def _atender(self, request: web.Request) -> web.Response:
        texto = await request.text()
        if request.path == panasonic.CAMINHO_DESCRICAO:
            return web.Response(status=200, text=DESCRICAO)
        acao = request.headers.get("SOAPACTION", "").rpartition("#")[2].strip('"')
        self.acoes.append(acao)
        if self.cifrada:
            return web.Response(status=500, text=CIFRADA)
        if acao == "X_SendKey":
            self.teclas.append(texto.partition("<X_KeyEvent>")[2].partition("<")[0])
            return web.Response(status=200, text=CORPO.format(dentro=""))
        if acao == "GetVolume":
            return web.Response(
                status=200,
                text=CORPO.format(dentro=f"<CurrentVolume>{self.volume}</CurrentVolume>"),
            )
        if acao == "GetMute":
            return web.Response(
                status=200, text=CORPO.format(dentro=f"<CurrentMute>{self.mudo}</CurrentMute>")
            )
        if acao == "SetVolume":
            self.volume = int(texto.partition("<DesiredVolume>")[2].partition("<")[0])
            return web.Response(status=200, text=CORPO.format(dentro=""))
        if acao == "SetMute":
            self.mudo = int(texto.partition("<DesiredMute>")[2].partition("<")[0])
            return web.Response(status=200, text=CORPO.format(dentro=""))
        return web.Response(status=500, text="")


@pytest.fixture
async def tv(monkeypatch):
    """A television already listening, with the driver aimed at the port it took."""
    ligadas: list[_Tv] = []

    async def abrir(campos=None, **extra):
        servidor = _Tv(**extra)
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(panasonic, "PORTA", servidor.endereco[1])
        return servidor, Panasonic(_Cadastro(campos=campos or {}))

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


def test_o_manifesto_e_valido_e_nao_promete_entrada():
    """The protocol only cycles the inputs, and a cycle cannot say where it stopped."""
    validar(Panasonic.MANIFESTO)
    assert Panasonic.MANIFESTO.categoria == "tv"
    assert "fonte" not in Panasonic.MANIFESTO.capacidades
    assert "volume" in Panasonic.MANIFESTO.capacidades


async def test_um_poll_le_o_volume_e_o_mudo_do_renderizador(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.volume == 12
    assert estado.mudo is False
    await driver.parar()


async def test_o_volume_e_o_mudo_vao_pelo_renderizador(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("volume", 30) is None
    assert servidor.volume == 30
    assert driver.estado().volume == 30
    assert await driver.executar("mudo", True) is None
    assert servidor.mudo == 1
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("volume", True) == "invalid_value"
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_uma_tecla_vai_pelo_nome_do_controle(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    assert servidor.teclas[-1] == "NRC_HOME-ONOFF"
    assert await driver.executar("pausar") is None
    assert servidor.teclas[-1] == "NRC_PAUSE-ONOFF"
    assert await driver.executar("tecla", "nao_existe") == "invalid_value"
    await driver.parar()


async def test_desligar_e_a_tecla_de_energia_e_ligar_na_rede_e_a_mesma(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("desligar") is None
    assert servidor.teclas[-1] == "NRC_POWER-ONOFF"
    assert driver.estado().ligado is False
    # The television is still answering, so the key is the way back on.
    assert await driver.executar("ligar") is None
    assert servidor.teclas[-1] == "NRC_POWER-ONOFF"
    assert driver.estado().ligado is True
    await driver.parar()


async def test_uma_tv_fora_da_rede_so_liga_pelo_pacote_magico(tv, monkeypatch):
    enviados: list[bytes] = []
    monkeypatch.setattr(panasonic, "_soprar", enviados.append)
    servidor, driver = await tv()
    await servidor.parar()
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    # Without a MAC there is nowhere to send the packet, and the driver says so.
    assert await driver.executar("ligar") == "invalid_value"
    driver.cadastro.campos[panasonic.CAMPO_MAC] = "AA:BB:CC:DD:EE:FF"
    assert await driver.executar("ligar") is None
    assert enviados == [bytes.fromhex("aabbccddeeff")]
    await driver.parar()


async def test_uma_tv_cifrada_de_2018_e_recusada_com_codigo(tv):
    """That television wants a PIN and a cipher, which is another protocol on the same port."""
    servidor, driver = await tv(cifrada=True)
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "nao_suportado"
    assert await driver.executar("tecla", "inicio") == "nao_suportado"
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


async def test_o_identificar_da_o_udn_da_descricao(tv):
    servidor, driver = await tv()
    assert await Panasonic.identificar(IP) == "uuid:4d454930-0100-1000-8001-aabbccddeeff"
    assert await Panasonic.identificar("nao-e-um-ip") is None
    await driver.parar()

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The LG NetCast driver against a simulated television of the generation before webOS."""

from dataclasses import dataclass, field

import pytest
from aiohttp import web

from iphub.drivers.manifesto import Auth, validar
from iphub.drivers.nativos import lg_netcast
from iphub.drivers.nativos.lg_netcast import LgNetcast
from iphub.drivers.simulado import ServidorHttp

IP = "127.0.0.1"
CHAVE = "123456"

VOLUME = (
    '<?xml version="1.0" encoding="utf-8"?><envelope><dataList><data>'
    "<level>17</level><mute>false</mute></data></dataList></envelope>"
)
RAIZ = (
    '<?xml version="1.0" encoding="utf-8"?><root><device>'
    "<uuid>abc-123</uuid><modelName>47LM7600</modelName></device></root>"
)


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Tv(ServidorHttp):
    """A television that answers the three paths of this protocol."""

    def __init__(self, *, chave: str = CHAVE, volume: str = VOLUME) -> None:
        super().__init__({})
        self.chave = chave
        self.volume = volume
        self.pedidos: list[tuple[str, str]] = []
        self.mostrou_a_chave = False

    async def _atender(self, request: web.Request) -> web.Response:
        corpo = await request.text()
        alvo = request.query.get("target", "")
        self.pedidos.append((request.path, corpo or alvo))
        if request.path.endswith("/auth"):
            if "AuthKeyReq" in corpo:
                self.mostrou_a_chave = True
                return web.Response(status=200, text="<envelope />")
            if f"<value>{self.chave}</value>" in corpo:
                return web.Response(status=200, text="<envelope><session>s-1</session></envelope>")
            return web.Response(status=401, text="")
        if request.path.endswith("/command"):
            if "<session>s-1</session>" not in corpo:
                return web.Response(status=401, text="")
            return web.Response(status=200, text="<envelope />")
        if alvo == "rootservice.xml":
            return web.Response(status=200, text=RAIZ)
        if alvo == "volume_info":
            return web.Response(status=200, text=self.volume)
        return web.Response(status=404, text="")


@pytest.fixture
async def tv(monkeypatch):
    """A television already listening, with the driver aimed at the port it took."""
    ligadas: list[_Tv] = []

    async def abrir(segredos=None, **extra):
        campos = extra.pop("campos", {})
        servidor = _Tv(**extra)
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(lg_netcast, "PORTA", servidor.endereco[1])
        return servidor, LgNetcast(_Cadastro(campos=campos, segredos=segredos or {}))

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


def test_o_manifesto_e_valido_e_nao_promete_nivel_de_volume():
    """This protocol has no way to set a level, so the capability is absent and the keys are."""
    validar(LgNetcast.MANIFESTO)
    assert LgNetcast.MANIFESTO.auth == Auth.CODIGO
    assert "volume" not in LgNetcast.MANIFESTO.capacidades
    assert {"mais", "menos"} <= set(LgNetcast.MANIFESTO.teclas)


async def test_o_pareamento_e_a_chave_na_tela_e_depois_a_sessao(tv):
    servidor, driver = await tv()
    assert await driver.autenticar() == "aguardando"
    assert servidor.mostrou_a_chave is True
    driver.cadastro.segredos[lg_netcast.CAMPO_CHAVE] = CHAVE
    assert await driver.autenticar() == "pareado"
    await driver.parar()


async def test_uma_chave_errada_falha_o_pareamento(tv):
    servidor, driver = await tv(segredos={lg_netcast.CAMPO_CHAVE: "000000"})
    assert await driver.autenticar() == "falhou"
    await driver.parar()


async def test_um_poll_le_o_volume_e_o_mudo_da_televisao(tv):
    servidor, driver = await tv(segredos={lg_netcast.CAMPO_CHAVE: CHAVE})
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.volume == 17
    assert estado.mudo is False
    await driver.parar()


async def test_uma_tecla_vai_dentro_da_sessao_e_pelo_numero_do_protocolo(tv):
    servidor, driver = await tv(segredos={lg_netcast.CAMPO_CHAVE: CHAVE})
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    comandos = [corpo for caminho, corpo in servidor.pedidos if caminho.endswith("/command")]
    assert "<value>21</value>" in comandos[-1]
    assert "<session>s-1</session>" in comandos[-1]
    assert await driver.executar("pausar") is None
    assert await driver.executar("tecla", "nao_existe") == "invalid_value"
    await driver.parar()


async def test_o_mudo_e_uma_tecla_e_o_estado_decide_se_ela_vai(tv):
    servidor, driver = await tv(segredos={lg_netcast.CAMPO_CHAVE: CHAVE})
    await driver.atualizar()
    quantos = len([1 for caminho, _ in servidor.pedidos if caminho.endswith("/command")])
    assert await driver.executar("mudo", False) is None
    assert quantos == len([1 for caminho, _ in servidor.pedidos if caminho.endswith("/command")])
    assert await driver.executar("mudo", True) is None
    comandos = [corpo for caminho, corpo in servidor.pedidos if caminho.endswith("/command")]
    assert "<value>26</value>" in comandos[-1]
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_desligar_e_a_tecla_de_energia_e_ligar_e_o_pacote_magico(tv, monkeypatch):
    enviados: list[bytes] = []
    monkeypatch.setattr(lg_netcast, "_soprar", enviados.append)
    servidor, driver = await tv(segredos={lg_netcast.CAMPO_CHAVE: CHAVE})
    await driver.atualizar()
    assert await driver.executar("desligar") is None
    comandos = [corpo for caminho, corpo in servidor.pedidos if caminho.endswith("/command")]
    assert "<value>1</value>" in comandos[-1]
    assert driver.estado().ligado is False
    # Without a MAC there is nowhere to send the packet, and the television says so.
    assert await driver.executar("ligar") == "invalid_value"
    driver.cadastro.campos[lg_netcast.CAMPO_MAC] = "AA:BB:CC:DD:EE:FF"
    assert await driver.executar("ligar") is None
    assert enviados == [bytes.fromhex("aabbccddeeff")]
    assert await driver.parar() is None


async def test_um_cadastro_sem_chave_pede_pareamento(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"
    await driver.parar()


async def test_uma_tv_que_nao_responde_e_offline_depois_de_dois_polls(tv):
    servidor, driver = await tv(segredos={lg_netcast.CAMPO_CHAVE: CHAVE})
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_o_identificar_da_o_uuid_da_televisao(tv):
    servidor, driver = await tv()
    assert await LgNetcast.identificar(IP) == "abc-123"
    assert await LgNetcast.identificar("nao-e-um-ip") is None
    await driver.parar()

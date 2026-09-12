# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Philips driver against a simulated television speaking Jointspace on one version."""

import json
from dataclasses import dataclass, field

import pytest
from aiohttp import web

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import philips
from iphub.drivers.nativos.philips import Philips
from iphub.drivers.simulado import ServidorHttp

IP = "127.0.0.1"

ENTRADAS = {
    "hdmi1": {"name": "Blu-ray"},
    "hdmi2": {"name": "HDMI 2"},
    "tv": {"name": "Televisão"},
}


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Tv(ServidorHttp):
    """A television of one API version, which answers 404 to the paths of the other."""

    def __init__(self, *, versao: str = "1", entradas: dict | None = None) -> None:
        super().__init__({})
        self.versao = versao
        self.entradas = ENTRADAS if entradas is None else entradas
        self.audio = {"muted": False, "current": 24, "min": 0, "max": 60}
        self.fonte = "hdmi1"
        self.teclas: list[str] = []
        self.vistos: list[tuple[str, str, str]] = []

    async def _atender(self, request: web.Request) -> web.Response:
        texto = await request.text()
        self.vistos.append((request.method, request.path, texto))
        versao, _, caminho = request.path.lstrip("/").partition("/")
        if versao != self.versao:
            return web.Response(status=404, text="")
        if caminho == "system":
            return _json({"name": "Philips TV", "serialnumber": "PH-9", "model": "42PFL"})
        if caminho == "audio/volume" and request.method == "GET":
            return _json(self.audio)
        if caminho == "audio/volume":
            self.audio.update(json.loads(texto))
            return _json({})
        if caminho == "input/key":
            self.teclas.append(json.loads(texto)["key"])
            return _json({})
        if caminho == "sources":
            if not self.entradas:
                return web.Response(status=404, text="")
            return _json(self.entradas)
        if caminho == "sources/current" and request.method == "GET":
            return _json({"id": self.fonte})
        if caminho == "sources/current":
            self.fonte = json.loads(texto)["id"]
            return _json({})
        return web.Response(status=404, text="")


def _json(dado: dict) -> web.Response:
    return web.Response(status=200, text=json.dumps(dado), content_type="application/json")


@pytest.fixture
async def tv(monkeypatch):
    """A television already listening, with the driver aimed at the port it took."""
    ligadas: list[_Tv] = []

    async def abrir(campos=None, **extra):
        servidor = _Tv(**extra)
        await servidor.iniciar()
        ligadas.append(servidor)
        monkeypatch.setattr(philips, "PORTA", servidor.endereco[1])
        return servidor, Philips(_Cadastro(campos=campos or {}))

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


def test_o_manifesto_e_valido():
    validar(Philips.MANIFESTO)
    assert Philips.MANIFESTO.categoria == "tv"
    assert {"volume", "fonte"} <= set(Philips.MANIFESTO.capacidades)


async def test_a_versao_da_api_e_perguntada_a_televisao(tv):
    """The newest is asked first, and the set of 2012 only answers the older number."""
    servidor, driver = await tv()
    await driver.atualizar()
    perguntadas = [caminho for _, caminho, _ in servidor.vistos if caminho.endswith("system")]
    assert perguntadas == ["/5/system", "/1/system"]
    assert driver.estado().online is True
    # The version is learned once and never asked again.
    await driver.atualizar()
    assert len([1 for _, caminho, _ in servidor.vistos if caminho.endswith("system")]) == 2
    await driver.parar()


async def test_a_versao_do_cadastro_evita_a_pergunta(tv):
    servidor, driver = await tv({philips.CAMPO_API: "5"}, versao="5")
    await driver.atualizar()
    assert [caminho for _, caminho, _ in servidor.vistos if caminho.endswith("system")] == []
    assert driver.estado().online is True
    await driver.parar()


async def test_um_poll_le_o_volume_na_escala_da_tv_o_mudo_e_a_entrada(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.ligado is True
    # The television counts to sixty and the contract counts to a hundred.
    assert estado.volume == 40
    assert estado.mudo is False
    assert estado.fonte == "Blu-ray"
    assert set(estado.fontes) == {"Blu-ray", "HDMI 2", "Televisão"}
    await driver.parar()


async def test_o_volume_e_o_mudo_viajam_na_mesma_mensagem(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("volume", 50) is None
    assert servidor.audio["current"] == 30
    assert driver.estado().volume == 50
    # Muting writes the same message, so the level it just set has to survive it.
    assert await driver.executar("mudo", True) is None
    assert servidor.audio == {"muted": True, "current": 30, "min": 0, "max": 60}
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_uma_tecla_vai_pela_palavra_do_protocolo(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    assert servidor.teclas[-1] == "Home"
    assert await driver.executar("pausar") is None
    assert servidor.teclas[-1] == "Pause"
    assert await driver.executar("tecla", "guia") == "invalid_value"
    await driver.parar()


async def test_a_entrada_vai_pelo_id_ou_pelo_nome_da_tela(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("fonte", "HDMI 2") is None
    assert servidor.fonte == "hdmi2"
    assert driver.estado().fonte == "HDMI 2"
    assert await driver.executar("fonte", "tv") is None
    assert servidor.fonte == "tv"
    assert await driver.executar("fonte", "hdmi9") == "invalid_value"
    assert await driver.executar("fonte", 1) == "invalid_value"
    await driver.parar()


async def test_uma_tv_sem_a_lista_de_entradas_recusa_a_troca(tv):
    """A set that dropped the endpoint gets a code, never a guess at which input to command."""
    servidor, driver = await tv(entradas={})
    await driver.atualizar()
    assert driver.estado().fontes == ()
    assert await driver.executar("fonte", "hdmi1") == "nao_suportado"
    await driver.parar()


async def test_desligar_e_a_espera_e_a_tv_na_rede_volta_pela_mesma_tecla(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    assert await driver.executar("desligar") is None
    assert servidor.teclas[-1] == "Standby"
    assert driver.estado().ligado is False
    assert await driver.executar("ligar") is None
    assert servidor.teclas[-1] == "Standby"
    assert driver.estado().ligado is True
    await driver.parar()


async def test_uma_tv_em_espera_so_liga_pelo_pacote_magico(tv, monkeypatch):
    enviados: list[bytes] = []
    monkeypatch.setattr(philips, "_soprar", enviados.append)
    servidor, driver = await tv()
    await servidor.parar()
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert await driver.executar("ligar") == "invalid_value"
    driver.cadastro.campos[philips.CAMPO_MAC] = "AA:BB:CC:DD:EE:FF"
    assert await driver.executar("ligar") is None
    assert enviados == [bytes.fromhex("aabbccddeeff")]
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


async def test_o_identificar_da_o_serial_da_televisao(tv):
    servidor, driver = await tv()
    assert await Philips.identificar(IP) == "PH-9"
    assert await Philips.identificar("nao-e-um-ip") is None
    await driver.parar()

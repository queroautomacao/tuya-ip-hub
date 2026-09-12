# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Sony receiver driver against a simulated device speaking the audio control API."""

import json
from dataclasses import dataclass, field

import pytest
from aiohttp import web

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import sony_avr
from iphub.drivers.nativos.sony_avr import SonyAvr
from iphub.drivers.simulado import ServidorHttp

IP = "127.0.0.1"

TERMINAIS = [
    {"uri": "extInput:hdmi?port=1", "title": "Blu-ray", "active": "active"},
    {"uri": "extInput:hdmi?port=2", "title": "TV", "active": ""},
    {"uri": "extInput:tv", "title": "Antena", "active": ""},
]
VOLUME = [{"target": "speaker", "volume": 25, "minVolume": 0, "maxVolume": 50, "mute": "off"}]


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "receiver-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Aparelho(ServidorHttp):
    """A device that answers by METHOD, which is what this API is: one path per service."""

    def __init__(self, resultados=None, *, versao_boa: str = "1.1", erros=None) -> None:
        super().__init__({})
        self.resultados = {
            "getPowerStatus": [{"status": "active"}],
            "getVolumeInformation": [VOLUME],
            "getCurrentExternalTerminalsStatus": [TERMINAIS],
            "getSystemInformation": [{"macAddr": "AA:BB:CC:DD:EE:FF", "model": "STR-DN1080"}],
            "setPowerStatus": [],
            "setAudioVolume": [],
            "setAudioMute": [],
            "setPlayContent": [],
            **(resultados or {}),
        }
        self.versao_boa = versao_boa
        self.erros = dict(erros or {})
        self.chamadas: list[dict] = []

    async def _atender(self, request: web.Request) -> web.Response:
        pedido = json.loads(await request.text())
        self.chamadas.append(pedido)
        metodo = pedido.get("method", "")
        if metodo in self.erros:
            return web.json_response({"error": self.erros[metodo], "id": 1})
        if pedido.get("version") != self.versao_boa:
            return web.json_response({"error": [14, "Unsupported Version"], "id": 1})
        if metodo not in self.resultados:
            return web.json_response({"error": [12, "No Such Method"], "id": 1})
        return web.json_response({"result": self.resultados[metodo], "id": 1})


@pytest.fixture
async def aparelho(monkeypatch):
    """A device already listening, with the driver aimed at the port it took."""
    ligados: list[_Aparelho] = []

    async def abrir(**extra):
        campos = extra.pop("campos", {})
        servidor = _Aparelho(**extra)
        await servidor.iniciar()
        ligados.append(servidor)
        monkeypatch.setattr(sony_avr, "PORTA_PADRAO", servidor.endereco[1])
        return servidor, SonyAvr(_Cadastro(campos=campos))

    yield abrir
    for servidor in ligados:
        await servidor.parar()


def test_o_manifesto_e_valido_e_nao_e_o_driver_de_uma_tv():
    validar(SonyAvr.MANIFESTO)
    assert SonyAvr.MANIFESTO.categoria == "receiver"
    assert SonyAvr.MANIFESTO.descoberta.ssdp_fabricantes == ()


async def test_um_poll_le_energia_volume_e_a_entrada_em_frente(aparelho):
    servidor, driver = await aparelho()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # The device counts to fifty and the contract counts to a hundred.
    assert estado.volume == 50
    assert estado.mudo is False
    assert estado.fonte == "Blu-ray"
    assert set(estado.fontes) == {"Blu-ray", "TV", "Antena"}
    await driver.parar()


async def test_a_faixa_de_volume_vem_do_proprio_aparelho(aparelho):
    servidor, driver = await aparelho(
        resultados={
            "getVolumeInformation": [
                [
                    {
                        "target": "speaker",
                        "volume": 30,
                        "minVolume": 0,
                        "maxVolume": 100,
                        "mute": "on",
                    }
                ]
            ]
        }
    )
    await driver.atualizar()
    assert driver.estado().volume == 30
    assert driver.estado().mudo is True
    assert await driver.executar("volume", 50) is None
    enviado = [c for c in servidor.chamadas if c["method"] == "setAudioVolume"][-1]
    assert enviado["params"] == [{"target": "speaker", "volume": "50"}]
    await driver.parar()


async def test_a_versao_do_metodo_e_descoberta_e_lembrada(aparelho):
    """A model answers one version of a method and refuses the other, and which one it is
    changes between models, so both are tried and the one that answered is kept."""
    servidor, driver = await aparelho(versao_boa="1.0")
    await driver.atualizar()
    assert driver.estado().online is True
    versoes = [c["version"] for c in servidor.chamadas if c["method"] == "getPowerStatus"]
    assert versoes[:2] == ["1.1", "1.0"]
    await driver.atualizar()
    versoes = [c["version"] for c in servidor.chamadas if c["method"] == "getPowerStatus"]
    # The version that worked is not looked for again.
    assert versoes[2:] == ["1.0"]
    await driver.parar()


async def test_ligar_desligar_e_mudo_mandam_a_palavra_da_api(aparelho):
    servidor, driver = await aparelho()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert await driver.executar("desligar") is None
    assert await driver.executar("mudo", True) is None
    energia = [c["params"] for c in servidor.chamadas if c["method"] == "setPowerStatus"]
    assert energia == [[{"status": "active"}], [{"status": "off"}]]
    mudo = [c["params"] for c in servidor.chamadas if c["method"] == "setAudioMute"]
    assert mudo == [[{"mute": "on"}]]
    assert await driver.executar("mudo", "sim") == "invalid_value"
    assert await driver.executar("volume", 101) == "invalid_value"
    await driver.parar()


async def test_a_entrada_viaja_pelo_nome_do_dono_ou_pela_uri(aparelho):
    servidor, driver = await aparelho()
    await driver.atualizar()
    assert await driver.executar("fonte", "TV") is None
    enviado = [c["params"] for c in servidor.chamadas if c["method"] == "setPlayContent"][-1]
    assert enviado == [{"uri": "extInput:hdmi?port=2"}]
    assert driver.estado().fonte == "TV"
    assert await driver.executar("fonte", "extInput:line?port=1") is None
    assert await driver.executar("fonte", "nao e uma uri") == "invalid_value"
    assert await driver.executar("fonte", 2) == "invalid_value"
    await driver.parar()


async def test_uma_recusa_que_nao_e_de_versao_e_valor_invalido(aparelho):
    servidor, driver = await aparelho(erros={"setPlayContent": [7, "Illegal Argument"]})
    await driver.atualizar()
    assert await driver.executar("fonte", "TV") == "invalid_value"
    assert driver.estado().online is True
    await driver.parar()


async def test_um_metodo_que_o_aparelho_nao_tem_e_erro_do_aparelho(aparelho):
    servidor, driver = await aparelho(erros={"getPowerStatus": [12, "No Such Method"]})
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"
    await driver.parar()


async def test_um_aparelho_que_nao_responde_e_offline_depois_de_dois_polls(aparelho):
    servidor, driver = await aparelho()
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_o_identificar_da_o_mac_do_aparelho(aparelho):
    servidor, driver = await aparelho()
    assert await SonyAvr.identificar(IP) == "aa:bb:cc:dd:ee:ff"
    assert await SonyAvr.identificar("nao-e-um-ip") is None
    await driver.parar()

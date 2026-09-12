# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Vizio driver against a simulated SmartCast, over a certificate it signed for itself."""

import json
from dataclasses import dataclass, field

import pytest
from aiohttp import web

from iphub.drivers.manifesto import Auth, validar
from iphub.drivers.nativos import vizio
from iphub.drivers.nativos.vizio import Vizio
from iphub.drivers.simulado import ServidorHttp

IP = "127.0.0.1"
TOKEN = "Zt-1234"
CODIGO = "4321"


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Tv(ServidorHttp):
    """A SmartCast that pairs by a code, keeps a hash on every value and refuses a stale one."""

    def __init__(self, *, ligada: int = 1, codigo: str = CODIGO) -> None:
        super().__init__({}, tls=True)
        self.ligada = ligada
        self.codigo = codigo
        self.volume = 22
        self.mudo = "Off"
        self.entrada = "HDMI-1"
        self.hash = 100
        self.teclas: list[dict] = []
        self.autorizacoes: list[str] = []
        self.pedido_de_pareamento = 0

    async def _atender(self, request: web.Request) -> web.Response:
        texto = await request.text()
        corpo = json.loads(texto) if texto else {}
        caminho = request.path
        if caminho.startswith("/pairing"):
            return _json(self._parear(caminho, corpo))
        self.autorizacoes.append(request.headers.get("AUTH", ""))
        if caminho == vizio.CAMINHO_INFO:
            return _json(_um({"CNAME": "info", "VALUE": {"SERIAL_NUMBER": "VZ-77"}}))
        if caminho == vizio.CAMINHO_ENERGIA:
            return _json(_um({"CNAME": "power_mode", "VALUE": self.ligada}))
        if caminho == vizio.CAMINHO_TECLA:
            self.teclas.append(corpo["KEYLIST"][0])
            return _json({"STATUS": {"RESULT": "SUCCESS"}})
        if caminho == vizio.CAMINHO_ENTRADAS:
            return _json(
                _muitos(
                    [
                        {"CNAME": "hdmi1", "NAME": "HDMI-1", "VALUE": "Blu-ray"},
                        {"CNAME": "hdmi2", "NAME": "HDMI-2", "VALUE": "HDMI-2"},
                    ]
                )
            )
        if caminho == vizio.CAMINHO_ENTRADA:
            if request.method == "GET":
                return _json(
                    _um(
                        {
                            "CNAME": "current_input",
                            "VALUE": self.entrada,
                            "HASHVAL": self.hash,
                        }
                    )
                )
            return _json(self._escrever("entrada", corpo))
        if caminho == vizio.CAMINHO_AUDIO:
            return _json(
                _muitos(
                    [
                        self._audio("volume", self.volume),
                        self._audio("mute", self.mudo),
                    ]
                )
            )
        if caminho.startswith(f"{vizio.CAMINHO_AUDIO}/"):
            nome = caminho.rpartition("/")[2]
            if request.method == "GET":
                return _json(_um(self._audio(nome, getattr(self, _CAMPOS[nome]))))
            return _json(self._escrever(_CAMPOS[nome], corpo))
        return web.Response(status=404, text="")

    def _audio(self, nome: str, valor: object) -> dict:
        return {"CNAME": nome, "NAME": nome, "VALUE": valor, "HASHVAL": self.hash}

    def _escrever(self, campo: str, corpo: dict) -> dict:
        # A hash that is not the current one is a write over a value somebody else changed.
        if corpo.get("HASHVAL") != self.hash:
            return {"STATUS": {"RESULT": "HASHVAL_MISMATCH"}}
        setattr(self, campo, corpo["VALUE"])
        self.hash += 1
        return {"STATUS": {"RESULT": "SUCCESS"}}

    def _parear(self, caminho: str, corpo: dict) -> dict:
        if caminho.endswith("start"):
            self.pedido_de_pareamento = 77
            return _um({"CHALLENGE_TYPE": 1, "PAIRING_REQ_TOKEN": 77})
        if corpo.get("RESPONSE_VALUE") != self.codigo:
            return {"STATUS": {"RESULT": "PAIRING_DENIED"}}
        return _um({"AUTH_TOKEN": TOKEN})


_CAMPOS = {"volume": "volume", "mute": "mudo", "entrada": "entrada"}


def _um(item: dict) -> dict:
    return {"ITEMS": [item], "STATUS": {"RESULT": "SUCCESS"}}


def _muitos(itens: list[dict]) -> dict:
    return {"ITEMS": itens, "STATUS": {"RESULT": "SUCCESS"}}


def _json(dado: dict) -> web.Response:
    return web.Response(status=200, text=json.dumps(dado), content_type="application/json")


@pytest.fixture
async def tv(monkeypatch):
    """A device already listening on its own certificate, with the driver aimed at its port."""
    ligadas: list[_Tv] = []

    async def abrir(segredos=None, campos=None, **extra):
        servidor = _Tv(**extra)
        await servidor.iniciar()
        ligadas.append(servidor)
        campos = dict(campos or {})
        campos[vizio.CAMPO_PORTA] = str(servidor.endereco[1])
        return servidor, Vizio(_Cadastro(campos=campos, segredos=segredos or {}))

    yield abrir
    for servidor in ligadas:
        await servidor.parar()


def test_o_manifesto_e_valido():
    validar(Vizio.MANIFESTO)
    assert Vizio.MANIFESTO.auth == Auth.CODIGO
    assert {"volume", "fonte", "tecla"} <= set(Vizio.MANIFESTO.capacidades)


async def test_o_pareamento_e_o_codigo_da_tela_e_depois_o_token(tv):
    servidor, driver = await tv()
    assert await driver.autenticar() == "aguardando"
    assert servidor.pedido_de_pareamento == 77
    assert driver.credenciais_do_aparelho() == {}
    driver.cadastro.campos[vizio.CAMPO_CODIGO] = CODIGO
    assert await driver.autenticar() == "pareado"
    # The token is handed over for the caller to keep; nobody walks to the television twice.
    assert driver.credenciais_do_aparelho() == {vizio.CAMPO_TOKEN: TOKEN}
    await driver.parar()


async def test_um_codigo_errado_falha_o_pareamento(tv):
    servidor, driver = await tv(campos={vizio.CAMPO_CODIGO: "0000"})
    assert await driver.autenticar() == "aguardando"
    assert await driver.autenticar() == "falhou"
    await driver.parar()


async def test_um_poll_le_energia_volume_mudo_e_a_entrada(tv):
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN})
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.volume == 22
    assert estado.mudo is False
    assert estado.fonte == "Blu-ray"
    assert set(estado.fontes) == {"Blu-ray", "HDMI-2"}
    # The token rides a header on every message that is not the power question.
    assert TOKEN in servidor.autorizacoes
    await driver.parar()


async def test_uma_tv_desligada_nao_e_perguntada_o_resto(tv):
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN}, ligada=0)
    await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().ligado is False
    assert driver.estado().volume is None
    await driver.parar()


async def test_o_volume_e_o_mudo_citam_o_hash_lido_na_hora(tv):
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN})
    await driver.atualizar()
    assert await driver.executar("volume", 40) is None
    assert servidor.volume == 40
    # The hash moved with the write, and the next command reads the new one before writing.
    assert await driver.executar("mudo", True) is None
    assert servidor.mudo == "On"
    assert driver.estado().mudo is True
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_a_energia_e_a_tecla_do_conjunto_de_energia(tv):
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN})
    await driver.atualizar()
    assert await driver.executar("desligar") is None
    assert servidor.teclas[-1] == {"CODESET": 11, "CODE": 0, "ACTION": "KEYPRESS"}
    assert driver.estado().ligado is False
    assert await driver.executar("ligar") is None
    assert servidor.teclas[-1] == {"CODESET": 11, "CODE": 1, "ACTION": "KEYPRESS"}
    assert driver.estado().ligado is True
    await driver.parar()


async def test_uma_tecla_vai_pelo_par_de_numeros(tv):
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN})
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    assert servidor.teclas[-1]["CODESET"] == 4
    assert servidor.teclas[-1]["CODE"] == 15
    assert await driver.executar("pausar") is None
    assert servidor.teclas[-1] == {"CODESET": 2, "CODE": 2, "ACTION": "KEYPRESS"}
    assert await driver.executar("tecla", "digito_1") == "invalid_value"
    await driver.parar()


async def test_a_entrada_vai_pelo_nome_do_dono_ou_pelo_da_tv(tv):
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN})
    await driver.atualizar()
    assert await driver.executar("fonte", "Blu-ray") is None
    assert servidor.entrada == "HDMI-1"
    assert driver.estado().fonte == "Blu-ray"
    assert await driver.executar("fonte", "HDMI-2") is None
    assert servidor.entrada == "HDMI-2"
    assert await driver.executar("fonte", "HDMI-9") == "invalid_value"
    assert await driver.executar("fonte", 2) == "invalid_value"
    await driver.parar()


async def test_um_cadastro_sem_token_pede_pareamento(tv):
    servidor, driver = await tv()
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"
    await driver.parar()


async def test_uma_tv_que_nao_responde_e_offline_depois_de_dois_polls(tv):
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN})
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_o_token_nunca_aparece_na_transcricao(tv, caplog):
    """The log of this hub carries what was asked, never what proves who is asking."""
    caplog.set_level("DEBUG", logger="iphub.drivers.nativos.vizio")
    servidor, driver = await tv(segredos={vizio.CAMPO_TOKEN: TOKEN})
    await driver.atualizar()
    assert await driver.executar("tecla", "inicio") is None
    assert TOKEN not in caplog.text
    assert vizio.CAMINHO_TECLA in caplog.text
    await driver.parar()


async def test_o_identificar_da_o_serial_do_aparelho(tv, monkeypatch):
    servidor, driver = await tv()
    monkeypatch.setattr(vizio, "PORTA_PADRAO", servidor.endereco[1])
    assert await Vizio.identificar(IP) == "VZ-77"
    assert await Vizio.identificar("nao-e-um-ip") is None
    await driver.parar()

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Samsung air conditioner over the SmartThings cloud, against a simulated cloud.

A driver is tested against a fake server and never against hardware, and this one never
against the real cloud of Samsung, which would spend the account of a customer to run a
suite. The base URL is the one knob a test turns.
"""

import json
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import Auth, validar
from iphub.drivers.nativos import samsung_smartthings as st
from iphub.drivers.nativos.samsung_smartthings import SamsungSmartthings
from iphub.drivers.simulado import ServidorHttp

DISPOSITIVO = "6f0a1b2c-3d4e-5f60-7182-93a4b5c6d7e8"
OUTRO = "aaaa1111-bbbb-2222-cccc-3333dddd4444"
TOKEN = "pat-de-teste-1234567890abcdef"

DEVICES = "/devices"
DEVICES_FILTRO = f"/devices?capability={st.CAP_MODO}"
ESTADO = f"/devices/{DISPOSITIVO}/status"
COMANDOS = f"/devices/{DISPOSITIVO}/commands"

MODOS = ["cool", "dry", "wind", "auto", "heat", "coolClean"]
VENTOS = ["auto", "low", "medium", "high", "turbo"]


@dataclass(frozen=True)
class _Cadastro:
    """A registration of a cloud driver: no address, a token and a device id."""

    identidade: str = "ar-da-sala"
    ip: str = ""
    campos: dict[str, str] = field(default_factory=lambda: {"dispositivo": DISPOSITIVO})
    segredos: dict[str, str] = field(default_factory=lambda: {"token": TOKEN})
    listas: dict[str, tuple] = field(default_factory=dict)


def _estado(
    *, ligado: str = "on", setpoint: float = 22, unidade: str = "C", **extra: object
) -> str:
    principal = {
        st.CAP_ENERGIA: {st.ATR_ENERGIA: {"value": ligado}},
        st.CAP_MODO: {
            st.ATR_MODO: {"value": "cool"},
            st.ATR_MODOS: {"value": MODOS},
        },
        st.CAP_VENTO: {
            st.ATR_VENTO: {"value": "high"},
            st.ATR_VENTOS: {"value": VENTOS},
        },
        st.CAP_SETPOINT: {st.ATR_SETPOINT: {"value": setpoint, "unit": unidade}},
        "temperatureMeasurement": {"temperature": {"value": 26, "unit": unidade}},
    }
    principal.update(extra)
    return json.dumps({"components": {"main": principal, "1": {}}})


def _conta(*ids: str, capacidade: str = st.CAP_MODO) -> str:
    return json.dumps(
        {
            "items": [
                {
                    "deviceId": identificador,
                    "label": f"Ar {numero}",
                    "name": "Samsung OCF Air Conditioner",
                    "components": [
                        {"id": "main", "capabilities": [{"id": "switch"}, {"id": capacidade}]}
                    ],
                }
                for numero, identificador in enumerate(ids, start=1)
            ]
        }
    )


def _aceito() -> str:
    return json.dumps({"results": [{"id": "1", "status": "ACCEPTED"}]})


def _rotas(**extras: tuple[int, str]) -> dict[str, tuple[int, str]]:
    rotas = {
        DEVICES: (200, _conta(DISPOSITIVO)),
        DEVICES_FILTRO: (200, _conta(DISPOSITIVO)),
        ESTADO: (200, _estado()),
        COMANDOS: (200, _aceito()),
    }
    rotas.update(extras)
    return rotas


@pytest.fixture
async def nuvem(monkeypatch):
    """A simulated SmartThings cloud plus a driver aimed at it, closed when the test ends."""
    criados: list[SamsungSmartthings] = []

    def montar(servidor: ServidorHttp, cadastro: _Cadastro | None = None) -> SamsungSmartthings:
        anfitriao, porta = servidor.endereco
        monkeypatch.setattr(st, "BASE", f"http://{anfitriao}:{porta}/{{caminho}}")
        driver = SamsungSmartthings(cadastro or _Cadastro())
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


def _ordens(servidor: ServidorHttp) -> list[dict]:
    return [
        json.loads(pedido.corpo)["commands"][0]
        for pedido in servidor.pedidos
        if pedido.caminho == COMANDOS and pedido.corpo
    ]


def test_o_manifesto_declara_a_nuvem_e_o_que_a_secao_6_pede():
    """A cloud driver asks for no address, declares no discovery signature and
    authenticates, because reaching a cloud is holding a credential of the customer.
    """
    manifesto = SamsungSmartthings.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.nuvem is True
    assert manifesto.auth is Auth.CHAVE
    assert manifesto.categoria == "ar_condicionado"
    assert manifesto.capacidades == ("ligar", "desligar", "temperatura", "modo", "vento")
    assert [campo.nome for campo in manifesto.config_campos] == ["token", "dispositivo"]
    segredos = [campo.nome for campo in manifesto.config_campos if campo.tipo == "segredo"]
    assert segredos == ["token"], "the token of the account is a secret and never a plain field"
    assert manifesto.lista_da_conta == "dispositivo"
    assert manifesto.descoberta.ssdp_st == ()
    assert manifesto.descoberta.mdns_servicos == ()


async def test_um_poll_le_o_estado_e_o_vocabulario_numa_requisicao_so(nuvem):
    """The status of a unit carries the lists of what it accepts, so there is no second
    document to read and the vocabulary is as fresh as the state.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.temperatura, estado.modo, estado.vento) == (
        True,
        22,
        "frio",
        "alto",
    )
    assert [pedido.caminho for pedido in servidor.pedidos] == [ESTADO]
    # coolClean is a mode of its own and is not the cool of the vocabulary.
    assert sorted(driver._modos) == ["auto", "frio", "quente", "seco", "vento"]
    assert driver._modos["vento"] == "wind"
    assert driver._ventos["alto"] == "high"


async def test_toda_requisicao_leva_o_token_e_ele_nunca_entra_no_log(nuvem, caplog):
    caplog.set_level("DEBUG", logger="iphub.drivers.nativos.samsung_smartthings")
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("ligar") is None
    assert servidor.pedidos[0].cabecalhos["Authorization"] == f"Bearer {TOKEN}"
    assert TOKEN not in caplog.text
    assert "commands" in caplog.text


async def test_ligar_e_desligar_mandam_o_comando_do_interruptor(nuvem):
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("ligar") is None
        assert await driver.executar("desligar") is None
    assert [ordem["command"] for ordem in _ordens(servidor)] == ["on", "off"]
    assert _ordens(servidor)[0]["capability"] == "switch"
    assert _ordens(servidor)[0]["component"] == "main"
    assert driver.estado().ligado is False


async def test_o_setpoint_vai_em_graus_e_fora_da_faixa_e_recusado(nuvem):
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("temperatura", 24) is None
        assert await driver.executar("temperatura", 40) == "invalid_value"
        assert await driver.executar("temperatura", "24") == "invalid_value"
        assert await driver.executar("temperatura", True) == "invalid_value"
    ordem = _ordens(servidor)[-1]
    assert (ordem["capability"], ordem["command"], ordem["arguments"]) == (
        "thermostatCoolingSetpoint",
        "setCoolingSetpoint",
        [24],
    )
    assert driver.estado().temperatura == 24


async def test_uma_conta_em_fahrenheit_e_convertida_nos_dois_sentidos(nuvem):
    """The customer asks for degrees Celsius and the unit reads its own scale, and the
    conversion lives here so the panel never learns there was another one.
    """
    async with ServidorHttp(_rotas(**{ESTADO: (200, _estado(setpoint=73, unidade="F"))})) as s:
        driver = nuvem(s)
        await driver.atualizar()
        assert driver.estado().temperatura == 23
        assert await driver.executar("temperatura", 23) is None
    assert _ordens(s)[-1]["arguments"] == [73]


async def test_o_modo_e_o_vento_vao_na_grafia_da_propria_unidade(nuvem):
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("modo", "vento") is None
        assert await driver.executar("vento", "medio") is None
        assert await driver.executar("modo", "nao_existe") == "invalid_value"
        assert await driver.executar("vento", 3) == "invalid_value"
    ordens = _ordens(servidor)
    assert (ordens[0]["command"], ordens[0]["arguments"]) == ("setAirConditionerMode", ["wind"])
    assert (ordens[1]["command"], ordens[1]["arguments"]) == ("setFanMode", ["medium"])
    assert driver.estado().modo == "vento"
    assert driver.estado().vento == "medio"


async def test_um_modo_que_a_unidade_nao_tem_e_recusado_antes_de_viajar(nuvem):
    """This model has no heat, and a command for it is refused here instead of in the cloud."""
    sem_quente = _estado(**{st.CAP_MODO: {st.ATR_MODOS: {"value": ["cool", "dry"]}}})
    async with ServidorHttp(_rotas(**{ESTADO: (200, sem_quente)})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("modo", "quente") == "invalid_value"
    assert _ordens(servidor) == []


async def test_um_comando_antes_do_primeiro_poll_le_o_vocabulario_e_vai(nuvem):
    """Refusing a word before anything was read would refuse a word the unit does take."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        assert await driver.executar("modo", "frio") is None
    assert [pedido.caminho for pedido in servidor.pedidos] == [ESTADO, COMANDOS]
    assert _ordens(servidor)[-1]["arguments"] == ["cool"]


async def test_um_comando_recusado_dentro_do_corpo_e_erro_do_aparelho(nuvem):
    """The cloud answers 200 to a refusal too, and the verdict is one result per command."""
    recusa = json.dumps({"results": [{"id": "1", "status": "FAILED"}]})
    async with ServidorHttp(_rotas(**{COMANDOS: (200, recusa)})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("ligar") == "erro_aparelho"


async def test_um_token_morto_pede_pareamento_e_nao_diz_que_o_ar_sumiu(nuvem):
    """Every token created after 30 December 2024 lands here 24 hours after it was made."""
    async with ServidorHttp(_rotas(**{ESTADO: (401, "")})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"


async def test_um_id_que_a_conta_nao_conhece_mais_pede_pareamento(nuvem):
    async with ServidorHttp(_rotas(**{ESTADO: (404, "")})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().detalhe == "auth_pendente"


async def test_a_nuvem_estrangulando_o_token_nao_e_o_ar_falhando(nuvem):
    async with ServidorHttp(_rotas(**{ESTADO: (429, "")})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        # One lost poll keeps the last state, two in a row is offline.
        assert driver.estado().online is False
        await driver.atualizar()
    assert driver.estado().detalhe == "eq_offline"


async def test_o_parear_adota_o_unico_ar_da_conta(nuvem):
    sem_id = _Cadastro(campos={})
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor, sem_id)
        assert await driver.autenticar() == "pareado"
        assert driver.credenciais_do_aparelho() == {"dispositivo": DISPOSITIVO}
        # The registration only learns the id after the caller writes it, and the poll that
        # follows the pairing must not wait for that trip.
        await driver.atualizar()
    assert driver.estado().online is True


async def test_com_dois_ares_a_escolha_e_do_integrador(nuvem, caplog):
    caplog.set_level("WARNING", logger="iphub.drivers.nativos.samsung_smartthings")
    dois = (200, _conta(DISPOSITIVO, OUTRO))
    async with ServidorHttp(_rotas(**{DEVICES: dois, DEVICES_FILTRO: dois})) as servidor:
        driver = nuvem(servidor, _Cadastro(campos={}))
        assert await driver.autenticar() == "falhou"
        assert driver.credenciais_do_aparelho() == {}
        achados = await driver.aparelhos_da_conta()
    assert [(ar.id, ar.nome) for ar in achados] == [(DISPOSITIVO, "Ar 1"), (OUTRO, "Ar 2")]
    assert OUTRO in caplog.text


async def test_a_conta_e_filtrada_pela_capacidade_que_este_driver_comanda(nuvem):
    """A cloud that ignored the filter does not get to put a doorbell in the list."""
    campainha = (200, _conta(OUTRO, capacidade="button"))
    async with ServidorHttp(_rotas(**{DEVICES: campainha, DEVICES_FILTRO: campainha})) as s:
        driver = nuvem(s, _Cadastro(campos={}))
        assert await driver.aparelhos_da_conta() == ()
    assert s.pedidos[0].caminho == DEVICES_FILTRO


async def test_um_cadastro_sem_token_nao_gasta_requisicao_da_conta(nuvem):
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor, _Cadastro(segredos={"token": ""}))
        await driver.atualizar()
        await driver.atualizar()
    assert servidor.pedidos == []
    assert driver.estado().detalhe == "auth_pendente"

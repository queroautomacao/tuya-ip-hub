# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The LG air conditioner over the ThinQ cloud, against a simulated cloud.

Section 12: a driver is tested against a fake server and never against hardware, and this one
never against the real cloud of LG, which would spend the account of a customer to run a
suite. The base URL is the one knob a test turns, exactly as the port is for the speaker.

O ar condicionado LG pela nuvem ThinQ, contra uma nuvem simulada.

Seção 12: um driver é testado contra servidor falso e nunca contra hardware, e este nunca
contra a nuvem de verdade da LG, que gastaria a conta de um cliente para rodar uma suíte. A
URL base é o único botão que um teste gira, exatamente como a porta é para a caixa.
"""

import json
import logging
from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import Auth, validar
from iphub.drivers.nativos import lg_thinq
from iphub.drivers.nativos.lg_thinq import LgThinq
from iphub.drivers.simulado import ServidorHttp

DISPOSITIVO = "0f7a1b2c3d4e5f60718293a4b5c6d7e8"
OUTRO = "aaaa1111bbbb2222cccc3333dddd4444"
TOKEN = "pat-de-teste-1234567890abcdef"
PAIS = "BR"

DEVICES = "/devices"
PERFIL = f"/devices/{DISPOSITIVO}/profile"
ESTADO = f"/devices/{DISPOSITIVO}/state"
CONTROLE = f"/devices/{DISPOSITIVO}/control"


@dataclass(frozen=True)
class _Cadastro:
    """A registration of a cloud driver: no address, a country, a token and a device id.

    Um cadastro de driver de nuvem: sem endereço, um país, um token e um id de aparelho.
    """

    identidade: str = "ar-da-sala"
    ip: str = ""
    campos: dict[str, str] = field(
        default_factory=lambda: {"pais": PAIS, "dispositivo": DISPOSITIVO}
    )
    segredos: dict[str, str] = field(default_factory=lambda: {"token": TOKEN})
    listas: dict[str, tuple] = field(default_factory=dict)


def _resposta(corpo: object) -> str:
    return json.dumps({"response": corpo})


def _perfil(modos: list[str] | None = None, ventos: list[str] | None = None) -> str:
    return _resposta(
        {
            "airConJobMode": {
                "currentJobMode": {"type": "enum", "value": {"w": modos or ["COOL", "AIR_DRY"]}}
            },
            "airFlow": {
                "windStrength": {"type": "enum", "value": {"w": ventos or ["LOW", "HIGH"]}}
            },
        }
    )


def _estado(**extra: object) -> str:
    lido = {
        "operation": {"airConOperationMode": "POWER_ON"},
        "temperature": {"targetTemperature": 22, "currentTemperature": 25},
        "airConJobMode": {"currentJobMode": "COOL"},
        "airFlow": {"windStrength": "HIGH"},
    }
    lido.update(extra)
    return _resposta(lido)


def _conta(*ids: str) -> str:
    return _resposta(
        [
            {
                "deviceId": identificador,
                "deviceInfo": {"deviceType": "DEVICE_AIR_CONDITIONER", "alias": f"Ar {numero}"},
            }
            for numero, identificador in enumerate(ids, start=1)
        ]
    )


def _rotas(**extras: str) -> dict[str, tuple[int, str]]:
    rotas = {
        DEVICES: (200, _conta(DISPOSITIVO)),
        PERFIL: (200, _perfil()),
        ESTADO: (200, _estado()),
        CONTROLE: (200, _resposta({})),
    }
    rotas.update({caminho: (200, corpo) for caminho, corpo in extras.items()})
    return rotas


@pytest.fixture
async def nuvem(monkeypatch):
    """A simulated ThinQ cloud plus a driver aimed at it, closed when the test ends.

    Uma nuvem ThinQ simulada mais um driver apontado para ela, fechado quando o teste acaba.
    """
    criados: list[LgThinq] = []

    def montar(servidor: ServidorHttp, cadastro: _Cadastro | None = None) -> LgThinq:
        anfitriao, porta = servidor.endereco
        monkeypatch.setattr(lg_thinq, "BASE", f"http://{anfitriao}:{porta}/{{caminho}}")
        driver = LgThinq(cadastro or _Cadastro())
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


def _corpos(servidor: ServidorHttp, caminho: str) -> list[dict]:
    return [json.loads(p.corpo) for p in servidor.pedidos if p.caminho == caminho and p.corpo]


def test_o_manifesto_declara_a_nuvem_e_o_que_a_secao_6_pede():
    """Section 1: a cloud driver asks for no address, declares no discovery signature and
    authenticates, because reaching a cloud is holding a credential of the customer.

    Seção 1: um driver de nuvem não pede endereço, não declara assinatura de descoberta e
    autentica, porque alcançar uma nuvem é guardar credencial do cliente.
    """
    manifesto = LgThinq.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.nuvem is True
    assert manifesto.auth is Auth.CHAVE
    assert manifesto.categoria == "ar_condicionado"
    assert manifesto.capacidades == ("ligar", "desligar", "temperatura", "modo", "vento")
    assert [campo.nome for campo in manifesto.config_campos] == ["pais", "token", "dispositivo"]
    segredos = [campo.nome for campo in manifesto.config_campos if campo.tipo == "segredo"]
    assert segredos == ["token"], "the token of the account is a secret and never a plain field"
    assert manifesto.descoberta.ssdp_st == ()
    assert manifesto.descoberta.mdns_servicos == ()


async def test_um_poll_le_o_estado_e_o_perfil_uma_vez_so(nuvem):
    """The profile says which words THIS unit takes and does not change, so it is read once
    and kept; a hub that asked for it every poll would spend the budget of the account.

    O profile diz quais palavras ESTA unidade aceita e não muda, então é lido uma vez e
    guardado; um hub que o pedisse a cada poll gastaria o orçamento da conta.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.temperatura, estado.modo, estado.vento) == (
        True,
        22,
        "frio",
        "alto",
    )
    caminhos = [pedido.caminho for pedido in servidor.pedidos]
    assert caminhos.count(PERFIL) == 1
    assert caminhos.count(ESTADO) == 2


async def test_toda_requisicao_leva_o_token_a_chave_de_api_e_o_pais(nuvem):
    """The headers of the cloud, section 14: without any one of them the account answers 401.

    Os cabeçalhos da nuvem, seção 14: sem qualquer um deles a conta responde 401.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
    cabecalhos = servidor.pedidos[0].cabecalhos
    assert cabecalhos["Authorization"] == f"Bearer {TOKEN}"
    assert cabecalhos["x-api-key"] == lg_thinq.CHAVE_DE_API
    assert cabecalhos["x-country"] == PAIS
    assert cabecalhos["x-service-phase"] == "OP"
    assert cabecalhos["x-client-id"]
    # Why: the message id is per request, so two of them are never the same.
    # Por que: o id de mensagem é por requisição, então dois nunca são iguais.
    ids = {pedido.cabecalhos["x-message-id"] for pedido in servidor.pedidos}
    assert len(ids) == len(servidor.pedidos)


async def test_ligar_e_desligar_falam_o_par_de_recurso_e_propriedade(nuvem):
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("ligar") is None
        assert await driver.executar("desligar") is None
    assert _corpos(servidor, CONTROLE) == [
        {"operation": {"airConOperationMode": "POWER_ON"}},
        {"operation": {"airConOperationMode": "POWER_OFF"}},
    ]
    assert driver.estado().ligado is False


async def test_um_aparelho_desligado_recusa_todo_comando_menos_ligar(nuvem):
    """Section 14: with airConOperationMode in POWER_OFF the unit takes nothing, so a scene
    reads the code and turns it on first instead of writing into the void.

    Seção 14: com o airConOperationMode em POWER_OFF a unidade não aceita nada, então uma cena
    lê o código e a liga antes em vez de escrever no vazio.
    """
    desligado = _estado(operation={"airConOperationMode": "POWER_OFF"})
    async with ServidorHttp(_rotas(**{ESTADO: desligado})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert driver.estado().ligado is False
        assert await driver.executar("temperatura", 23) == "eq_offline"
        assert await driver.executar("modo", "frio") == "eq_offline"
        assert await driver.executar("vento", "alto") == "eq_offline"
        assert _corpos(servidor, CONTROLE) == []
        assert await driver.executar("ligar") is None
    assert _corpos(servidor, CONTROLE) == [{"operation": {"airConOperationMode": "POWER_ON"}}]


async def test_o_modo_e_o_vento_usam_as_palavras_que_a_unidade_declara(nuvem):
    """The spelling of a model comes from its profile, and a word of section 6 the unit does
    not have is refused before anything is sent.

    A grafia de um modelo vem do profile dele, e uma palavra da seção 6 que a unidade não tem é
    recusada antes de qualquer coisa ser enviada.
    """
    perfil = _perfil(modos=["COOL", "AIR_DRY", "auto"], ventos=["low", "MID"])
    async with ServidorHttp(_rotas(**{PERFIL: perfil})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("modo", "seco") is None
        assert await driver.executar("modo", "auto") is None
        assert await driver.executar("vento", "medio") is None
        # Heat and the highest fan speed are not in this profile.
        # Quente e o vento mais alto não estão neste profile.
        assert await driver.executar("modo", "quente") == "invalid_value"
        assert await driver.executar("vento", "alto") == "invalid_value"
    assert _corpos(servidor, CONTROLE) == [
        {"airConJobMode": {"currentJobMode": "AIR_DRY"}},
        {"airConJobMode": {"currentJobMode": "auto"}},
        {"airFlow": {"windStrength": "MID"}},
    ]


async def test_refrigerar_e_aquecer_vao_sem_a_conferencia_condicional(nuvem):
    """Section 14: the cloud refuses cool and heat on a unit that is already on while
    x-conditional-control is true, and the documentation says to send those two with it false.

    Seção 14: a nuvem recusa refrigerar e aquecer num aparelho já ligado com o
    x-conditional-control em true, e a documentação manda mandar esses dois com ele false.
    """
    perfil = _perfil(modos=["COOL", "HEAT", "AIR_DRY"])
    async with ServidorHttp(_rotas(**{PERFIL: perfil})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("modo", "frio") is None
        assert await driver.executar("modo", "quente") is None
        assert await driver.executar("modo", "seco") is None
    condicoes = [
        pedido.cabecalhos["x-conditional-control"]
        for pedido in servidor.pedidos
        if pedido.caminho == CONTROLE
    ]
    assert condicoes == ["false", "false", "true"]


@pytest.mark.parametrize("valor", [15, 31, "22", 22.0, True, None])
async def test_uma_temperatura_fora_do_contrato_nunca_chega_a_nuvem(nuvem, valor):
    """Section 6 fixes whole degrees from 16 to 30, and True is an int in Python.

    A seção 6 fixa graus inteiros de 16 a 30, e True é int em Python.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("temperatura", valor) == "invalid_value"
    assert _corpos(servidor, CONTROLE) == []


async def test_a_temperatura_vai_como_o_alvo_em_graus(nuvem):
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("temperatura", 24) is None
    assert _corpos(servidor, CONTROLE) == [{"temperature": {"targetTemperature": 24}}]
    assert driver.estado().temperatura == 24


async def test_o_setpoint_tambem_e_lido_do_campo_em_celsius(nuvem):
    """A model that answers targetTemperatureC instead of targetTemperature is the same fact.

    Um modelo que responde targetTemperatureC em vez de targetTemperature é o mesmo fato.
    """
    celsius = _estado(temperature={"targetTemperatureC": 19, "currentTemperature": 25})
    async with ServidorHttp(_rotas(**{ESTADO: celsius})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
    assert driver.estado().temperatura == 19


async def test_parear_confere_o_id_e_lista_a_conta_no_log_quando_ele_nao_casa(nuvem, caplog):
    """The id of a device is a string nobody memorises, and the listing is the only place it
    exists; pairing writes it to the log so the integrator copies it from the screen.

    O id de um aparelho é uma string que ninguém decora, e a listagem é o único lugar onde ela
    existe; o pareamento a escreve no log para o integrador copiá-la da tela.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        assert await driver.autenticar() == "pareado"
    async with ServidorHttp(_rotas(**{DEVICES: _conta(OUTRO)})) as servidor:
        driver = nuvem(servidor)
        with caplog.at_level(logging.WARNING):
            assert await driver.autenticar() == "falhou"
    assert any(OUTRO in registro.getMessage() for registro in caplog.records)
    assert not any(TOKEN in registro.getMessage() for registro in caplog.records)


async def test_uma_conta_que_recusa_o_token_e_auth_pendente(nuvem):
    """A token that expired is not a device that failed, and the panel says pair it again.

    Um token vencido não é aparelho que falhou, e o painel diz pareie de novo.
    """
    recusa = {caminho: (401, "") for caminho in (DEVICES, PERFIL, ESTADO, CONTROLE)}
    async with ServidorHttp(recusa) as servidor:
        driver = nuvem(servidor)
        assert await driver.autenticar() == "falhou"
        assert await driver.executar("ligar") == "auth_pendente"
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"


@pytest.mark.parametrize(
    ("status", "esperado"),
    [(400, "invalid_value"), (429, "erro_aparelho"), (500, "erro_aparelho")],
)
async def test_o_status_da_nuvem_vira_o_codigo_estavel_da_secao_6(nuvem, status, esperado):
    async with ServidorHttp(_rotas(**{})) as servidor:
        servidor.rotas[CONTROLE] = (status, "")
        driver = nuvem(servidor)
        await driver.atualizar()
        assert await driver.executar("ligar") == esperado


async def test_uma_nuvem_que_nao_responde_e_offline_depois_de_dois_polls(nuvem):
    """One lost poll keeps the last state, two in a row is offline, the same as on the LAN.

    Um poll perdido guarda o último estado, dois seguidos é offline, o mesmo que na LAN.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.rotas.clear()
        servidor.rotas[ESTADO] = (503, "")
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_um_cadastro_sem_token_ou_com_pais_ruim_nunca_disca(nuvem):
    """A registration saved with a bad field spends no request of the account to be told what
    the driver already knows.

    Um cadastro salvo com campo ruim não gasta requisição da conta para ouvir o que o driver já
    sabe.
    """
    async with ServidorHttp(_rotas()) as servidor:
        sem_token = _Cadastro(segredos={})
        driver = nuvem(servidor, sem_token)
        await driver.atualizar()
        assert driver.estado().detalhe == "" or driver.estado().online is False
        pais_ruim = _Cadastro(campos={"pais": "brasil", "dispositivo": DISPOSITIVO})
        outro = nuvem(servidor, pais_ruim)
        assert await outro.executar("ligar") == "auth_pendente"
        vazio = _Cadastro(campos={"pais": PAIS, "dispositivo": ""})
        terceiro = nuvem(servidor, vazio)
        assert await terceiro.executar("ligar") == "auth_pendente"
    assert servidor.pedidos == []


async def test_uma_resposta_que_nao_e_json_ou_e_gigante_nao_derruba_o_poll(nuvem):
    async with ServidorHttp(_rotas(**{ESTADO: "isto nao e json"})) as servidor:
        driver = nuvem(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_a_regiao_vem_do_pais_da_conta():
    """The table of LG: the Americas on aic, Asia on kic, everything else on eic.

    A tabela da LG: as Américas em aic, a Ásia em kic, todo o resto em eic.
    """
    assert lg_thinq._regiao("BR") == "aic"
    assert lg_thinq._regiao("US") == "aic"
    assert lg_thinq._regiao("KR") == "kic"
    assert lg_thinq._regiao("PT") == "eic"
    assert lg_thinq._regiao("ZZ") == "eic"


def test_a_base_e_https_e_nunca_vem_do_cadastro():
    """Section 9: the host is a constant of the driver, so no registration can send the token
    of a customer to a host somebody typed.

    Seção 9: o host é constante do driver, então cadastro nenhum manda o token de um cliente
    para um host que alguém digitou.
    """
    assert lg_thinq.BASE.startswith("https://")
    assert "lgthinq.com" in lg_thinq.BASE
    campos = {campo.nome for campo in LgThinq.MANIFESTO.config_campos}
    assert "url" not in campos and "base" not in campos and "host" not in campos

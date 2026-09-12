# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Sony Bravia TV over the Scalar Web API and IRCC, against a simulated TV.

a driver is tested against a fake device and never against hardware. The whole
REST API of a Bravia lives under four paths, so the table of answers below picks one by the
method inside the body the simulated server has just recorded, which is what lets one test
prove the exact bytes of getPowerStatus and of setPowerStatus on the same path.
"""

import json
import logging
from base64 import b64encode
from dataclasses import dataclass, field

import pytest

from iphub.drivers.base import CODIGOS
from iphub.drivers.gestor import valor_no_vocabulario
from iphub.drivers.manifesto import TECLAS, Auth, item_valido, por_lista, validar
from iphub.drivers.nativos import sony
from iphub.drivers.nativos.sony import SonyBravia
from iphub.drivers.simulado import ServidorDatagrama, ServidorHttp

CAMINHO_SISTEMA = "/sony/system"
CAMINHO_AUDIO = "/sony/audio"
CAMINHO_CONTEUDO = "/sony/avContent"
CAMINHO_APLICATIVOS = "/sony/appControl"
CAMINHOS_REST = (CAMINHO_SISTEMA, CAMINHO_AUDIO, CAMINHO_CONTEUDO, CAMINHO_APLICATIVOS)

IRCC = "/sony/IRCC"
IRCC_MINUSCULO = "/sony/ircc"

# The names of the wire, written here and not imported, so a rename of a constant of the
# driver does not quietly rename what this test says the TV was asked.
ENERGIA = "getPowerStatus"
SISTEMA = "getSystemInformation"
CONTROLE = "getRemoteControllerInfo"
VOLUME = "getVolumeInformation"
CONTEUDO = "getPlayingContentInfo"
MANDA_ENERGIA = "setPowerStatus"
MANDA_WOL = "setWolMode"
MANDA_VOLUME = "setAudioVolume"
MANDA_MUDO = "setAudioMute"
MANDA_CONTEUDO = "setPlayContent"
MANDA_APLICATIVO = "setActiveApp"
MANDA_REINICIO = "requestReboot"
MANDA_FIM_DOS_APPS = "terminateApps"

PSK = "chave-de-teste"
CID = "xyzabc123"
MAC = "AA:BB:CC:DD:EE:FF"
# The same address as somebody copies it off the box, which is what a registration carries.
MAC_ESCRITO = "aa-bb-cc-dd-ee-ff"
PACOTE = bytes.fromhex("FF" * 6 + "AABBCCDDEEFF" * 16)
HOST_LOCAL = "127.0.0.1"
# A port nobody listens on, which is where the magic packet goes in every test but the one
# that proves it, and where the TV is when a test needs it to have gone away.
PORTA_MUDA = 9

HDMI1 = "extInput:hdmi?port=1"
HDMI2 = "extInput:hdmi?port=2"
APP = "com.sony.dtv.com.netflix.ninja.MainActivity"
# The uri of the application every Bravia has, measured: 100 characters, which is past the 64
# of a value of a list, so the shortcut of this one cannot be registered.
APP_LONGO = (
    "com.sony.dtv.com.google.android.youtube.tv."
    "com.google.android.apps.youtube.tv.activity.ShellActivity"
)
CANAL = "tv:isdbt?trip=1.2.3"

# The keys of a remote, named by the TV itself; the code of each is base64 and nothing else.
NOMES = (
    "Play",
    "Pause",
    "Stop",
    "Next",
    "Prev",
    "ChannelUp",
    "ChannelDown",
    "Up",
    "Down",
    "Left",
    "Right",
    "Confirm",
    "Return",
    "Home",
    "ActionMenu",
    "Exit",
    *(f"Num{numero}" for numero in range(10)),
)
CODIGO = {nome: b64encode(nome.encode()).decode() for nome in NOMES}

# The envelope of a key press, written whole here: this is the wire, and a test that built it
# from the driver would prove only that the driver agrees with itself.
ENVELOPE = (
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
    '<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">'
    "<IRCCCode>{codigo}</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>"
)


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "tv-da-sala"
    ip: str = HOST_LOCAL
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=lambda: {"psk": PSK})
    listas: dict[str, tuple] = field(default_factory=dict)


def _resposta(resultado: object) -> str:
    return json.dumps({"result": resultado, "id": 1})


def _erro(codigo: int, mensagem: str) -> str:
    """The failure of this API, which travels inside an HTTP 200."""
    return json.dumps({"error": [codigo, mensagem], "id": 1})


def _energia(estado: str = "active") -> str:
    return _resposta([{"status": estado}])


def _sistema() -> str:
    return _resposta(
        [
            {
                "product": "TV",
                "model": "KD-55XF8596",
                "serial": "1112233",
                "macAddr": MAC,
                "name": "BRAVIA",
                "cid": CID,
            }
        ]
    )


def _controle(*nomes: str) -> str:
    """The keys of THIS model, which is the second entry of the result and not the first."""
    escolhidos = nomes or NOMES
    return _resposta(
        [
            {"bundled": True, "type": "IR_REMOTE_BUNDLE_TYPE_RESERVED01"},
            [{"name": nome, "value": CODIGO[nome]} for nome in escolhidos],
        ]
    )


def _alto_falante(volume: int = 25, mudo: bool = False, **faixa: object) -> dict:
    entrada = {"target": "speaker", "volume": volume, "mute": mudo}
    entrada.update({"minVolume": 0, "maxVolume": 100} if not faixa else faixa)
    return entrada


def _volume(*entradas: dict) -> str:
    """The result of the volume is a list OF LISTS, with one entry per audio output."""
    return _resposta([list(entradas or (_alto_falante(),))])


def _conteudo(uri: str = HDMI1, **extra: object) -> str:
    lido = {"uri": uri, "title": "HDMI 1", "source": "extInput:hdmi"}
    lido.update(extra)
    return _resposta([lido])


VAZIO = _resposta([])

RESPOSTAS = {
    ENERGIA: (200, _energia()),
    SISTEMA: (200, _sistema()),
    CONTROLE: (200, _controle()),
    VOLUME: (200, _volume()),
    CONTEUDO: (200, _conteudo()),
    MANDA_ENERGIA: (200, VAZIO),
    MANDA_WOL: (200, VAZIO),
    MANDA_VOLUME: (200, VAZIO),
    MANDA_MUDO: (200, VAZIO),
    MANDA_CONTEUDO: (200, VAZIO),
    MANDA_APLICATIVO: (200, VAZIO),
    MANDA_REINICIO: (200, VAZIO),
    MANDA_FIM_DOS_APPS: (200, VAZIO),
}


class _Tabela:
    """The answers of the TV chosen by the method of the body, not only by the path.

    The simulated server picks an answer by path and asks its route table for it with get,
    after recording the request; the four paths of this API each serve many methods, so the
    table reads the method of the request that was just recorded and answers that one.
    """

    def __init__(
        self,
        servidor: ServidorHttp,
        metodos: dict[str, tuple[int, str]],
        caminhos: dict[str, tuple[int, str]],
    ) -> None:
        self.servidor = servidor
        self.metodos = dict(metodos)
        self.caminhos = dict(caminhos)

    def get(self, caminho: str, padrao: object = None) -> object:
        if caminho in self.caminhos:
            return self.caminhos[caminho]
        if caminho not in CAMINHOS_REST or not self.servidor.pedidos:
            return padrao
        return self.metodos.get(_metodo(self.servidor.pedidos[-1].corpo), padrao)


def _metodo(corpo: str) -> str:
    try:
        return json.loads(corpo).get("method", "")
    except ValueError:
        return ""


def _tv(caminhos: dict[str, tuple[int, str]] | None = None, **trocas: tuple[int, str]):
    """A simulated Bravia: the answers of the REST by method, the IRCC by path."""
    servidor = ServidorHttp({})
    metodos = dict(RESPOSTAS)
    metodos.update(trocas)
    servidor.rotas = _Tabela(
        servidor, metodos, {IRCC: (200, "OK")} if caminhos is None else caminhos
    )
    return servidor


@pytest.fixture
async def tv(monkeypatch):
    """A driver aimed at the port of the simulated TV, closed when the test ends."""
    criados: list[SonyBravia] = []

    def montar(servidor: ServidorHttp, cadastro: _Cadastro | None = None) -> SonyBravia:
        monkeypatch.setattr(sony, "PORTA", servidor.endereco[1])
        # The magic packet goes to the registered address, which here is the loopback of
        # whoever runs the suite, so the port is moved to one nobody listens on; the test that
        # proves the packet points it at a simulated server of its own.
        monkeypatch.setattr(sony, "WOL_PORTA", PORTA_MUDA)
        driver = SonyBravia(cadastro or _Cadastro())
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


def _chamadas(servidor: ServidorHttp, caminho: str = "") -> list[dict]:
    return [
        json.loads(pedido.corpo)
        for pedido in servidor.pedidos
        if pedido.caminho in CAMINHOS_REST and (not caminho or pedido.caminho == caminho)
    ]


def _metodos(servidor: ServidorHttp) -> list[str]:
    return [chamada.get("method") for chamada in _chamadas(servidor)]


def _params(servidor: ServidorHttp, metodo: str) -> list[object]:
    return [c.get("params") for c in _chamadas(servidor) if c.get("method") == metodo]


def _teclas(servidor: ServidorHttp) -> list[tuple[str, str]]:
    return [
        (pedido.caminho, pedido.corpo)
        for pedido in servidor.pedidos
        if pedido.caminho in (IRCC, IRCC_MINUSCULO)
    ]


def test_o_manifesto_e_o_de_uma_tv_com_chave_e_com_as_teclas_do_controle():
    """A TV switches, sets a level, picks an input, presses keys and takes a
    shortcut; it has no group, no mode of its own, no fan speed and no setpoint.
    """
    manifesto = SonyBravia.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "tv_sony_bravia"
    assert manifesto.categoria == "tv"
    assert manifesto.nuvem is False
    assert manifesto.capacidades == (
        "ligar",
        "desligar",
        "volume",
        "mudo",
        "fonte",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
        "tecla",
        "atalho",
        "comando_extra",
    )
    assert set(manifesto.teclas) <= set(TECLAS)
    assert {"mais", "menos", "ok", "digito_9"} <= set(manifesto.teclas)
    assert manifesto.descoberta.ssdp_fabricantes == ("sony",)
    # The inputs of a Bravia are the same uris on every model and nobody types one from
    # memory; a shortcut is not suggested, because an application uri changes with the model.
    assert set(por_lista(manifesto)) == {"entradas"}
    assert [item.valor for item in por_lista(manifesto)["entradas"]][:2] == [HDMI1, HDMI2]


def test_a_chave_e_um_segredo_do_cadastro_e_nao_ha_campo_de_porta():
    """The key is a device credential, so it lives in the config.json and never
    goes back to the panel; the port is fixed by this protocol and is nobody's field.
    """
    campos = SonyBravia.MANIFESTO.config_campos
    assert [campo.nome for campo in campos] == ["psk", "mac"]
    assert campos[0].tipo == "segredo"
    assert campos[0].obrigatorio is True
    # The address is optional: a TV the hub has already seen awake said it by itself, and the
    # field is what switches on the one it never saw.
    assert campos[1].tipo == "texto"
    assert campos[1].obrigatorio is False
    assert SonyBravia.MANIFESTO.auth is Auth.CHAVE


async def test_o_iniciar_le_o_mapa_de_teclas_e_a_primeira_tecla_do_dia_nao_o_paga(tv):
    """The names of the keys change with the model, so the map comes from the TV; reading it
    when the driver opens is what keeps the first key press from paying for it.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.iniciar()
        assert _metodos(servidor) == [CONTROLE]
        assert await driver.executar("tecla", "ok") is None
    assert _metodos(servidor) == [CONTROLE], "the map was already there"
    assert len(_teclas(servidor)) == 2, "the wake up and the key"


async def test_o_iniciar_de_uma_tv_que_nao_responde_nao_estoura(tv, monkeypatch):
    """A TV that did not answer now is asked again by the first key that needs the map, and
    the lifecycle never breaks because a device was not there.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        monkeypatch.setattr(sony, "PORTA", PORTA_MUDA)
        await driver.iniciar()
        monkeypatch.setattr(sony, "PORTA", servidor.endereco[1])
        assert await driver.executar("tecla", "ok") is None
    assert _metodos(servidor) == [CONTROLE]


async def test_toda_requisicao_leva_a_chave_e_o_caminho_sem_barra_no_fim(tv):
    """The documentation of Sony says a URL with a slash after the service name is invalid,
    and params is always a list, with the empty one for a method that takes no argument.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
    pedido = servidor.pedidos[0]
    assert pedido.metodo == "POST"
    assert pedido.caminho == CAMINHO_SISTEMA
    assert pedido.cabecalhos[sony.CABECALHO_CHAVE] == PSK
    assert json.loads(pedido.corpo) == {
        "method": ENERGIA,
        "id": 1,
        "params": [],
        "version": "1.0",
    }


async def test_um_poll_le_energia_volume_mudo_e_o_que_toca(tv):
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.volume, estado.mudo) == (True, 25, False)
    # The uri and not the title: maps the value of the list of the registration.
    assert estado.fonte == HDMI1
    assert estado.detalhe == ""


async def test_as_entradas_ficam_com_as_sugestoes_e_nunca_no_estado(tv):
    """The panel builds an example of the list out of a value it also shows as the label, and
    every input uri of a Bravia is longer than the 16 characters of a label, so
    publishing them would offer the integrator items the registration then refuses.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().fontes == ()
    assert "getCurrentExternalInputsStatus" not in _metodos(servidor)
    sugeridas = por_lista(SonyBravia.MANIFESTO)["entradas"]
    assert [s.valor for s in sugeridas][:2] == [HDMI1, HDMI2]
    # What the driver leaves for the panel is a label the registration takes, which the uri
    # itself is not: the shortest of them is 20 characters against a ceiling of 16.
    assert all(item_valido(s.rotulo, s.valor) for s in sugeridas)
    assert not any(item_valido(s.valor, s.valor) for s in sugeridas)


@pytest.mark.parametrize(
    ("lido", "esperado"),
    [
        ({"uri": HDMI1, "title": "HDMI 1"}, None),
        ({"uri": CANAL, "programTitle": "Jornal", "dispNum": "12.1"}, "Jornal"),
        ({"uri": CANAL, "dispNum": "12.1"}, "12.1"),
        ({"uri": "com.sony.dtv.x", "title": "Netflix"}, "Netflix"),
    ],
)
async def test_o_titulo_do_que_toca_nao_e_o_nome_de_uma_entrada(tv, lido, esperado):
    """The transport and the title are different facts, and the title of an input
    is the name of the input, which is not something playing.
    """
    async with _tv(**{CONTEUDO: (200, _resposta([lido]))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
    assert driver.estado().tocando == esperado


async def test_reproduzindo_e_sempre_none(tv):
    """This API carries no transport state at all, and reading it from an empty answer would
    report a TV playing inside an application as paused, which forbids.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        assert driver.estado().reproduzindo is None
        servidor.rotas.metodos[CONTEUDO] = (200, VAZIO)
        await driver.atualizar()
    assert driver.estado().reproduzindo is None
    assert driver.estado().tocando is None


@pytest.mark.parametrize(
    ("faixa", "esperado"),
    [
        ({"minVolume": 0, "maxVolume": 100}, 25),
        ({"minVolume": 0, "maxVolume": 50}, 50),
        ({"minVolume": 20, "maxVolume": 120}, 5),
        ({}, None),
        ({"minVolume": 0, "maxVolume": 0}, None),
        ({"minVolume": "0", "maxVolume": "100"}, None),
    ],
)
async def test_o_volume_da_tv_vira_a_escala_da_secao_6(tv, faixa, esperado):
    """The TV speaks minVolume to maxVolume and is 0 to 100; a TV that did not say
    which scale it speaks has no readable volume, which is not volume zero.
    """
    entrada = {"target": "speaker", "volume": 25, "mute": False, **faixa}
    async with _tv(**{VOLUME: (200, _volume(entrada))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
    assert driver.estado().volume == esperado


async def test_o_volume_publicado_e_o_do_alto_falante_e_nunca_o_do_fone(tv):
    """A TV with a headphone connected answers that entry too, and publishing whatever came
    last would put the level of the headphone on the panel.
    """
    fone = {"target": "headphone", "volume": 90, "mute": False, "minVolume": 0, "maxVolume": 100}
    async with _tv(**{VOLUME: (200, _volume(_alto_falante(volume=30), fone))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        assert driver.estado().volume == 30
        servidor.rotas.metodos[VOLUME] = (200, _volume(fone))
        await driver.atualizar()
    assert driver.estado().volume is None, "no speaker in the answer is no volume of ours"


async def test_a_faixa_vai_embora_com_o_alto_falante_e_o_volume_para_de_ser_aceito(tv):
    """The range converts the level, so it goes with the level. Keeping the one of the last
    answer that had a speaker would let a command write the old scale into a TV whose state
    already says the level here is unknown.
    """
    fone = {"target": "headphone", "volume": 90, "mute": False, "minVolume": 0, "maxVolume": 100}
    entrada = _alto_falante(volume=10, minVolume=0, maxVolume=50)
    async with _tv(**{VOLUME: (200, _volume(entrada))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        assert driver.estado().volume == 20
        servidor.rotas.metodos[VOLUME] = (200, _volume(fone))
        await driver.atualizar()
        assert driver.estado().volume is None
        # The value is inside the contract, so what is refused is the TV and not the value.
        assert await driver.executar("volume", 40) == "erro_aparelho"
    assert _params(servidor, MANDA_VOLUME) == []


async def test_o_volume_vai_como_string_na_escala_do_aparelho(tv):
    """The field is a string, always, and the 0 to 100 is converted before it."""
    entrada = _alto_falante(volume=10, minVolume=0, maxVolume=50)
    async with _tv(**{VOLUME: (200, _volume(entrada))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        assert await driver.executar("volume", 50) is None
    assert _params(servidor, MANDA_VOLUME) == [[{"target": "speaker", "volume": "25"}]]
    assert driver.estado().volume == 50


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_ao_fio(tv, valor):
    """True is an int in Python: a mute arriving where a volume fits would silence a room."""
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        assert await driver.executar("volume", valor) == "invalid_value"
    assert MANDA_VOLUME not in _metodos(servidor)


async def test_um_volume_numa_tv_que_esconde_a_faixa_e_recusado(tv):
    """Writing 0 to 100 into a TV that answers another range sets a level nobody asked for,
    so the level is refused and the volume keys, which need no scale, are the way in.
    """
    entrada = {"target": "speaker", "volume": 25, "mute": False}
    async with _tv(**{VOLUME: (200, _volume(entrada))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        # The value is inside the contract, so what is refused is the TV and not the value.
        assert await driver.executar("volume", 40) == "erro_aparelho"
        assert await driver.executar("tecla", "mais") is None
    assert _params(servidor, MANDA_VOLUME) == [[{"target": "speaker", "volume": "+1"}]]


async def test_as_teclas_de_volume_vao_como_passo_relativo_e_nunca_por_ircc(tv):
    """A step up and a step down do not depend on knowing the scale of this model."""
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("tecla", "mais") is None
        assert await driver.executar("tecla", "menos") is None
    assert _params(servidor, MANDA_VOLUME) == [
        [{"target": "speaker", "volume": "+1"}],
        [{"target": "speaker", "volume": "-1"}],
    ]
    assert _teclas(servidor) == []


async def test_o_mudo_vai_como_o_status_do_servico_de_audio(tv):
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
        assert await driver.executar("mudo", "sim") == "invalid_value"
    assert _params(servidor, MANDA_MUDO) == [[{"status": True}], [{"status": False}]]
    assert driver.estado().mudo is False


async def test_uma_tv_em_standby_so_responde_o_getpowerstatus(tv):
    """Polling a TV in standby keeps its network awake and costs watts, and a TV that is off
    has no volume, no input and nothing playing to read.
    """
    async with _tv(**{ENERGIA: (200, _energia("standby"))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().ligado is False
    assert driver.estado().tocando is None
    # The system information is read once, for the address the magic packet needs.
    assert _metodos(servidor) == [ENERGIA, SISTEMA, ENERGIA]


async def test_um_poll_de_tv_acesa_pergunta_energia_volume_e_conteudo_e_mais_nada(tv):
    """The system information is read once, and the poll of a TV that is on is three
    questions: what is left to ask is the registration and not the TV.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert _metodos(servidor) == [
        ENERGIA,
        SISTEMA,
        VOLUME,
        CONTEUDO,
        ENERGIA,
        VOLUME,
        CONTEUDO,
    ]


async def test_ligar_da_os_tres_tiros_na_ordem_certa(tv, monkeypatch):
    """The magic packet, then the REST, then the one IRCC code: an old model obeys only the
    IRCC and a new one only the REST, and none of the three confirms anything.
    """
    async with (
        ServidorDatagrama({}) as rede,
        _tv(**{ENERGIA: (200, _energia("standby"))}) as servidor,
    ):
        driver = tv(servidor)
        # The address of the TV is read from the TV itself, and it is what the packet carries.
        await driver.atualizar()
        monkeypatch.setattr(sony, "WOL_PORTA", rede.endereco[1])
        assert await driver.executar("ligar") is None
    assert rede.recebidos == [PACOTE]
    assert _metodos(servidor)[-2:] == [ENERGIA, MANDA_ENERGIA]
    assert _params(servidor, MANDA_ENERGIA) == [[{"status": True}]]
    # The empty frame wakes the API up, and the code of the power on comes right behind it.
    assert [corpo for _caminho, corpo in _teclas(servidor)] == [
        ENVELOPE.format(codigo=""),
        ENVELOPE.format(codigo="AAAAAQAAAAEAAAAuAw=="),
    ]
    assert driver.estado().ligado is True


async def test_ligar_numa_tv_ja_acesa_para_no_primeiro_tiro(tv):
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("ligar") is None
    assert _metodos(servidor) == [ENERGIA]
    assert _teclas(servidor) == []


async def test_o_pacote_magico_sai_com_o_endereco_do_cadastro_numa_tv_nunca_vista(tv, monkeypatch):
    """The address of the TV is read from the TV, and a TV that has been off since the hub
    booted never answered anything: without the field of the registration the one shot that
    reaches a TV that is really off would never leave, which is the whole point of it.
    """
    async with ServidorDatagrama({}) as rede, _tv() as servidor:
        driver = tv(servidor, _Cadastro(campos={"mac": MAC_ESCRITO}))
        monkeypatch.setattr(sony, "WOL_PORTA", rede.endereco[1])
        # The TV is not there: nothing was ever read from it, and the two shots over IP have
        # nobody to reach.
        monkeypatch.setattr(sony, "PORTA", PORTA_MUDA)
        await driver.atualizar()
        await driver.atualizar()
        assert driver.estado().online is False
        assert await driver.executar("ligar") is None
    assert rede.recebidos == [PACOTE]


async def test_um_cadastro_sem_endereco_fisico_nao_manda_pacote_e_a_tv_muda_e_offline(
    tv, monkeypatch
):
    """Nothing else reaches a TV that is really off, so with no address to carry there is
    nothing to report but the silence of the two shots that travel over IP.
    """
    async with ServidorDatagrama({}) as rede, _tv() as servidor:
        driver = tv(servidor)
        monkeypatch.setattr(sony, "WOL_PORTA", rede.endereco[1])
        monkeypatch.setattr(sony, "PORTA", PORTA_MUDA)
        assert await driver.executar("ligar") == "eq_offline"
    assert rede.recebidos == []


async def test_um_ligar_que_so_teve_o_pacote_magico_nao_volta_como_falha(tv, monkeypatch):
    """A TV that is really off answers neither of the two shots that travel over IP, so their
    silence is what this command looks like when it works; reporting it would paint a failed
    step on every night scene, two seconds before the screen lights up.
    """
    async with ServidorDatagrama({}) as rede, _tv() as servidor:
        driver = tv(servidor)
        # The address is learned while the TV still answers, and then the TV goes away.
        await driver.atualizar()
        monkeypatch.setattr(sony, "WOL_PORTA", rede.endereco[1])
        monkeypatch.setattr(sony, "PORTA", PORTA_MUDA)
        assert await driver.executar("ligar") is None
    assert rede.recebidos == [PACOTE]


async def test_uma_tv_que_falou_e_recusou_o_ligar_volta_o_que_ela_disse(tv, monkeypatch):
    """A TV that answered did talk, and what it said is not the silence the magic packet
    covers: a fault of the device travels out even with the packet gone.
    """
    trocas = {ENERGIA: (200, _energia("standby")), MANDA_ENERGIA: (200, _erro(7, "Illegal State"))}
    async with ServidorDatagrama({}) as rede, _tv(caminhos={}, **trocas) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        monkeypatch.setattr(sony, "WOL_PORTA", rede.endereco[1])
        assert await driver.executar("ligar") == "erro_aparelho"
    assert rede.recebidos == [PACOTE]


async def test_desligar_numa_tv_ja_desligada_e_sucesso_e_nao_erro(tv):
    """A TV that is already off answers "not power-on" to the off command, which is the state
    that was asked for; the same message anywhere else is a TV that is not there.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("desligar") is None
        servidor.rotas.metodos[MANDA_ENERGIA] = (200, _erro(400, "not power-on"))
        assert await driver.executar("desligar") is None
        assert driver.estado().ligado is False
        servidor.rotas.metodos[MANDA_CONTEUDO] = (200, _erro(400, "not power-on"))
        assert await driver.executar("fonte", HDMI2) == "eq_offline"
        # Any other refusal of the off command is a fault of the TV and travels out as one.
        servidor.rotas.metodos[MANDA_ENERGIA] = (200, _erro(7, "Illegal State"))
        assert await driver.executar("desligar") == "erro_aparelho"
    assert _params(servidor, MANDA_ENERGIA) == [
        [{"status": False}],
        [{"status": False}],
        [{"status": False}],
    ]


async def test_a_entrada_vai_como_a_uri_da_lista_do_cadastro(tv):
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("fonte", HDMI2) is None
    assert _params(servidor, MANDA_CONTEUDO) == [[{"uri": HDMI2}]]
    assert driver.estado().fonte == HDMI2


async def test_um_atalho_de_aplicativo_vai_por_outro_servico_que_um_canal(tv):
    """The TV names its own applications with a prefix, and that prefix is the only thing
    that says which of the two methods plays the value of the list.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("atalho", APP) is None
        assert await driver.executar("atalho", CANAL) is None
    assert _params(servidor, MANDA_APLICATIVO) == [[{"uri": APP}]]
    assert _params(servidor, MANDA_CONTEUDO) == [[{"uri": CANAL}]]
    assert _chamadas(servidor, CAMINHO_APLICATIVOS)[0]["method"] == MANDA_APLICATIVO


@pytest.mark.parametrize(
    "valor",
    [
        "extInput:hdmi?port=1 e mais",
        'a"b',
        "linha\nquebrada",
        "<x>",
        "",
        # The uri of the application every Bravia has: a real value, 100 characters, past the
        # 64, so the shortcut of YouTube is not something this TV can be given.
        APP_LONGO,
        "x" * 65,
        7,
        None,
    ],
)
async def test_um_valor_que_nao_e_uma_uri_nunca_chega_ao_fio(tv, valor):
    """The value of a list reaches a device on the LAN, so what is not a uri this
    API answers is refused here and never handed to the TV.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("fonte", valor) == "invalid_value"
        assert await driver.executar("atalho", valor) == "invalid_value"
    assert servidor.pedidos == []


async def test_o_transporte_e_as_teclas_falam_ircc_com_o_codigo_que_a_tv_deu(tv):
    """The base64 of a key is never written in the driver: it comes from the TV, because the
    list changes with the model.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("tocar") is None
        assert await driver.executar("pausar") is None
        assert await driver.executar("parar") is None
        assert await driver.executar("proxima") is None
        assert await driver.executar("anterior") is None
        assert await driver.executar("tecla", "ok") is None
        assert await driver.executar("tecla", "digito_7") is None
    pedido = servidor.pedidos[1]
    assert pedido.caminho == IRCC
    assert pedido.cabecalhos["SOAPACTION"] == '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'
    assert pedido.cabecalhos["Content-Type"] == "text/xml; charset=UTF-8"
    assert pedido.cabecalhos[sony.CABECALHO_CHAVE] == PSK
    # The empty frame of the first key is the wake up, and the seven codes come after it.
    enviados = [corpo for _caminho, corpo in _teclas(servidor)]
    assert enviados[0] == ENVELOPE.format(codigo="")
    assert enviados[1:] == [
        ENVELOPE.format(codigo=CODIGO[nome])
        for nome in ("Play", "Pause", "Stop", "Next", "Prev", "Confirm", "Num7")
    ]


async def test_uma_tecla_que_esta_tv_nao_lista_volta_nao_suportado(tv):
    """The names change with the model, and a key the TV did not list is a key it does not
    have: refusing it is what keeps the panel from reporting success for nothing.
    """
    async with _tv(**{CONTROLE: (200, _controle("Play", "Confirm"))}) as servidor:
        driver = tv(servidor)
        assert await driver.executar("tecla", "menu") == "nao_suportado"
        assert await driver.executar("tecla", "ok") is None
        # A word of the vocabulary this manifest does not declare never reaches the driver:
        # the gate judges it against the manifest, and the guard inside the
        # driver is the second lock and not the one the panel meets.
        for palavra in ("guia", "info", "play_pause"):
            assert palavra in TECLAS
            assert valor_no_vocabulario(SonyBravia.MANIFESTO, "tecla", palavra) is False
            assert await driver.executar("tecla", palavra) == "invalid_value"
        assert valor_no_vocabulario(SonyBravia.MANIFESTO, "tecla", 7) is False
        assert await driver.executar("tecla", 7) == "invalid_value"
    assert len(_teclas(servidor)) == 2, "only the wake up and the key the TV listed"


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        ((500, ""), "erro_aparelho"),
        ((401, ""), "auth_pendente"),
        ((200, _resposta([{"bundled": True}])), "erro_aparelho"),
        # A list of keys where no entry is a name plus a base64 code is a list with no key in
        # it, and a driver that took one of these would press bytes the TV never named.
        (
            (
                200,
                _resposta(
                    [
                        {"bundled": True},
                        [
                            "isto nao e um dicionario",
                            {"name": "Play", "value": "isto nao e base64"},
                            {"value": CODIGO["Play"]},
                        ],
                    ]
                ),
            ),
            "erro_aparelho",
        ),
    ],
)
async def test_uma_tv_que_nao_diz_as_teclas_dela_nunca_finge_sucesso(tv, resposta, esperado):
    """An empty map is a TV that did not answer, not a TV without keys, so the reason travels
    out; saying nao_suportado here would blame the model for a lost exchange.
    """
    async with _tv(**{CONTROLE: resposta}) as servidor:
        driver = tv(servidor)
        assert await driver.executar("tocar") == esperado
    assert _teclas(servidor) == []


async def test_o_caminho_do_ircc_e_achado_e_guardado(tv):
    """Some firmware answers only /sony/ircc and some only /sony/IRCC, so the driver turns on
    the 404 and remembers the spelling that worked, instead of paying two requests per key.
    """
    async with _tv(caminhos={IRCC_MINUSCULO: (200, "OK")}) as servidor:
        driver = tv(servidor)
        assert await driver.executar("tocar") is None
        assert await driver.executar("pausar") is None
    caminhos = [caminho for caminho, _corpo in _teclas(servidor)]
    assert caminhos == [IRCC, IRCC_MINUSCULO, IRCC_MINUSCULO, IRCC_MINUSCULO]


async def test_uma_tecla_com_as_duas_grafias_em_404_e_a_api_da_tv_reiniciando(tv):
    """The WebApiCore service of the TV answers 404 to everything while it restarts, and the
    key that fell in that half minute is a TV nobody reached, not a key that does not exist.
    """
    async with _tv(caminhos={}) as servidor:
        driver = tv(servidor)
        assert await driver.executar("tocar") == "eq_offline"
    # Both spellings were tried before the driver gave up on this press.
    assert [caminho for caminho, _corpo in _teclas(servidor)] == [IRCC, IRCC_MINUSCULO]


@pytest.mark.parametrize(
    ("estado", "esperado"),
    [(401, "auth_pendente"), (403, "auth_pendente"), (500, "erro_aparelho")],
)
async def test_o_ircc_que_volta_com_erro_http_nunca_finge_que_a_tecla_aconteceu(
    tv, estado, esperado
):
    """The IRCC has a door of its own and answers with a status, so a key refused there says
    so: 401 and 403 are the key of the registration and anything else is the TV.
    """
    async with _tv(caminhos={IRCC: (estado, "")}) as servidor:
        driver = tv(servidor)
        assert await driver.executar("tocar") == esperado
        assert await driver.executar("tecla", "ok") == esperado
    # The wake up frame is the one that hits the refusal, so the code never leaves.
    assert [corpo for _caminho, corpo in _teclas(servidor)] == [ENVELOPE.format(codigo="")] * 2


async def test_o_primeiro_ircc_depois_de_dez_minutos_manda_o_quadro_vazio(tv, monkeypatch):
    """The first key after some ten minutes of silence is swallowed with no error at all, so
    an empty frame wakes the API up and the key that follows it lands.
    """
    agora = [1000.0]
    monkeypatch.setattr(sony, "monotonic", lambda: agora[0])
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("tocar") is None
        assert len(_teclas(servidor)) == 2, "the wake up and the key"
        assert await driver.executar("pausar") is None
        assert len(_teclas(servidor)) == 3, "no wake up while the API is awake"
        agora[0] += sony.DESPERTAR_S + 1
        assert await driver.executar("parar") is None
    assert len(_teclas(servidor)) == 5
    assert _teclas(servidor)[3][1] == ENVELOPE.format(codigo="")


async def test_o_comando_extra_reinicia_a_tv_ou_encerra_os_aplicativos(tv):
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar("comando_extra", "reboot") is None
        assert await driver.executar("comando_extra", "terminate_apps") is None
        assert await driver.executar("comando_extra", "qualquer") == "invalid_value"
        assert await driver.executar("comando_extra", None) == "invalid_value"
    assert _metodos(servidor) == [MANDA_REINICIO, MANDA_FIM_DOS_APPS]
    assert _params(servidor, MANDA_REINICIO) == [[]]


async def test_parear_confere_a_chave_e_liga_o_wake_on_lan(tv, caplog):
    """Wake on lan is off on a TV out of the factory, and without it the magic packet never
    works and nothing on the panel says why; a TV that refuses it is still paired.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        with caplog.at_level(logging.INFO):
            assert await driver.autenticar() == "pareado"
        assert _metodos(servidor) == [SISTEMA, MANDA_WOL]
        assert _params(servidor, MANDA_WOL) == [[{"enabled": True}]]
        servidor.rotas.metodos[MANDA_WOL] = (200, _erro(7, "Illegal State"))
        assert await driver.autenticar() == "pareado"
    # The identity is written to the log, and the key never is.
    assert any(CID in registro.getMessage() for registro in caplog.records)
    assert not any(PSK in registro.getMessage() for registro in caplog.records)


async def test_uma_chave_que_a_tv_recusa_e_auth_pendente_e_nunca_aguardando(tv):
    """There is no handshake in this API, so there is no pending state to report: the key
    either is the key or is not, and the panel is told to fix the key and not the network.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        # The TV was answering, so a poll that stops answering has a state to keep.
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.rotas.metodos.update({metodo: (403, "") for metodo in RESPOSTAS})
        assert await driver.autenticar() == "falhou"
        assert await driver.executar("ligar") == "auth_pendente"
        assert await driver.executar("mudo", True) == "auth_pendente"
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        assert driver.estado().detalhe == ""
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"


@pytest.mark.parametrize(
    "chave",
    ["minha chave", "chave-comum", "a" * 64],
)
async def test_uma_chave_do_alfabeto_do_cabecalho_vai_para_a_tv(tv, chave):
    """The key goes in a header of every request and the on screen keyboard of a TV has a
    space, so a key with one is a key, not a typo: refusing it here would send the integrator
    to read the same text on the TV and find it identical.
    """
    async with _tv() as servidor:
        driver = tv(servidor, _Cadastro(segredos={"psk": chave}))
        assert await driver.autenticar() == "pareado"
    assert servidor.pedidos[0].cabecalhos[sony.CABECALHO_CHAVE] == chave


@pytest.mark.parametrize("chave", ["chavão", "chave\tcom\ttabulação", "a" * 65])
async def test_uma_chave_fora_do_alfabeto_e_recusada_com_a_razao_no_log(tv, chave, caplog):
    """A key with an accent or longer than the header takes makes every command answer
    auth_pendente without a single request, and without this line the integrator reads the
    same key on the TV, finds it identical and goes hunting the network.
    """
    async with _tv() as servidor:
        driver = tv(servidor, _Cadastro(segredos={"psk": chave}))
        with caplog.at_level(logging.WARNING):
            assert await driver.autenticar() == "falhou"
            assert await driver.executar("mudo", True) == "auth_pendente"
    assert servidor.pedidos == []
    # The reason names the ceiling and the alphabet, and the key itself never goes to the log.
    ditas = [registro.getMessage() for registro in caplog.records]
    assert any("printable ascii" in mensagem and str(len(chave)) in mensagem for mensagem in ditas)
    assert not any(chave in mensagem for mensagem in ditas)
    assert sum("printable ascii" in mensagem for mensagem in ditas) == 1, "said once per driver"


async def test_uma_tv_que_nao_diz_o_sistema_dela_e_perguntada_de_novo_no_poll_seguinte(tv):
    """The system information carries the identity and the address of the magic packet, and an
    answer that is not an object carries neither: a poll that lost it keeps the TV on the
    panel and asks again, because one silent service is not a TV that went away.
    """
    async with _tv(**{SISTEMA: (200, _resposta(["isto nao e um objeto"]))}) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is True
    assert _metodos(servidor).count(SISTEMA) == 2


async def test_um_cadastro_sem_a_chave_nunca_fala_com_a_tv(tv):
    """The key is what this API asks for on every request, and a registration
    saved without one spends no exchange to be told what the driver already knows.
    """
    async with _tv() as servidor:
        driver = tv(servidor, _Cadastro(segredos={}))
        assert await driver.executar("tocar") == "auth_pendente"
        assert await driver.executar("volume", 20) == "auth_pendente"
        await driver.atualizar()
        await driver.atualizar()
    assert servidor.pedidos == []
    assert driver.estado().online is False
    assert driver.estado().detalhe == "auth_pendente"


async def test_um_cadastro_sem_ip_nunca_disca_para_ninguem(tv):
    """The address is where the device answered today, and a registration without
    one is a registration nothing can be dialled from.
    """
    async with _tv() as servidor:
        driver = tv(servidor, _Cadastro(ip=""))
        assert await driver.executar("ligar") == "eq_offline"
        await driver.atualizar()
    assert servidor.pedidos == []
    assert driver.estado().online is False


async def test_uma_tv_que_nao_responde_e_offline_depois_de_dois_polls(tv, monkeypatch):
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        monkeypatch.setattr(sony, "PORTA", PORTA_MUDA)
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_o_servico_que_reinicia_responde_404_e_a_tv_nao_sai_do_painel(tv):
    """The WebApiCore service of the TV restarts by itself and answers 404 to everything for
    about half a minute; two polls of tolerance would take the TV off the panel every time.
    """
    async with _tv() as servidor:
        driver = tv(servidor)
        await driver.atualizar()
        servidor.rotas.metodos.clear()
        for _volta in range(sony.REINICIOS_ATE_OFFLINE - 1):
            await driver.atualizar()
            assert driver.estado().online is True
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"
        # The service that came back puts the TV back on the panel with the next poll.
        servidor.rotas.metodos.update(RESPOSTAS)
        await driver.atualizar()
    assert driver.estado().online is True


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        ((200, _erro(7, "Illegal State")), "erro_aparelho"),
        ((200, "isto nao e json"), "erro_aparelho"),
        ((200, json.dumps({"id": 1})), "erro_aparelho"),
        ((200, json.dumps(["isto nao e um objeto"])), "erro_aparelho"),
        ((500, ""), "erro_aparelho"),
        ((401, ""), "auth_pendente"),
        # The key is refused inside a 200 as often as it is refused by the status.
        ((200, _erro(403, "Forbidden")), "auth_pendente"),
        ((200, _erro(401, "Unauthorized")), "auth_pendente"),
        ((200, _erro(400, "not power-on")), "eq_offline"),
    ],
)
async def test_a_resposta_da_tv_vira_o_codigo_estavel_da_secao_6(tv, resposta, esperado):
    """The failure of this API travels inside a 200, so the status alone proves nothing."""
    async with _tv(**{MANDA_MUDO: resposta}) as servidor:
        driver = tv(servidor)
        assert await driver.executar("mudo", True) == esperado


async def test_uma_resposta_longa_ou_sem_os_campos_nao_derruba_o_poll(tv):
    """A body under the ceiling is read whole, however long, and a field that is not there is
    a fact the driver does not have and never one it invents.
    """
    longa = _resposta([{"status": "active", "lixo": "x" * 100_000}])
    assert len(longa) < sony.CORPO_MAXIMO
    trocas = {
        ENERGIA: (200, longa),
        VOLUME: (200, _resposta([{"nada": True}])),
        CONTEUDO: (200, _resposta(["isto nao e um objeto"])),
    }
    async with _tv(**trocas) as servidor:
        driver = tv(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert (estado.volume, estado.mudo, estado.fonte, estado.tocando) == (None, None, None, None)
    assert estado.fontes == ()


async def test_uma_resposta_acima_do_teto_e_um_aparelho_quebrado_e_nao_uma_tv_que_sumiu(tv):
    """The answer of a device on the LAN of the customer never sizes the memory of the hub, so
    it is cut at the ceiling; what is cut stops being json, and a TV answering nonsense is
    erro_aparelho and not eq_offline, which would send the integrator to look at the network.
    """
    acima = _resposta([{"status": "active", "lixo": "x" * sony.CORPO_MAXIMO}])
    assert len(acima) > sony.CORPO_MAXIMO
    async with _tv(**{ENERGIA: (200, acima), MANDA_MUDO: (200, acima)}) as servidor:
        driver = tv(servidor)
        assert await driver.executar("mudo", True) == "erro_aparelho"
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


@pytest.mark.parametrize("acao", ["agrupar", "modo", "vento", "temperatura"])
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(tv, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with _tv() as servidor:
        driver = tv(servidor)
        assert await driver.executar(acao, 22) == "nao_suportado"
    assert servidor.pedidos == []


async def test_a_identidade_nao_e_perguntada_sem_a_chave(tv):
    """Nothing answers this TV without the key, getSystemInformation included, so the sweep
    finds an address here and the identity is only read after the registration has the key.
    """
    async with _tv() as servidor:
        tv(servidor)
        assert await SonyBravia.identificar(HOST_LOCAL) is None
    assert servidor.pedidos == []


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """Five stable codes and nothing else ever leaves a driver."""
    assert {
        sony.EQ_OFFLINE,
        sony.INVALID_VALUE,
        sony.AUTH_PENDENTE,
        sony.ERRO_APARELHO,
        sony.NAO_SUPORTADO,
    } == set(CODIGOS)

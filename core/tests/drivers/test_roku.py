# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Roku TV and player over the ECP, against a simulated device,.

The protocol answers 200 with an empty body to a key, so the answer proves nothing and the
only thing worth testing is what the driver PUT on the wire: which path, which method, and,
above all, when it wrote nothing. Play is one key that toggles, mute is one key that toggles
and reports nothing, and there is no volume anywhere, so each of those is a test that attacks
the driver instead of agreeing with it.

The names of the wire are written by hand in this file. A test that imported the paths and the
keys from the driver would agree with any rename the driver made, which is exactly what a
protocol test exists to catch.
"""

import logging
import time
from dataclasses import dataclass, field

import pytest

from iphub.drivers.base import CODIGOS
from iphub.drivers.descoberta import montar
from iphub.drivers.manifesto import TECLAS, Auth, por_lista, validar
from iphub.drivers.nativos import roku
from iphub.drivers.nativos.roku import Roku
from iphub.drivers.simulado import ServidorHttp

APARELHO = "/query/device-info"
APPS = "/query/apps"
APP_ATIVO = "/query/active-app"
TOCADOR = "/query/media-player"
CANAL = "/query/tv-active-channel"

SERIAL = "1GU48T017973"
UDN = "015e5108-9000-1046-8035-b0a737964dfb"
# Of the protocol: on a newer Roku TV the device-id is NOT the serial, which is why
# the identity of this hub is the serial and nothing else.
DEVICE_ID = "S04KH07YG08J"

# The literals of the protocol this driver is allowed to write, listed here so a route exists
# for each one and an unlisted key answers 404, exactly as a device that refuses it does.
TECLAS_NO_FIO = (
    "PowerOn",
    "PowerOff",
    "VolumeUp",
    "VolumeDown",
    "VolumeMute",
    "Play",
    "Fwd",
    "Rev",
    "Select",
    "Home",
    "Back",
    "Up",
    "Down",
    "Left",
    "Right",
    "Info",
    "ChannelUp",
    "ChannelDown",
    "InstantReplay",
    "FindRemote",
    "Backspace",
    "Enter",
    "InputTuner",
    "InputHDMI1",
    "InputHDMI4",
    "InputAV1",
)
APPS_NO_FIO = ("12", "13", "74519", "tvinput.hdmi1", "tvinput.hdmi2", "tvinput.dtv")

# The keys the texts of the manifest promise as a shortcut, written by hand here, because a
# shortcut is exactly the key of the closed list that the vocabulary has no word
# for; every other key of the protocol has a capacity of its own and goes through it.
ATALHOS_DOS_TEXTOS = (
    "InstantReplay",
    "Enter",
    "Backspace",
    "FindRemote",
    "InputTuner",
    "InputHDMI1",
    "InputHDMI4",
    "InputAV1",
)

# The keys a capacity of this driver already covers: an atalho is not a second door to them.
TECLAS_COM_CAPACIDADE = (
    "Play",
    "PowerOn",
    "PowerOff",
    "VolumeUp",
    "VolumeDown",
    "VolumeMute",
    "Fwd",
    "Rev",
    "Home",
    "Select",
    "ChannelUp",
)

NETFLIX = '<active-app><app id="12" type="appl" version="5.2.0">Netflix</app></active-app>'
TELA_INICIAL = "<active-app><app>Roku</app></active-app>"
PROTETOR_DE_TELA = (
    "<active-app><app>Roku</app>"
    '<screensaver id="55545" type="ssvr" version="2.0.1">Default screensaver</screensaver>'
    "</active-app>"
)
ENTRADA_HDMI = (
    '<active-app><app id="tvinput.hdmi1" type="tvin" version="1.0.0">HDMI</app></active-app>'
)
ANTENA = '<active-app><app id="tvinput.dtv" type="tvin" version="1.0.0">Antena</app></active-app>'

LISTA_DE_APPS = (
    "<apps>"
    '<app id="12" type="appl" version="4.1.218">Netflix</app>'
    '<app id="13" type="appl" version="5.2.2">Amazon Video on Demand</app>'
    '<app id="tvinput.hdmi1" type="tvin" version="1.0.0">Satellite TV</app>'
    '<app id="tvinput.dtv" type="tvin" version="1.0.0">Antenna TV</app>'
    "</apps>"
)
FONTES = ("12", "13", "tvinput.hdmi1", "tvinput.dtv")

PROGRAMA = "Airwolf"
CANAL_DA_ANTENA = (
    "<tv-channel><channel><number>14.3</number><name>getTV</name>"
    f"<program-title>{PROGRAMA}</program-title></channel></tv-channel>"
)


# The body of an answer is read up to a ceiling of 128 KB, re.search does not release the GIL
# and a poll happens every 10 s, so a pattern that can walk the whole body again from each
# start position is a device on the LAN freezing the daemon, the DP bus, the panel and every
# other driver. Each body below is the worst case of one read of the poll, at the exact
# ceiling: an opening tag that never closes, repeated. Anything answering on 8060 writes them,
# and so does an address typed wrong that lands on another HTTP server.
ORCAMENTO_DE_CPU_S = 1.0


def _no_teto(peca: str) -> str:
    return (peca * (roku.CORPO_MAXIMO // len(peca) + 1))[: roku.CORPO_MAXIMO]


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "sala"
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


def _aparelho(energia: str = "PowerOn", serial: str = SERIAL) -> str:
    """The device-info of the protocol, with the power mode last, where a truncated read
    would lose it.
    """
    return (
        "<?xml version='1.0' encoding='UTF-8'?><device-info>"
        f"<udn>{UDN}</udn>"
        f"<serial-number>{serial}</serial-number>"
        f"<device-id>{DEVICE_ID}</device-id>"
        "<vendor-name>Roku</vendor-name>"
        "<model-name>Roku 3</model-name>"
        "<is-tv>true</is-tv>"
        "<user-device-name>Sala</user-device-name>"
        f"<power-mode>{energia}</power-mode>"
        "</device-info>"
    )


def _tocador(estado: str = "play", erro: str = "false") -> str:
    return (
        f'<player error="{erro}" state="{estado}">'
        '<plugin bandwidth="10000000 bps" id="13" name="Amazon Video on Demand" />'
        "<position>31820 ms</position><is_live>false</is_live></player>"
    )


def _rotas(**extras: str) -> dict[str, tuple[int, str]]:
    rotas: dict[str, tuple[int, str]] = {
        APARELHO: (200, _aparelho()),
        APPS: (200, LISTA_DE_APPS),
        APP_ATIVO: (200, NETFLIX),
        TOCADOR: (200, _tocador()),
        CANAL: (200, CANAL_DA_ANTENA),
    }
    rotas.update({f"/keypress/{tecla}": (200, "") for tecla in TECLAS_NO_FIO})
    rotas.update({f"/launch/{app}": (200, "") for app in APPS_NO_FIO})
    rotas.update({caminho: (200, texto) for caminho, texto in extras.items()})
    return rotas


@pytest.fixture
async def aparelho(monkeypatch):
    """A simulated device plus a driver aimed at its port, closed when the test ends."""
    criados: list[Roku] = []

    def montar_driver(servidor: ServidorHttp | None = None, *, ip: str = "127.0.0.1") -> Roku:
        if servidor is not None:
            monkeypatch.setattr(roku, "PORTA", servidor.endereco[1])
        driver = Roku(_Cadastro(ip=ip))
        criados.append(driver)
        return driver

    yield montar_driver
    for driver in criados:
        await driver.parar()


def _teclas(servidor: ServidorHttp) -> list[str]:
    return [
        pedido.caminho.removeprefix("/keypress/")
        for pedido in servidor.pedidos
        if pedido.caminho.startswith("/keypress/")
    ]


def _abertos(servidor: ServidorHttp) -> list[str]:
    return [
        pedido.caminho.removeprefix("/launch/")
        for pedido in servidor.pedidos
        if pedido.caminho.startswith("/launch/")
    ]


def _lidos(servidor: ServidorHttp) -> list[str]:
    return [pedido.caminho for pedido in servidor.pedidos if pedido.caminho.startswith("/query/")]


def test_o_manifesto_e_o_de_uma_tv_que_nao_tem_volume():
    """The protocol has no level and no stop, so neither capability is declared;
    a driver never implements a method only to refuse.
    """
    manifesto = Roku.MANIFESTO
    # Validar raises listing every broken rule and answers nothing, so the test here is the
    # absence of the exception and never the value of the call.
    validar(manifesto)
    assert (manifesto.tipo, manifesto.categoria, manifesto.marca) == ("tv_roku", "tv", "Roku")
    assert manifesto.auth is Auth.NENHUMA
    assert manifesto.nuvem is False
    # The port of the protocol is the same on every model, so the registration asks for
    # nothing besides the address.
    assert manifesto.config_campos == ()
    assert manifesto.capacidades == (
        "ligar",
        "desligar",
        "mudo",
        "fonte",
        "tocar",
        "pausar",
        "proxima",
        "anterior",
        "tecla",
        "atalho",
        "comando_extra",
    )
    assert "volume" not in manifesto.capacidades
    assert "parar" not in manifesto.capacidades


def test_as_teclas_declaradas_sao_as_que_o_protocolo_tem():
    """There is no menu, guide or exit key in this protocol, and the digits only reach the
    device while a keyboard is on the screen, so none of them is declared.
    """
    declaradas = set(Roku.MANIFESTO.teclas)
    assert declaradas <= set(TECLAS)
    assert not declaradas & {"menu", "guia", "sair", *(f"digito_{n}" for n in range(10))}
    assert {"mais", "menos", "ok", "inicio", "play_pause"} <= declaradas


def test_a_assinatura_de_descoberta_e_o_st_do_protocolo():
    """The device announces its own search target and no mDNS service, and claiming the
    manufacturer as well would put a search for everything on the segment on the plan.
    """
    descoberta = Roku.MANIFESTO.descoberta
    assert descoberta.ssdp_st == ("roku:ecp",)
    assert (descoberta.ssdp_fabricantes, descoberta.mdns_servicos) == ((), ())
    assert montar([Roku.MANIFESTO]).por_st == {"roku:ecp": "tv_roku"}


def test_o_driver_sugere_o_que_ninguem_decora_e_nao_sugere_o_que_o_aparelho_lista():
    """The physical inputs of a Roku TV are always named the same way, and the id
    of a streaming app changes with what is installed and comes from the device on every poll.
    """
    sugeridas = por_lista(Roku.MANIFESTO)
    assert set(sugeridas) == {"entradas", "atalhos"}
    assert [item.valor for item in sugeridas["entradas"]] == [
        "tvinput.hdmi1",
        "tvinput.hdmi2",
        "tvinput.hdmi3",
        "tvinput.dtv",
    ]
    # Every suggested shortcut is a key of the closed list, because a shortcut is pressed and
    # never typed onto the path of the device.
    assert [item.valor for item in sugeridas["atalhos"]] == [
        "InstantReplay",
        "FindRemote",
        "Backspace",
    ]


def test_o_ajuste_do_aparelho_esta_escrito_nos_dois_idiomas():
    """The setting that makes every key fail while the device still reads as online is the
    one thing an integrator has to be told, so it is named in both languages.
    """
    textos = Roku.MANIFESTO.textos
    assert set(textos["pt"]) == set(textos["en"])
    for idioma in ("pt", "en"):
        assert "Control by mobile apps" in textos[idioma]["ajuste_controle_local"]
        assert textos[idioma]["sem_volume"]


async def test_um_poll_le_energia_entrada_lista_e_transporte(aparelho):
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.ligado) == (True, True)
    assert (estado.fonte, estado.fontes) == ("12", FONTES)
    assert estado.reproduzindo is True
    assert _lidos(servidor) == [APARELHO, APPS, APP_ATIVO, TOCADOR]


async def test_o_que_o_protocolo_nao_responde_fica_em_none(aparelho):
    """A driver that cannot tell leaves the field None. This protocol answers no
    level, no mute and no sound mode, so an optimistic value here would be an invention the
    reread would confirm as truth.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.volume, estado.mudo, estado.modo) == (None, None, None)
    assert (estado.temperatura, estado.vento) == (None, None)
    assert estado.detalhe == ""


@pytest.mark.parametrize("energia", ["Ready", "DisplayOff", "PowerOff"])
async def test_em_standby_o_aparelho_responde_e_o_hub_nao_pergunta_o_que_ele_nao_sabe(
    aparelho, energia
):
    """A device in standby keeps answering the protocol, which is why turning it on works over
    IP; but it answers the app of the last time it was on, and publishing that would show an
    input nobody is looking at.
    """
    async with ServidorHttp(_rotas(**{APARELHO: _aparelho(energia=energia)})) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.ligado) == (True, False)
    assert (estado.fonte, estado.reproduzindo, estado.tocando) == (None, None, None)
    assert _lidos(servidor) == [APARELHO, APPS]


@pytest.mark.parametrize("documento", [TELA_INICIAL, PROTETOR_DE_TELA])
async def test_a_tela_inicial_e_o_protetor_de_tela_nao_sao_uma_entrada(aparelho, documento):
    """Maps the input onto an index of the list of the registration, so the home
    screen has to read as no input at all and never as the word the device answered.
    """
    async with ServidorHttp(_rotas(**{APP_ATIVO: documento})) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    assert driver.estado().fonte is None
    assert TOCADOR not in _lidos(servidor)


async def test_numa_entrada_de_tv_o_transporte_nao_e_perguntado(aparelho):
    async with ServidorHttp(_rotas(**{APP_ATIVO: ENTRADA_HDMI})) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    assert driver.estado().fonte == "tvinput.hdmi1"
    assert driver.estado().reproduzindo is None
    assert TOCADOR not in _lidos(servidor)


async def test_na_antena_o_titulo_e_o_programa_e_nao_o_nome_do_app(aparelho):
    """The title is the program the antenna is showing, which is the only title
    the protocol has; the name of the app is the input and never the title.
    """
    async with ServidorHttp(_rotas(**{APP_ATIVO: ANTENA})) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    assert (driver.estado().fonte, driver.estado().tocando) == ("tvinput.dtv", PROGRAMA)
    assert TOCADOR not in _lidos(servidor)


async def test_a_antena_sem_o_ajuste_do_aparelho_nao_derruba_o_poll(aparelho):
    """The channel of the antenna is the one read the setting of the device governs, so a
    refusal there is a device with the setting off and not a poll worth losing.
    """
    rotas = _rotas(**{APP_ATIVO: ANTENA})
    rotas[CANAL] = (403, "")
    async with ServidorHttp(rotas) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.ligado, estado.fonte) == (True, True, "tvinput.dtv")
    assert estado.tocando is None


async def test_uma_leitura_perdida_do_app_ativo_nao_apaga_a_entrada(aparelho):
    """A lost read is not an empty screen: the device is still answering, and publishing no
    input would flap the DP 146, which is class B and counts against the 250
    reports a day of a licence. The list of apps of the same poll already keeps what it had,
    and these two are the same read.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert (driver.estado().fonte, driver.estado().reproduzindo) == ("12", True)
        servidor.rotas[APP_ATIVO] = (500, "")
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.fonte) == (True, "12")
    assert estado.reproduzindo is True
    assert estado.fontes == FONTES


@pytest.mark.parametrize(
    ("estado", "erro", "esperado"),
    [
        ("play", "false", True),
        ("pause", "false", False),
        ("close", "false", None),
        ("none", "false", None),
        ("play", "true", None),
    ],
)
async def test_o_transporte_e_play_ou_pause_e_o_resto_e_nao_sei(aparelho, estado, erro, esperado):
    async with ServidorHttp(_rotas(**{TOCADOR: _tocador(estado, erro)})) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    assert driver.estado().reproduzindo is esperado


async def test_a_lista_de_apps_nao_e_perguntada_a_cada_poll(aparelho, monkeypatch):
    """The list only changes when somebody installs an app, so asking on every poll would
    spend a request every ten seconds to learn nothing.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        monkeypatch.setattr(roku, "POLLS_ENTRE_LISTAS", 1)
        await driver.atualizar()
        await driver.atualizar()
        assert _lidos(servidor).count(APPS) == 1
        await driver.atualizar()
    assert _lidos(servidor).count(APPS) == 2
    assert driver.estado().fontes == FONTES


async def test_ligar_e_desligar_falam_as_teclas_de_energia(aparelho):
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("ligar") is None
        assert driver.estado().ligado is True
        assert await driver.executar("desligar") is None
    assert _teclas(servidor) == ["PowerOn", "PowerOff"]
    assert driver.estado().ligado is False


async def test_um_desligar_que_nao_volta_ainda_e_um_desligar(aparelho):
    """The device is gone before the POST returns, so the silence of that one command is
    success; reading it as a fault would answer eq_offline for the command that worked.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    assert await driver.executar("desligar") is None
    assert driver.estado().ligado is False
    # Every other command still tells the truth about a device that is not answering.
    assert await driver.executar("ligar") == "eq_offline"


@pytest.mark.parametrize("valor", [True, False])
async def test_o_mudo_e_uma_tecla_que_alterna_e_o_hub_nao_finge_saber(aparelho, valor):
    """The key toggles and the device reports the result nowhere, so the value is ignored and
    Estado.mudo stays None; an optimistic mute would be published on the DP as a
    fact, and the remote in the room desynchronises it on the first press.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert await driver.executar("mudo", valor) is None
    assert _teclas(servidor) == ["VolumeMute"]
    assert driver.estado().mudo is None


@pytest.mark.parametrize("valor", ["sim", 1, 0, None, 1.0])
async def test_um_mudo_fora_do_contrato_nunca_chega_ao_fio(aparelho, valor):
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("mudo", valor) == "invalid_value"
    assert _teclas(servidor) == []


async def test_tocar_e_pausar_sao_a_mesma_tecla_e_ela_so_e_apertada_quando_muda(aparelho):
    """Play toggles, so playing what already plays would PAUSE it; the driver holds the key
    when what it read says the device is already where the caller asked.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert driver.estado().reproduzindo is True
        assert await driver.executar("tocar") is None
        assert _teclas(servidor) == [], "playing what plays would pause it"
        assert await driver.executar("pausar") is None
        assert driver.estado().reproduzindo is False
        assert await driver.executar("pausar") is None
    assert _teclas(servidor) == ["Play"]


async def test_sem_transporte_lido_a_tecla_de_play_e_um_alterna_as_cegas(aparelho):
    """On a TV input there is no transport to read, and the honest thing is to press the key
    and say so in the texts, not to invent a state the device never answered.

    a driver that cannot tell leaves reproduzindo None. Writing the asked for value
    here would show a HDMI input as playing on the panel and, worse, the guard of the key
    would swallow the second tocar of the customer until the next poll, ten seconds later.
    """
    async with ServidorHttp(_rotas(**{APP_ATIVO: ENTRADA_HDMI})) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert driver.estado().reproduzindo is None
        assert await driver.executar("tocar") is None
        assert driver.estado().reproduzindo is None, "the device answered no transport"
        # A blind toggle stays blind: the next press reaches the device, it is not swallowed.
        assert await driver.executar("pausar") is None
        assert driver.estado().reproduzindo is None
    assert _teclas(servidor) == ["Play", "Play"]


async def test_proxima_e_anterior_sao_avanco_e_retrocesso(aparelho):
    """The protocol has no track skip: what it has is fast forward and rewind, and the texts
    say so, so nobody registers a scene expecting the next song.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("proxima") is None
        assert await driver.executar("anterior") is None
    assert _teclas(servidor) == ["Fwd", "Rev"]


@pytest.mark.parametrize("app", ["12", "tvinput.hdmi1", "74519"])
async def test_a_entrada_e_um_app_aberto_pelo_id(aparelho, app):
    """Keeps the value of the list of the registration, and the same field carries a
    number for a streaming app and a tvinput for a physical input of a Roku TV.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert await driver.executar("fonte", app) is None
    assert _abertos(servidor) == [app]
    estado = driver.estado()
    assert estado.fonte == app
    # The transport and the title belonged to the source that was playing, and it just changed.
    assert (estado.reproduzindo, estado.tocando) == (None, None)


@pytest.mark.parametrize(
    "valor",
    [
        "../keypress/PowerOff",
        "12/13",
        "tvinput hdmi1",
        "12?a=1",
        "%2e%2e",
        "tvinput.hdmi1;12",
        "",
        "   ",
        "x" * 70,
        7,
        True,
        None,
    ],
)
async def test_um_valor_que_sairia_do_caminho_nunca_chega_ao_fio(aparelho, valor):
    """The value lands in the path of the URL of the device, so a byte that could
    climb out of it would write a request that nobody wrote in this file.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("fonte", valor) == "invalid_value"
        assert await driver.executar("atalho", valor) == "invalid_value"
        assert await driver.executar("comando_extra", valor) == "invalid_value"
    assert servidor.pedidos == []


@pytest.mark.parametrize(
    ("palavra", "literal"),
    [
        ("mais", "VolumeUp"),
        ("menos", "VolumeDown"),
        ("canal_mais", "ChannelUp"),
        ("canal_menos", "ChannelDown"),
        ("cima", "Up"),
        ("baixo", "Down"),
        ("esquerda", "Left"),
        ("direita", "Right"),
        ("ok", "Select"),
        ("voltar", "Back"),
        ("inicio", "Home"),
        ("info", "Info"),
        ("play_pause", "Play"),
        ("proxima", "Fwd"),
        ("anterior", "Rev"),
    ],
)
async def test_a_palavra_da_secao_6_vira_o_literal_do_protocolo(aparelho, palavra, literal):
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("tecla", palavra) is None
    assert _teclas(servidor) == [literal]


@pytest.mark.parametrize("valor", ["menu", "guia", "sair", "digito_1", "Home", "", 7, None])
async def test_uma_tecla_fora_do_vocabulario_nunca_chega_ao_fio(aparelho, valor):
    """The manager already refuses a word the manifest does not declare, and the driver
    refuses it again: the name of the key is written into the path of the device.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("tecla", valor) == "invalid_value"
    assert servidor.pedidos == []


@pytest.mark.parametrize(
    ("valor", "literal"),
    [
        ("InstantReplay", "InstantReplay"),
        ("findremote", "FindRemote"),
        ("INPUTHDMI1", "InputHDMI1"),
        ("  Enter  ", "Enter"),
        ("InputTuner", "InputTuner"),
    ],
)
async def test_um_atalho_e_uma_tecla_da_lista_fechada_em_qualquer_caixa(aparelho, valor, literal):
    """A 200 with an empty body confirms nothing, so the only protection is the closed list:
    the name is checked here and never joined into the path as it was typed.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("atalho", valor) is None
        assert await driver.executar("comando_extra", valor) is None
    assert _teclas(servidor) == [literal, literal]


@pytest.mark.parametrize("valor", ["Search", "Lit_0", "Home/../PowerOff", "Play Play", "poweroff2"])
async def test_uma_tecla_que_o_protocolo_nao_tem_nunca_chega_ao_fio(aparelho, valor):
    """Search was retired in Roku OS 12.0 and the digits only work with a keyboard on the
    screen, so neither is on the closed list and neither reaches the device.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("atalho", valor) == "invalid_value"
    assert servidor.pedidos == []


@pytest.mark.parametrize("valor", ATALHOS_DOS_TEXTOS)
async def test_o_atalho_aceito_e_exatamente_o_que_os_textos_prometem(aparelho, valor):
    """The texts of the manifest are the contract of the panel, so what they name has to be
    what the wire takes: the key the vocabulary has no word for.
    """
    for idioma in ("pt", "en"):
        assert valor in Roku.MANIFESTO.textos[idioma]["cap_atalho"]
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("atalho", valor) is None
    assert _teclas(servidor) == [valor]


@pytest.mark.parametrize("valor", TECLAS_COM_CAPACIDADE)
async def test_um_atalho_nunca_e_uma_tecla_que_ja_tem_capacidade(aparelho, valor):
    """A shortcut that carried these went around the only two guards this driver has.

    An atalho Play pauses a device the state still reports as playing, so the tocar that
    follows is held by the guard of the transport and the customer presses play with no
    effect; an atalho PowerOff turns the device off while ligado stays true and, on real
    hardware, that POST never returns, so the command that worked answers eq_offline and a
    step of a scene is logged as a failure. Each of these keys has a capacity of its own.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert await driver.executar("atalho", valor) == "invalid_value"
        assert await driver.executar("comando_extra", valor.lower()) == "invalid_value"
    assert _teclas(servidor) == []
    estado = driver.estado()
    assert (estado.ligado, estado.reproduzindo) == (True, True)


async def test_o_atalho_que_o_driver_sugere_e_um_atalho_que_ele_aceita(aparelho):
    """A suggestion is born into the registration of the integrator, so a suggested
    value the guard of the driver refuses would be a shortcut that fails on the first press.
    """
    sugeridos = [item.valor for item in por_lista(Roku.MANIFESTO)["atalhos"]]
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        for valor in sugeridos:
            assert await driver.executar("atalho", valor) is None
    assert _teclas(servidor) == sugeridos


async def test_a_leitura_e_um_get_e_o_comando_e_um_post_de_corpo_vazio(aparelho):
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert await driver.executar("tecla", "ok") is None
        assert await driver.executar("fonte", "12") is None
    leituras = [p for p in servidor.pedidos if p.caminho.startswith("/query/")]
    comandos = [p for p in servidor.pedidos if not p.caminho.startswith("/query/")]
    assert {p.metodo for p in leituras} == {"GET"}
    assert [(p.metodo, p.corpo) for p in comandos] == [("POST", ""), ("POST", "")]


async def test_uma_tecla_recusada_pelo_aparelho_diz_qual_ajuste_procurar(aparelho, caplog):
    """With "Control by mobile apps" off the device reads as online, still changes app and
    fails every key, so the log names the setting instead of leaving the integrator hunting
    the network for a device that is answering perfectly.
    """
    rotas = _rotas()
    rotas["/keypress/Home"] = (403, "")
    async with ServidorHttp(rotas) as servidor:
        driver = aparelho(servidor)
        with caplog.at_level(logging.WARNING, logger="iphub.drivers.nativos.roku"):
            assert await driver.executar("tecla", "inicio") == "erro_aparelho"
        # The app still opens, which is the confusing part of that setting.
        assert await driver.executar("fonte", "12") is None
    assert "control by mobile apps" in caplog.text.lower()


async def test_um_aparelho_que_nao_responde_e_offline_depois_de_dois_polls(aparelho):
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is True, "one lost poll keeps the last state"
    await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.detalhe) == (False, "eq_offline")
    assert (estado.reproduzindo, estado.tocando) == (None, None)


async def test_uma_resposta_gigante_ou_sem_os_campos_nao_derruba_o_poll(aparelho):
    enorme = "<device-info>" + "<lixo>x</lixo>" * 20_000 + "</device-info>"
    async with ServidorHttp(_rotas(**{APARELHO: enorme})) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.fonte, estado.reproduzindo, estado.tocando) == (
        None,
        None,
        None,
        None,
    )
    assert APP_ATIVO not in _lidos(servidor)


async def test_uma_resposta_partida_em_dois_segmentos_e_lida_inteira(aparelho):
    """The power mode is the last field of the answer, so a driver reading only the first
    segment would publish a device that is on as a device that says nothing.
    """
    async with ServidorHttp(_rotas(), partir=True) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
    assert (driver.estado().ligado, driver.estado().fonte) == (True, "12")


@pytest.mark.parametrize("acao", ["volume", "parar", "modo", "temperatura", "vento", "agrupar"])
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(aparelho, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar(acao, 50) == "nao_suportado"
    assert servidor.pedidos == []


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(aparelho):
    """Whatever reaches a device takes an IP literal, never a name, so the hub
    never becomes a proxy into the LAN of the customer.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = aparelho(servidor, ip="roku.local")
        assert await driver.executar("ligar") == "eq_offline"
        await driver.atualizar()
    assert servidor.pedidos == []
    assert driver.estado().online is False


async def test_a_identidade_e_o_serial_que_o_aparelho_responde(aparelho, monkeypatch):
    """The identity is a serial and never the address. The udn is a valid uuid that
    nothing in this ecosystem keys on, and the device-id differs from the serial on the newer
    Roku TVs, so the serial is the only one stable across families.
    """
    async with ServidorHttp(_rotas()) as servidor:
        monkeypatch.setattr(roku, "PORTA", servidor.endereco[1])
        assert await Roku.identificar("127.0.0.1") == SERIAL
        assert await Roku.identificar("roku.local") is None
    assert _lidos(servidor) == [APARELHO], "a name is never dialled"


async def test_o_serial_volta_em_caixa_alta_e_o_ilegivel_nao_volta(aparelho, monkeypatch):
    async with ServidorHttp({APARELHO: (200, _aparelho(serial=SERIAL.lower()))}) as servidor:
        monkeypatch.setattr(roku, "PORTA", servidor.endereco[1])
        assert await Roku.identificar("127.0.0.1") == SERIAL
    async with ServidorHttp({APARELHO: (200, _aparelho(serial="!!"))}) as outro:
        monkeypatch.setattr(roku, "PORTA", outro.endereco[1])
        assert await Roku.identificar("127.0.0.1") is None
    async with ServidorHttp({APARELHO: (403, "")}) as recusa:
        monkeypatch.setattr(roku, "PORTA", recusa.endereco[1])
        assert await Roku.identificar("127.0.0.1") is None


@pytest.mark.parametrize("redirecionamento", [301, 302, 307])
async def test_um_redirecionamento_nao_e_uma_resposta(aparelho, redirecionamento):
    """The exchange goes out with redirects refused, so a 3xx comes back as a body
    of its own; it is the proof that the request was NOT served and never a success.

    Reading anything under 400 as an answer left a device that redirects reading online with
    no power mode forever, and made a keypress that never reached the device answer None,
    which is the word for done and what a step of a scene writes down as delivered.
    """
    rotas = _rotas()
    rotas[APARELHO] = (redirecionamento, "")
    rotas["/keypress/Home"] = (redirecionamento, "")
    async with ServidorHttp(rotas) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("tecla", "inicio") == "erro_aparelho"
        await driver.atualizar()
        await driver.atualizar()
        assert driver.estado().online is False
        assert await Roku.identificar("127.0.0.1") is None


async def test_um_204_e_uma_resposta_porque_e_um_2xx(aparelho):
    """The ceiling is the family and not the number: a device that answers a key with no
    content answered it, and refusing that would fail a command that worked.
    """
    rotas = _rotas()
    rotas["/keypress/Select"] = (204, "")
    async with ServidorHttp(rotas) as servidor:
        driver = aparelho(servidor)
        assert await driver.executar("tecla", "ok") is None
    assert _teclas(servidor) == ["Select"]


@pytest.mark.parametrize(
    ("rota", "corpo", "extras"),
    [
        (APARELHO, "<power-mode>", {}),
        (APPS, "<app ", {}),
        (APP_ATIVO, "<app ", {}),
        (TOCADOR, "<player ", {}),
        (CANAL, "<program-title>", {APP_ATIVO: ANTENA}),
    ],
)
async def test_um_corpo_hostil_no_teto_nao_congela_o_laco(aparelho, rota, corpo, extras):
    """Registers the danger and the declarative engine has regex_seguro to contain
    it; a native driver has no such fence and pays for the pattern it writes.

    re.search does not release the GIL, so a poll that spends seconds inside one pattern stops
    the DP bus, the panel and every other driver for longer than the interval of the poll
    itself. Measured before the fix, with the body at the exact ceiling: 8.9 s of CPU on the
    list of apps and 5.2 s on the player. Anything answering on 8060 writes such a body, and
    so does an address typed wrong that lands on another HTTP server.
    """
    async with ServidorHttp(_rotas(**{rota: _no_teto(corpo), **extras})) as servidor:
        driver = aparelho(servidor)
        inicio = time.process_time()
        await driver.atualizar()
        gasto = time.process_time() - inicio
    assert rota in _lidos(servidor), "the hostile body has to reach the read under test"
    assert gasto < ORCAMENTO_DE_CPU_S, f"{rota} spent {gasto:.1f} s of CPU in one poll"
    assert driver.estado().online is True


async def test_todo_codigo_que_sai_do_driver_e_um_estavel_do_contrato(aparelho):
    """A driver answers None or one of CODIGOS, and nothing else.

    Naming the constants of the driver here would prove nothing, because nothing ties them to
    what executar returns; what follows walks every refusal this driver has and collects what
    came back, so a code invented inside a branch fails this test.
    """
    rotas = _rotas()
    rotas["/keypress/Home"] = (403, "")
    rotas["/keypress/Select"] = (302, "")
    devolvidos = set()
    async with ServidorHttp(rotas) as servidor:
        driver = aparelho(servidor)
        await driver.atualizar()
        devolvidos.add(await driver.executar("volume", 50))
        devolvidos.add(await driver.executar("fonte", "../keypress/PowerOff"))
        devolvidos.add(await driver.executar("tecla", "menu"))
        devolvidos.add(await driver.executar("atalho", "Play"))
        devolvidos.add(await driver.executar("mudo", "sim"))
        devolvidos.add(await driver.executar("tecla", "inicio"))
        devolvidos.add(await driver.executar("tecla", "ok"))
    # The server is closed here: the device stopped answering.
    devolvidos.add(await driver.executar("ligar"))
    devolvidos.discard(None)
    assert devolvidos <= set(CODIGOS)
    assert devolvidos == {"nao_suportado", "invalid_value", "erro_aparelho", "eq_offline"}

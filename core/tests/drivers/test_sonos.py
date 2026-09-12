# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Sonos multiroom driver against a simulated speaker, no hardware, ever.

Each fact that a service call on a customer site would otherwise pay for is a test that
ATTACKS it here: a transport on a slave must never reach the wire while its volume must, a
slave must read what the master plays and never what it says about itself, TRANSITIONING must
never read as a stop, a routine UPnP error must never read as a broken speaker, and a document
carrying an entity must never reach a parser.

The wire is written by hand in this file, envelope, headers and all. A test that built the
request with the driver would agree with any change the driver made to it, which is exactly
what a protocol test exists to catch.
"""

import logging
from dataclasses import dataclass, field

import pytest

from iphub.drivers.base import CODIGOS
from iphub.drivers.descoberta import montar
from iphub.drivers.manifesto import Auth, por_lista, validar
from iphub.drivers.nativos import sonos
from iphub.drivers.nativos.sonos import Escravo, Sonos
from iphub.drivers.simulado import Pedido, ServidorHttp

DESCRICAO = "/xml/device_description.xml"
RENDERIZACAO = "/MediaRenderer/RenderingControl/Control"
TRANSPORTE = "/MediaRenderer/AVTransport/Control"
CONTEUDO = "/MediaServer/ContentDirectory/Control"
TOPOLOGIA = "/ZoneGroupTopology/Control"

UID = "RINCON_000E58ABCDEF01400"
OUTRO_UID = "RINCON_000E58FEDCBA01400"
UID_DO_QUARTO = "RINCON_000E5811111101400"
IP_DO_QUARTO = "10.0.0.11"
# An address of documentation, which nothing on this machine answers: a driver that dialled
# its own registration instead of the address it was handed would never reach the simulated box.
IP_INALCANCAVEL = "192.0.2.1"

MODELO = "Sonos Play:5"
RADIO = "Radio Aqui"
FAIXA = "Album da casa"
ENTRADA_FAVORITA = "TV da Sala"
ENDERECO_DA_RADIO = "x-sonosapi-stream:s2846?sid=254&amp;flags=32"
ENDERECO_DA_FAIXA = "x-rincon-cpcontainer:1006206calbum"
ENDERECO_DA_ENTRADA = f"x-rincon-stream:{OUTRO_UID}"
METADADO_DA_RADIO = "&lt;DIDL-Lite&gt;meta da radio&lt;/DIDL-Lite&gt;"

TITULO = "Musica 1"


@dataclass(frozen=True)
class _Cadastro:
    """A registration whose identity is NOT the uid of the speaker, on purpose: an integrator
    names a block what he likes, and the identity of the box is what the box says.
    """

    identidade: str = "sala"
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)


def _escapado(documento: str) -> str:
    """A whole document travelling as the text of one field, which is how this speaker answers
    its topology and its favourites.
    """
    return documento.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _envelope(acao: str, servico: str, campos: dict[str, str]) -> str:
    corpo = "".join(f"<{nome}>{valor}</{nome}>" for nome, valor in campos.items())
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        f'<s:Body><u:{acao}Response xmlns:u="urn:schemas-upnp-org:service:{servico}:1">'
        f"{corpo}</u:{acao}Response></s:Body></s:Envelope>"
    )


def _falha(codigo: str) -> str:
    return (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">'
        "<s:Body><s:Fault><faultcode>s:Client</faultcode>"
        '<detail><UPnPError xmlns="urn:schemas-upnp-org:control-1-0">'
        f"<errorCode>{codigo}</errorCode></UPnPError></detail>"
        "</s:Fault></s:Body></s:Envelope>"
    )


def _descricao(uid: str = UID, modelo: str = MODELO) -> str:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<root xmlns="urn:schemas-upnp-org:device-1-0">'
        "<specVersion><major>1</major><minor>0</minor></specVersion>"
        "<device><deviceType>urn:schemas-upnp-org:device:ZonePlayer:1</deviceType>"
        "<friendlyName>127.0.0.1 - Sonos</friendlyName>"
        f"<modelName>{modelo}</modelName><modelNumber>S5</modelNumber>"
        "<roomName>Sala</roomName><serialNum>00-0E-58-AB-CD-EF:A</serialNum>"
        f"<UDN>uuid:{uid}</UDN></device></root>"
    )


def _renderizacao(volume: str = "42", mudo: str = "0", fixo: str = "0") -> str:
    return _envelope(
        "GetVolume",
        "RenderingControl",
        {"CurrentVolume": volume, "CurrentMute": mudo, "CurrentFixed": fixo},
    )


def _transporte(
    midia: str = "x-sonosapi-stream:s2846",
    situacao: str = "PLAYING",
    faixa: str = "x-sonosapi-stream:s2846",
    titulo: str = TITULO,
) -> str:
    metadado = (
        '&lt;DIDL-Lite xmlns:dc="http://purl.org/dc/elements/1.1/"&gt;&lt;item&gt;'
        f"&lt;dc:title&gt;{titulo}&lt;/dc:title&gt;&lt;/item&gt;&lt;/DIDL-Lite&gt;"
        if titulo
        else ""
    )
    return _envelope(
        "GetMediaInfo",
        "AVTransport",
        {
            "CurrentURI": midia,
            "CurrentTransportState": situacao,
            "CurrentTransportStatus": "OK",
            "TrackURI": faixa,
            "TrackDuration": "0:03:20",
            "TrackMetaData": metadado,
        },
    )


DIDL_DOS_FAVORITOS = (
    '<DIDL-Lite xmlns="urn:schemas-upnp-org:metadata-1-0/DIDL-Lite/" '
    'xmlns:dc="http://purl.org/dc/elements/1.1/" '
    'xmlns:r="urn:schemas-rinconnetworks-com:metadata-1-0/">'
    f'<item id="FV:2/0" parentID="FV:2"><dc:title>{RADIO}</dc:title>'
    f"<res>{ENDERECO_DA_RADIO}</res><r:resMD>{METADADO_DA_RADIO}</r:resMD></item>"
    f'<item id="FV:2/1" parentID="FV:2"><dc:title>{FAIXA}</dc:title>'
    f"<res>{ENDERECO_DA_FAIXA}</res><r:resMD>&lt;DIDL-Lite&gt;fila&lt;/DIDL-Lite&gt;</r:resMD>"
    "</item>"
    f'<item id="FV:2/2" parentID="FV:2"><dc:title>{ENTRADA_FAVORITA}</dc:title>'
    f"<res>{ENDERECO_DA_ENTRADA}</res><r:resMD>&lt;DIDL-Lite&gt;linha&lt;/DIDL-Lite&gt;</r:resMD>"
    "</item></DIDL-Lite>"
)


def _conteudo(didl: str = DIDL_DOS_FAVORITOS) -> str:
    return _envelope(
        "Browse",
        "ContentDirectory",
        {"Result": _escapado(didl), "NumberReturned": "2", "TotalMatches": "2"},
    )


def _zonas(coordenador: str = UID) -> str:
    return (
        "<ZoneGroupState><ZoneGroups>"
        f'<ZoneGroup Coordinator="{coordenador}" ID="{coordenador}:46">'
        f'<ZoneGroupMember UUID="{coordenador}" ZoneName="Sala" '
        'Location="http://127.0.0.1:1400/xml/device_description.xml"/>'
        f'<ZoneGroupMember UUID="{UID_DO_QUARTO}" ZoneName="Quarto" '
        f'Location="http://{IP_DO_QUARTO}:1400/xml/device_description.xml">'
        f'<Satellite UUID="{UID_DO_QUARTO}:SAT" ZoneName="Quarto (R)" '
        'Location="http://10.0.0.12:1400/xml/device_description.xml"/>'
        "</ZoneGroupMember>"
        '<ZoneGroupMember UUID="RINCON_000E5822222201400" ZoneName="Ponte" IsZoneBridge="1" '
        'Location="http://10.0.0.13:1400/xml/device_description.xml"/>'
        '<ZoneGroupMember UUID="RINCON_000E5833333301400" ZoneName="Escondida" Invisible="1" '
        'Location="http://10.0.0.14:1400/xml/device_description.xml"/>'
        '<ZoneGroupMember UUID="RINCON_000E5844444401400" ZoneName="Por nome" '
        'Location="http://caixa.local:1400/xml/device_description.xml"/>'
        "</ZoneGroup>"
        f'<ZoneGroup Coordinator="{OUTRO_UID}" ID="{OUTRO_UID}:1">'
        f'<ZoneGroupMember UUID="{OUTRO_UID}" ZoneName="Outra casa" '
        'Location="http://10.0.0.20:1400/xml/device_description.xml"/>'
        "</ZoneGroup></ZoneGroups></ZoneGroupState>"
    )


def _topologia(zonas: str = "") -> str:
    return _envelope(
        "GetZoneGroupState",
        "ZoneGroupTopology",
        {"ZoneGroupState": _escapado(zonas or _zonas())},
    )


def _rotas(**extras: tuple[int, str]) -> dict[str, tuple[int, str]]:
    """Every door of the speaker answered, plus whatever the test wants on top.

    A service answers many actions on one path, so each canned answer carries every field
    the driver may read from that service; the driver reads by field and never by action name.
    """
    rotas = {
        DESCRICAO: (200, _descricao()),
        RENDERIZACAO: (200, _renderizacao()),
        TRANSPORTE: (200, _transporte()),
        CONTEUDO: (200, _conteudo()),
        TOPOLOGIA: (200, _topologia()),
    }
    rotas.update(extras)
    return rotas


def _cabecalho(pedido: Pedido, nome: str) -> str:
    for chave, valor in pedido.cabecalhos.items():
        if chave.lower() == nome.lower():
            return valor
    return ""


def _acoes(servidor: ServidorHttp) -> list[str]:
    return [
        _cabecalho(pedido, "soapaction").rpartition("#")[2]
        for pedido in servidor.pedidos
        if pedido.metodo == "POST"
    ]


def _pedido_de(servidor: ServidorHttp, acao: str) -> Pedido:
    for pedido in servidor.pedidos:
        if _cabecalho(pedido, "soapaction").endswith(f"#{acao}"):
            return pedido
    raise AssertionError(f"the driver never sent {acao}: {_acoes(servidor)}")


def _corpo_de(servidor: ServidorHttp, acao: str) -> str:
    return _pedido_de(servidor, acao).corpo


@pytest.fixture
async def caixa(monkeypatch):
    """A simulated speaker plus a driver aimed at its port, closed when the test ends."""
    criados: list[Sonos] = []

    def montar_driver(servidor: ServidorHttp | None = None, *, ip: str = "127.0.0.1") -> Sonos:
        if servidor is not None:
            monkeypatch.setattr(sonos, "PORTA", servidor.endereco[1])
        driver = Sonos(_Cadastro(ip=ip))
        criados.append(driver)
        return driver

    yield montar_driver
    for driver in criados:
        await driver.parar()


def test_o_manifesto_e_valido_e_nao_promete_ligar_nem_desligar():
    """Sections 6 and 14: this speaker has no power at all, so the capability is OMITTED, never
    implemented to refuse.
    """
    manifesto = Sonos.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "multiroom_sonos"
    assert manifesto.categoria == "multiroom"
    # The panel lists the makes this hub reaches, and an empty one there is a driver
    # of no make in particular (a generic protocol), which this speaker is not.
    assert manifesto.marca == "Sonos"
    assert manifesto.auth is Auth.NENHUMA
    assert manifesto.nuvem is False
    assert manifesto.capacidades == (
        "volume",
        "mudo",
        "fonte",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
        "agrupar",
        "atalho",
    )
    assert "ligar" not in manifesto.capacidades
    assert "desligar" not in manifesto.capacidades
    assert "comando_extra" not in manifesto.capacidades
    assert "tecla" not in manifesto.capacidades
    # The ip is the address the discovery re-resolves, and this protocol fixes the
    # port, so the registration asks for nothing else.
    assert manifesto.config_campos == ()


def test_o_driver_sugere_as_entradas_e_nunca_os_favoritos_da_casa():
    """A driver suggests only what IT knows; the favourites belong to the house and
    change whenever the owner opens the application of the manufacturer.
    """
    sugeridas = por_lista(Sonos.MANIFESTO)
    assert set(sugeridas) == {"entradas"}
    assert [item.valor for item in sugeridas["entradas"]] == ["line_in", "tv"]


def test_os_textos_do_manifesto_estao_nos_dois_idiomas():
    """Every message the panel shows about the driver comes from textos."""
    textos = Sonos.MANIFESTO.textos
    assert set(textos) == {"pt", "en"}
    assert set(textos["pt"]) == set(textos["en"])
    assert "descricao" in textos["pt"]
    for capacidade in ("fonte", "tocar", "atalho", "agrupar"):
        assert f"cap_{capacidade}" in textos["pt"]
    # The panel renders the help of each list of the registration from these keys, and a list
    # whose values are strings of a protocol is a field the integrator otherwise fills guessing.
    for lista in ("entradas", "atalhos"):
        assert f"lista_{lista}" in textos["pt"]
        assert f"lista_{lista}" in textos["en"]
    # The one fault of this speaker no command of the hub can fix travels in both languages.
    assert "upnp_desligado" in textos["pt"]
    assert "upnp_desligado" in textos["en"]


def test_a_descoberta_gerada_reivindica_a_assinatura_da_caixa():
    """The sweep plan is GENERATED from the manifest, never written beside it."""
    plano = montar([Sonos.MANIFESTO])
    assert plano.por_st == {"urn:schemas-upnp-org:device:ZonePlayer:1": "multiroom_sonos"}


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """A code invented here is a phrase the panel cannot translate."""
    usados = {
        sonos.EQ_OFFLINE,
        sonos.INVALID_VALUE,
        sonos.ERRO_APARELHO,
        sonos.NAO_SUPORTADO,
        sonos.RECUSA_DE_GRUPO,
    }
    assert usados <= set(CODIGOS)


async def test_a_identidade_e_o_uid_da_descricao_e_nunca_o_ip(caixa, monkeypatch):
    """The identity is the uid the speaker answers, so a finding of the sweep turns
    into a registration nobody has to type.
    """
    async with ServidorHttp(_rotas()) as servidor:
        monkeypatch.setattr(sonos, "PORTA", servidor.endereco[1])
        assert await Sonos.identificar("127.0.0.1") == UID
        # Only an IP literal reaches a device, so the hub is never a resolver.
        assert await Sonos.identificar("caixa.local") is None
        assert servidor.pedidos[-1].metodo == "GET"
        assert servidor.pedidos[-1].caminho == DESCRICAO


@pytest.mark.parametrize(
    "documento",
    [
        "<root><device><UDN>uuid:naoeumsonos</UDN></device></root>",
        "<root><device><serialNum>00-0E-58</serialNum></device></root>",
        "nao e xml",
        "",
    ],
)
async def test_uma_descricao_sem_uid_nao_vira_identidade(caixa, monkeypatch, documento):
    async with ServidorHttp({DESCRICAO: (200, documento)}) as servidor:
        monkeypatch.setattr(sonos, "PORTA", servidor.endereco[1])
        assert await Sonos.identificar("127.0.0.1") is None


async def test_um_poll_le_volume_mudo_entrada_transporte_e_titulo(caixa):
    """One poll asks the identity, then the two facts of this box, then who it follows, then
    what plays, and publishes the typed state.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.volume == 42
    assert estado.mudo is False
    assert estado.fonte == "radio"
    assert estado.reproduzindo is True
    assert estado.tocando == TITULO
    # This speaker has no power, so the power fact stays unknown for ever.
    assert estado.ligado is None
    assert driver.identidade_do_aparelho() == UID
    assert driver.e_escravo() is False
    assert _acoes(servidor) == [
        "GetOutputFixed",
        "GetVolume",
        "GetMute",
        "GetMediaInfo",
        "GetTransportInfo",
        "GetPositionInfo",
    ]


async def test_a_caixa_que_voltou_e_perguntada_de_novo_quem_ela_e(caixa):
    """The address is only where the identity answered today, and while a speaker was
    away the lease may have handed its address to another box.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.identidade_do_aparelho() == UID
        # Another box answering at this address is not this block, and it never gets commanded.
        servidor.rotas[DESCRICAO] = (200, _descricao(uid=OUTRO_UID))
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"
        await driver.atualizar()
    assert driver.estado().online is True
    assert driver.identidade_do_aparelho() == OUTRO_UID


@pytest.mark.parametrize(
    ("situacao", "reproduzindo"),
    [("PLAYING", True), ("TRANSITIONING", True), ("PAUSED_PLAYBACK", False), ("STOPPED", False)],
)
async def test_transitioning_e_o_vao_entre_duas_faixas_e_nunca_uma_parada(
    caixa, situacao, reproduzindo
):
    """Reading TRANSITIONING as a stop makes the application send a play to a speaker that is
    already playing, which is the same damage describes for the other multiroom driver.
    """
    rotas = _rotas(**{TRANSPORTE: (200, _transporte(situacao=situacao))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().reproduzindo is reproduzindo


async def test_no_vao_entre_duas_faixas_a_midia_nao_e_relida(caixa):
    """Between two tracks this speaker answers the media of neither of them, so the last one
    read stands and the position is not even asked for.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        servidor.rotas[TRANSPORTE] = (200, _transporte(situacao="TRANSITIONING"))
        servidor.pedidos.clear()
        await driver.atualizar()
    assert "GetPositionInfo" not in _acoes(servidor)
    assert driver.estado().tocando == TITULO


async def test_o_que_nao_esta_tocando_nao_reporta_titulo(caixa):
    """The transport and the title are different facts, and a speaker that stopped
    still carries the title of what it played before.
    """
    rotas = _rotas(**{TRANSPORTE: (200, _transporte(situacao="STOPPED"))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().reproduzindo is False
    assert driver.estado().tocando is None


async def test_uma_pausa_sem_midia_carregada_e_estado_normal(caixa):
    """This speaker considers itself paused with nothing loaded, which happens whenever a
    service hands the playback to another device; it is not a defect to work around.
    """
    parada = _transporte(midia="", situacao="PAUSED_PLAYBACK", faixa="", titulo="")
    async with ServidorHttp(_rotas(**{TRANSPORTE: (200, parada)})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.reproduzindo, estado.tocando, estado.fonte) == (
        True,
        False,
        None,
        None,
    )


@pytest.mark.parametrize(
    ("endereco", "fonte"),
    [
        ("x-rincon-stream:RINCON_000E58ABCDEF01400", "line_in"),
        ("x-sonos-htastream:RINCON_000E58ABCDEF01400:spdif", "tv"),
        ("x-sonosapi-radio:trilha", "radio"),
        ("x-rincon-mp3radio://estacao/fluxo", "radio"),
        ("aac:fluxo", "radio"),
        ("x-sonos-vli:RINCON_1,airplay:1", "airplay"),
        ("x-sonos-vli:RINCON_1,spotify:2", "spotify"),
        ("x-file-cifs://servidor/musica.flac", "biblioteca"),
        ("https://exemplo/arquivo.mp3", "web"),
        ("x-sonos-vli:RINCON_1,desconhecido:1", None),
        ("", None),
    ],
)
async def test_a_entrada_ativa_sai_da_forma_do_endereco_da_faixa(caixa, endereco, fonte):
    """The active input travels as the position of a driver value in the list of the
    registration, so the driver publishes the value and never a phrase.
    """
    rotas = _rotas(**{TRANSPORTE: (200, _transporte(faixa=endereco))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().fonte == fonte


async def test_o_volume_vai_no_0_a_100_com_o_envelope_do_protocolo(caixa):
    """The volume is ALWAYS 0 to 100, and this speaker speaks the same scale, so
    nothing is converted and the whole envelope is written here by hand.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("volume", 50) is None
        assert driver.estado().volume == 50
    assert _corpo_de(servidor, "SetVolume") == (
        '<?xml version="1.0"?>'
        '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
        ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
        '<s:Body><u:SetVolume xmlns:u="urn:schemas-upnp-org:service:RenderingControl:1">'
        "<InstanceID>0</InstanceID><Channel>Master</Channel><DesiredVolume>50</DesiredVolume>"
        "</u:SetVolume></s:Body></s:Envelope>"
    )
    pedido = _pedido_de(servidor, "SetVolume")
    # The details of the wire that look optional: the quotes of the content type are part of it,
    # and the action of the header goes without quotes around its value.
    assert _cabecalho(pedido, "content-type") == 'text/xml; charset="utf-8"'
    assert (
        _cabecalho(pedido, "soapaction")
        == "urn:schemas-upnp-org:service:RenderingControl:1#SetVolume"
    )


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_ao_fio(caixa, valor):
    """True is an int in Python: a mute arriving where a volume belongs would silence a room."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("volume", valor) == "invalid_value"
        assert servidor.pedidos == []


async def test_o_mudo_manda_um_e_zero_como_o_servico_espera(caixa):
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("mudo", True) is None
        assert driver.estado().mudo is True
        assert "<DesiredMute>1</DesiredMute>" in _corpo_de(servidor, "SetMute")
        servidor.pedidos.clear()
        assert await driver.executar("mudo", False) is None
        assert driver.estado().mudo is False
        assert "<DesiredMute>0</DesiredMute>" in _corpo_de(servidor, "SetMute")
        assert await driver.executar("mudo", 1) == "invalid_value"


@pytest.mark.parametrize(
    ("acao", "esperada"),
    [
        ("tocar", "Play"),
        ("pausar", "Pause"),
        ("parar", "Stop"),
        ("proxima", "Next"),
        ("anterior", "Previous"),
    ],
)
async def test_o_transporte_fala_as_acoes_do_servico_com_a_velocidade(caixa, acao, esperada):
    """The Speed of a pause, a stop, a next and a previous is not in the specification of the
    service and is what these boxes are known to take, so it goes on all five.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar(acao) is None
    corpo = _corpo_de(servidor, esperada)
    assert "<InstanceID>0</InstanceID><Speed>1</Speed>" in corpo
    assert f'<u:{esperada} xmlns:u="urn:schemas-upnp-org:service:AVTransport:1">' in corpo


async def test_pausar_e_parar_limpam_o_titulo_e_tocar_nao_recebe_endereco(caixa):
    """Play resumes what is loaded and takes no value: what to load is an input or a shortcut,
    and a play that silently ignored an address would play something other than what was asked.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().tocando == TITULO
        assert await driver.executar("tocar", "http://exemplo/fluxo.mp3") == "invalid_value"
        assert await driver.executar("parar") is None
        assert driver.estado().reproduzindo is False
        assert driver.estado().tocando is None
        assert await driver.executar("tocar") is None
        assert driver.estado().reproduzindo is True
    assert _acoes(servidor).count("Play") == 1


async def test_a_entrada_e_carregada_com_o_uid_da_propria_caixa(caixa):
    """The address of an input is made of the uid of the speaker ITSELF: the same string with
    the uid of another box plays the line input of that other box instead.
    """
    rotas = _rotas(**{DESCRICAO: (200, _descricao(modelo="Sonos Amp"))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().fontes == ("line_in", "tv")
        assert await driver.executar("fonte", "line_in") is None
        assert f"<CurrentURI>x-rincon-stream:{UID}</CurrentURI>" in _corpo_de(
            servidor, "SetAVTransportURI"
        )
        assert driver.estado().fonte == "line_in"
        servidor.pedidos.clear()
        assert await driver.executar("fonte", "tv") is None
        assert f"<CurrentURI>x-sonos-htastream:{UID}:spdif</CurrentURI>" in _corpo_de(
            servidor, "SetAVTransportURI"
        )
        # Loading an input is only half of it: the speaker still has to be told to play.
        assert _acoes(servidor) == ["SetAVTransportURI", "Play"]


@pytest.mark.parametrize(
    ("modelo", "fontes"),
    [
        ("Sonos Play:5", ("line_in",)),
        ("Sonos PORT", ("line_in",)),
        ("Sonos Beam", ("tv",)),
        ("Sonos Playbar", ("tv",)),
        ("Sonos Amp", ("line_in", "tv")),
        ("Sonos One SL", ()),
        ("", ()),
    ],
)
async def test_o_modelo_sugere_as_entradas_e_o_vocabulario_e_quem_recusa(caixa, modelo, fontes):
    """The model of the description says which inputs the box is known to carry, which is what
    the panel offers as the examples of the list; what is refused before the wire is a value
    outside the two words this driver has an address for.
    """
    rotas = _rotas(**{DESCRICAO: (200, _descricao(modelo=modelo))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().fontes == fontes
        servidor.pedidos.clear()
        for entrada in ("bluetooth", "optical", "", 7, None, True):
            assert await driver.executar("fonte", entrada) == "invalid_value", entrada
        assert servidor.pedidos == []


async def test_a_entrada_do_cadastro_chega_ao_fio_num_modelo_que_a_tabela_nao_conhece(caixa):
    """A fixed table of models ages with every launch of the manufacturer, so it only SUGGESTS
    and the list of the registration decides: a refusal by the table is a button of the panel
    that answers invalid for ever, with no way to fix it from the panel.
    """
    rotas = _rotas(**{DESCRICAO: (200, _descricao(modelo="Sonos Five"))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().fontes == (), "the table has never heard of this model"
        servidor.pedidos.clear()
        assert await driver.executar("fonte", "line_in") is None
        assert f"<CurrentURI>x-rincon-stream:{UID}</CurrentURI>" in _corpo_de(
            servidor, "SetAVTransportURI"
        )
    assert _acoes(servidor) == ["SetAVTransportURI", "Play"]


async def test_um_atalho_e_o_favorito_da_casa_achado_pelo_titulo(caixa):
    """The value of a shortcut is the TITLE of a favourite and never its position,
    because the position walks the moment the owner reorders the favourites.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("atalho", " radio aqui ") is None
        assert driver.estado().reproduzindo is True
        # A radio names nothing of itself until the station sends metadata, which many never
        # do, so the title of the favourite stands in meanwhile.
        assert driver.estado().tocando == "radio aqui"
    procura = _pedido_de(servidor, "Browse")
    assert "<ObjectID>FV:2</ObjectID>" in procura.corpo
    assert "<BrowseFlag>BrowseDirectChildren</BrowseFlag>" in procura.corpo
    # The content directory of this speaker only answers a browse well with an agent of its own
    # family, and no other service of it carries one.
    assert _cabecalho(procura, "user-agent") == "Sonos/83.1-61210"
    assert _cabecalho(_pedido_de(servidor, "Play"), "user-agent") != "Sonos/83.1-61210"
    carga = _corpo_de(servidor, "SetAVTransportURI")
    assert "<CurrentURI>x-sonosapi-stream:s2846?sid=254&amp;flags=32</CurrentURI>" in carga
    assert (
        "<CurrentURIMetaData>&lt;DIDL-Lite&gt;meta da radio&lt;/DIDL-Lite&gt;"
        "</CurrentURIMetaData>" in carga
    )
    assert _acoes(servidor) == ["Browse", "SetAVTransportURI", "Play"]


async def test_o_titulo_do_favorito_cai_quando_a_caixa_troca_de_fonte_por_fora(caixa):
    """The name of the favourite only stands in while the station names nothing of itself, and
    the owner pressing the input button on the box is not that station going quiet: publishing
    the radio over a line input names something the speaker is not playing.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("atalho", RADIO) is None
        assert driver.estado().tocando == RADIO
        servidor.rotas[TRANSPORTE] = (200, _transporte(faixa=f"x-rincon-stream:{UID}", titulo=""))
        await driver.atualizar()
    estado = driver.estado()
    assert estado.fonte == "line_in"
    assert estado.tocando is None


async def test_a_entrada_de_linha_nao_publica_o_metadado_da_faixa_de_rede_anterior(caixa):
    """On a line input and on the TV this speaker answers the metadata of the last track of the
    network it played, so a title read there is the name of something it is not playing.
    """
    velha = _transporte(faixa=f"x-sonos-htastream:{UID}:spdif", titulo="Faixa velha da radio")
    async with ServidorHttp(_rotas(**{TRANSPORTE: (200, velha)})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.fonte == "tv"
    assert estado.tocando is None


async def test_um_favorito_de_entrada_de_linha_mantem_o_nome_dele_no_titulo(caixa):
    """A favourite of a line input is one this driver plays, and the name the owner gave it is
    the only title that input will ever have, so it stands while the speaker plays it.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("atalho", ENTRADA_FAVORITA) is None
        servidor.rotas[TRANSPORTE] = (
            200,
            _transporte(faixa=ENDERECO_DA_ENTRADA, titulo="Faixa velha da radio"),
        )
        await driver.atualizar()
    estado = driver.estado()
    assert estado.fonte == "line_in"
    assert estado.tocando == ENTRADA_FAVORITA


async def test_um_favorito_de_fila_e_recusado_e_um_que_nao_existe_tambem(caixa):
    """A favourite of a track or of a playlist is a queue to clear, fill, point at and seek,
    which is four more exchanges and a queue to keep; a favourite that is gone is a registration
    that names something the house no longer has.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("atalho", FAIXA) == "nao_suportado"
        assert await driver.executar("atalho", "Nao existe") == "invalid_value"
        assert _acoes(servidor) == ["Browse", "Browse"], "neither ever loads anything"


@pytest.mark.parametrize("valor", ["", "   ", "x" * 65, 7, None, True])
async def test_um_atalho_fora_do_contrato_nunca_pergunta_nada_a_caixa(caixa, valor):
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("atalho", valor) == "invalid_value"
        assert servidor.pedidos == []


async def test_o_escravo_perde_o_transporte_e_mantem_o_volume(caixa):
    """The half that is the OPPOSITE of the other multiroom driver of this image: what plays and
    what is loaded belong to the coordinator, and the volume belongs to each box, so a slave is
    refused the transport and still takes a volume and a mute.
    """
    escrava = _transporte(midia=f"x-rincon:{OUTRO_UID}")
    async with ServidorHttp(_rotas(**{TRANSPORTE: (200, escrava)})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.e_escravo() is True
        servidor.pedidos.clear()
        for acao, valor in (
            ("tocar", None),
            ("pausar", None),
            ("parar", None),
            ("proxima", None),
            ("anterior", None),
            ("fonte", "line_in"),
            ("atalho", RADIO),
        ):
            assert await driver.executar(acao, valor) == "nao_suportado", acao
        assert servidor.pedidos == [], "a refused command must never reach the wire"
        assert await driver.executar("volume", 30) is None
        assert await driver.executar("mudo", True) is None
    assert _acoes(servidor) == ["SetVolume", "SetMute"]


async def test_o_escravo_nao_e_lido_pela_midia_dele_e_sim_pelo_espelho(caixa):
    """A slave answers the last track it played by itself, so reading its media would publish an
    old title and a transport that has nothing to do with the group.
    """
    escrava = _transporte(
        midia=f"x-rincon:{OUTRO_UID}", situacao="STOPPED", faixa="x-file-cifs://velha.flac"
    )
    async with ServidorHttp(_rotas(**{TRANSPORTE: (200, escrava)})) as servidor:
        driver = caixa(servidor)
        driver.espelhar("Faixa do mestre", True)
        await driver.atualizar()
        servidor.pedidos.clear()
        await driver.atualizar()
    estado = driver.estado()
    assert estado.tocando == "Faixa do mestre"
    assert estado.reproduzindo is True
    assert estado.fonte is None
    assert _acoes(servidor) == ["GetVolume", "GetMute", "GetMediaInfo"]


async def test_o_escravo_que_saiu_do_grupo_por_dois_polls_e_reportado(caixa):
    """The physical group dissolved by itself and the logical state has to be reconciled; one
    poll out of the slave mode is not enough to say so.
    """
    escrava = _transporte(midia=f"x-rincon:{OUTRO_UID}")
    async with ServidorHttp(_rotas(**{TRANSPORTE: (200, escrava)})) as servidor:
        driver = caixa(servidor)
        driver.espelhar("Faixa do mestre", True)
        await driver.atualizar()
        assert driver.e_escravo() is True
        servidor.rotas[TRANSPORTE] = (200, _transporte())
        await driver.atualizar()
        assert driver.saiu_do_grupo() is False, "one poll out of the mode is a hiccup"
        assert driver.e_escravo() is True
        await driver.atualizar()
    assert driver.saiu_do_grupo() is True
    assert driver.e_escravo() is False
    assert driver.estado().tocando == TITULO


async def test_marcar_grupo_apaga_o_veredito_e_o_espelho(caixa):
    """Whoever owns the group logic has just declared where this speaker stands, and a verdict
    left over from an earlier group would make the reconcile tear down the group it formed.
    """
    escrava = _transporte(midia=f"x-rincon:{OUTRO_UID}")
    async with ServidorHttp(_rotas(**{TRANSPORTE: (200, escrava)})) as servidor:
        driver = caixa(servidor)
        driver.espelhar("Faixa do mestre", True)
        await driver.atualizar()
        servidor.rotas[TRANSPORTE] = (200, _transporte())
        await driver.atualizar()
        await driver.atualizar()
        assert driver.saiu_do_grupo() is True
        driver.marcar_grupo(True)
        assert driver.saiu_do_grupo() is False
        driver.marcar_grupo(False)
        await driver.atualizar()
    assert driver.estado().tocando == TITULO


async def test_a_caixa_que_acabou_de_entrar_num_grupo_recusa_a_entrada_sem_esperar_o_poll(caixa):
    """A member that takes an input loads an address of its own and becomes the coordinator of
    itself, which takes the group down in silence; the mark of a slave arrives by a poll that is
    up to ten seconds away, so the move that formed the group is what closes the door.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.entrar_no_grupo("127.0.0.1") is None
        driver.marcar_grupo(True)
        assert driver.e_escravo() is False, "no poll has seen this box follow anybody yet"
        servidor.pedidos.clear()
        for acao, valor in (
            ("fonte", "line_in"),
            ("atalho", RADIO),
            ("tocar", None),
            ("pausar", None),
            ("parar", None),
            ("proxima", None),
            ("anterior", None),
        ):
            assert await driver.executar(acao, valor) == "nao_suportado", acao
        assert servidor.pedidos == [], "a refused command must never reach the wire"
        # Leaving the group hands the transport back at once, with no poll in between.
        assert await driver.executar("agrupar", None) is None
        servidor.pedidos.clear()
        assert await driver.executar("fonte", "line_in") is None
    assert _acoes(servidor) == ["SetAVTransportURI", "Play"]


async def test_o_mestre_marcado_num_grupo_mantem_a_entrada_e_o_atalho(caixa):
    """The owner of the group logic marks the MASTER as being in a group too, and on this
    speaker the master is exactly the box that loads an input and starts the audio of everybody.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        driver.marcar_grupo(True)
        servidor.pedidos.clear()
        assert await driver.executar("fonte", "line_in") is None
        assert await driver.executar("atalho", RADIO) is None
    assert _acoes(servidor) == [
        "SetAVTransportURI",
        "Play",
        "Browse",
        "SetAVTransportURI",
        "Play",
    ]


async def test_a_caixa_tirada_do_grupo_pelo_dono_do_grupo_recupera_o_transporte(caixa):
    """Whoever owns the group logic says a member left, and the transport of that box comes back
    with that word and not two polls later.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.entrar_no_grupo("127.0.0.1") is None
        assert await driver.executar("tocar") == "nao_suportado"
        driver.marcar_grupo(False)
        servidor.pedidos.clear()
        assert await driver.executar("tocar") is None
    assert _acoes(servidor) == ["Play"]


async def test_entrar_num_grupo_pergunta_ao_mestre_quem_ele_e(caixa):
    """The invite is made of the uid of the master and the contract hands an address, which is
    only where that master answered today; a stale uid would form a group with whoever holds it.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.identidade_do_aparelho() == UID
        servidor.rotas[DESCRICAO] = (200, _descricao(uid=OUTRO_UID))
        servidor.pedidos.clear()
        assert await driver.entrar_no_grupo("127.0.0.1") is None
    assert servidor.pedidos[0].metodo == "GET"
    # A box that already leads a group of its own carries it into the new one and its members
    # inherit a queue nobody asked for, so it stands alone before the invite.
    assert _acoes(servidor) == ["BecomeCoordinatorOfStandaloneGroup", "SetAVTransportURI"]
    assert f"<CurrentURI>x-rincon:{OUTRO_UID}</CurrentURI>" in _corpo_de(
        servidor, "SetAVTransportURI"
    )


async def test_um_mestre_que_nao_responde_nao_forma_grupo(caixa):
    """A resolution that failed is a refused move, never a group formed with an old uid."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        servidor.rotas.clear()
        servidor.pedidos.clear()
        assert await driver.entrar_no_grupo("127.0.0.1") == "erro_aparelho"
    assert _acoes(servidor) == []


async def test_desfazer_um_grupo_e_a_caixa_voltar_a_ficar_sozinha(caixa):
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.desfazer_grupo() is None
        assert await driver.executar("agrupar", None) is None
        assert await driver.executar("agrupar", "") is None
    assert _acoes(servidor) == ["BecomeCoordinatorOfStandaloneGroup"] * 3
    assert "<InstanceID>0</InstanceID>" in _corpo_de(servidor, "BecomeCoordinatorOfStandaloneGroup")


async def test_tirar_um_membro_e_o_volume_dele_falam_no_endereco_do_membro(caixa):
    """This protocol has no move that expels a member, only one by which a member leaves, and
    the volume is of each box; both are spoken at the address of the member and not at ours,
    which is why the registration here points nowhere.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor, ip=IP_INALCANCAVEL)
        assert await driver.tirar_do_grupo("127.0.0.1") is None
        assert await driver.volume_de_escravo("127.0.0.1", 40) is None
    assert _acoes(servidor) == ["BecomeCoordinatorOfStandaloneGroup", "SetVolume"]
    assert "<DesiredVolume>40</DesiredVolume>" in _corpo_de(servidor, "SetVolume")


@pytest.mark.parametrize("valor", ["caixa.local", "http://10.0.0.9", "10.0.0.9:1400", 10, "", None])
async def test_um_grupo_so_e_formado_com_um_endereco_literal(caixa, valor):
    """Only an IP literal reaches a device, so the hub is never a resolver and never
    a proxy into the LAN of the client, not even for a box that is not the registered one.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.entrar_no_grupo(valor) == "invalid_value"
        assert await driver.tirar_do_grupo(valor) == "invalid_value"
        assert await driver.volume_de_escravo(valor, 30) == "invalid_value"
        assert await driver.volume_de_escravo("127.0.0.1", 300) == "invalid_value"
        assert servidor.pedidos == []


async def test_o_grupo_lido_e_chaveado_por_uuid_e_deixa_de_fora_o_que_nao_e_caixa(caixa):
    """The key of a member is its uid; a bridge, an invisible member and the
    satellites of a home theatre are not speakers anybody puts on a panel, and a member whose
    address is a name would make the hub reach whatever the speaker wrote.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        grupo = await driver.ler_grupo()
    assert grupo is not None
    assert grupo.escravos == (Escravo(UID_DO_QUARTO, IP_DO_QUARTO, "Quarto"),)


async def test_o_grupo_de_outro_coordenador_nao_e_nosso(caixa):
    """The topology of a house lists every group, and only the one this box coordinates is its
    own; reading another one would put speakers of a neighbouring group in our books.
    """
    rotas = _rotas(**{TOPOLOGIA: (200, _topologia(_zonas(coordenador=OUTRO_UID)))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        grupo = await driver.ler_grupo()
    assert grupo is not None
    assert grupo.escravos == ()


async def test_uma_topologia_ilegivel_nao_publica_grupo_vazio(caixa):
    """A topology this driver could not read is not an empty group, and publishing one would
    take down a group the customer is listening to right now.
    """
    quebrada = _envelope(
        "GetZoneGroupState", "ZoneGroupTopology", {"ZoneGroupState": "&lt;ZoneGroups"}
    )
    async with ServidorHttp(_rotas(**{TOPOLOGIA: (200, quebrada)})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.ler_grupo() is None
        servidor.rotas[TOPOLOGIA] = (500, _falha("501"))
        assert await driver.ler_grupo() is None


async def test_uma_lista_de_membros_sem_fim_tem_teto(caixa):
    """A speaker on the LAN must not be able to make the daemon hold what it likes."""
    membros = "".join(
        f'<ZoneGroupMember UUID="RINCON_{numero:016d}01400" ZoneName="Caixa {numero}" '
        'Location="http://10.0.0.30:1400/xml/device_description.xml"/>'
        for numero in range(500)
    )
    zonas = f'<ZoneGroups><ZoneGroup Coordinator="{UID}">{membros}</ZoneGroup></ZoneGroups>'
    async with ServidorHttp(_rotas(**{TOPOLOGIA: (200, _topologia(zonas))})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        grupo = await driver.ler_grupo()
    assert grupo is not None
    assert len(grupo.escravos) == sonos.ESCRAVOS_MAXIMO


@pytest.mark.parametrize("codigo", ["701", "711", "712"])
async def test_um_erro_de_rotina_do_protocolo_nao_e_uma_caixa_quebrada(caixa, codigo):
    """A next on a radio, a play with nothing loaded and a browse of an object that is gone all
    answer one of these, and none of them is a speaker with a fault.
    """
    async with ServidorHttp(_rotas(**{TRANSPORTE: (500, _falha(codigo))})) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("proxima") == "nao_suportado"
        assert await driver.executar("tocar") == "nao_suportado"


async def test_um_erro_que_nao_e_de_rotina_e_erro_do_aparelho(caixa):
    async with ServidorHttp(_rotas(**{TRANSPORTE: (500, _falha("800"))})) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("proxima") == "erro_aparelho"


async def test_a_interface_local_desligada_nao_e_uma_caixa_offline(caixa, caplog):
    """An HTTP 403 on every request is the local interface turned off in the application of the
    owner, which no command of the hub fixes: reading it as a network fault sends an integrator
    to change a cable, so the diary names it and the state says it is the device that refused.
    """
    with caplog.at_level(logging.WARNING, logger="iphub.drivers.nativos.sonos"):
        async with ServidorHttp(_rotas(**{DESCRICAO: (403, "")})) as servidor:
            driver = caixa(servidor)
            await driver.atualizar()
            await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho", "a box that answered is not a box offline"
    assert any("local interface is off" in registro.getMessage() for registro in caplog.records)


async def test_um_documento_com_entidade_nunca_chega_ao_analisador(caixa, monkeypatch):
    """Two answers of this speaker carry a whole document escaped inside another one, and the
    parser of the standard library reads a document type and an entity as instructions.
    """
    bomba = (
        '<?xml version="1.0"?><!DOCTYPE root [<!ENTITY x SYSTEM "file:///etc/passwd">]>'
        f'<root xmlns="urn:schemas-upnp-org:device-1-0"><device><UDN>uuid:{UID}</UDN>'
        "<modelName>&x;</modelName></device></root>"
    )
    async with ServidorHttp(_rotas(**{DESCRICAO: (200, bomba)})) as servidor:
        monkeypatch.setattr(sonos, "PORTA", servidor.endereco[1])
        assert await Sonos.identificar("127.0.0.1") is None
        driver = caixa()
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_uma_topologia_com_entidade_nao_e_lida(caixa):
    entidade = (
        '&lt;!DOCTYPE x [&lt;!ENTITY y SYSTEM "file:///etc/hosts"&gt;]&gt;&lt;ZoneGroups/&gt;'
    )
    quebrada = _envelope("GetZoneGroupState", "ZoneGroupTopology", {"ZoneGroupState": entidade})
    async with ServidorHttp(_rotas(**{TOPOLOGIA: (200, quebrada)})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.ler_grupo() is None


async def test_uma_caixa_que_so_responde_metade_do_poll_fica_offline(caixa):
    """A poll of this speaker is five exchanges, and a box that answers who it is and then goes
    quiet is not a box the panel may show as online: the count of failures belongs to the whole
    poll and never to the exchange that happened to be the last one to answer.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        del servidor.rotas[RENDERIZACAO]
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_dois_polls_falhos_deixam_offline_e_um_certo_traz_de_volta(caixa):
    """One lost poll is not a speaker that went away, two in a row is."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.rotas.clear()
        await driver.atualizar()
        assert driver.estado().online is True, "one failure must not blink the panel offline"
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "erro_aparelho"
        assert driver.identidade_do_aparelho() is None
        servidor.rotas.update(_rotas())
        await driver.atualizar()
    assert driver.estado().online is True
    assert driver.estado().detalhe == ""
    assert driver.identidade_do_aparelho() == UID


async def test_uma_caixa_que_nao_responde_e_eq_offline_e_nunca_uma_excecao(caixa):
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
    assert await driver.executar("volume", 30) == "eq_offline"
    assert await driver.executar("mudo", True) == "eq_offline"
    assert await driver.executar("atalho", RADIO) == "eq_offline"
    assert await driver.entrar_no_grupo("127.0.0.1") == "eq_offline"
    assert await driver.ler_grupo() is None
    await driver.atualizar()
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(caixa):
    """The hub only talks to an address somebody registered, never to a resolver default."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor, ip="")
        assert await driver.executar("volume", 30) == "eq_offline"
        assert await driver.executar("proxima") == "eq_offline"
        await driver.atualizar()
        assert servidor.pedidos == []
    assert driver.estado().online is False


async def test_uma_resposta_sem_os_campos_ou_gigante_nao_derruba_o_poll(caixa):
    """A speaker on the LAN answers what it likes, and the panel still reads a typed state."""
    vazia = _envelope("GetVolume", "RenderingControl", {})
    enorme = _envelope(
        "GetMediaInfo",
        "AVTransport",
        {"CurrentURI": "", "Lixo": "x" * 100_000, "CurrentTransportState": ""},
    )
    rotas = _rotas(**{RENDERIZACAO: (200, vazia), TRANSPORTE: (200, enorme)})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.volume, estado.mudo, estado.fonte, estado.reproduzindo) == (
        None,
        None,
        None,
        None,
    )


@pytest.mark.parametrize("bruto", ["abc", "", "1e3", "999999999999999999999999"])
async def test_um_volume_que_nao_e_um_numero_nao_vira_volume_zero(caixa, bruto):
    """A speaker that answers a word where a number belongs is not a volume of zero, and writing
    zero would tell the panel a speaker is silent while it plays.
    """
    rotas = _rotas(**{RENDERIZACAO: (200, _renderizacao(volume=bruto, mudo=bruto))})
    async with ServidorHttp(rotas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().volume is None
    assert driver.estado().mudo is None


async def test_um_corpo_sem_fim_nao_enche_a_memoria(caixa):
    """A speaker on the LAN must not be able to make the daemon buffer without bound."""
    enorme = "<root>" + "<lixo>x</lixo>" * 40_000 + "</root>"
    async with ServidorHttp(_rotas(**{DESCRICAO: (200, enorme)})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.identidade_do_aparelho() is None


async def test_a_saida_de_volume_fixo_e_dita_no_diario(caixa, caplog):
    """With a fixed line output a volume command is accepted and does nothing, and the speaker
    does not complain, so the only alternative to this line in the diary is an integrator
    chasing a volume bar that is dead by design.
    """
    rotas = _rotas(**{RENDERIZACAO: (200, _renderizacao(fixo="1"))})
    with caplog.at_level(logging.WARNING, logger="iphub.drivers.nativos.sonos"):
        async with ServidorHttp(rotas) as servidor:
            driver = caixa(servidor)
            await driver.atualizar()
            servidor.pedidos.clear()
            await driver.atualizar()
    assert driver.estado().online is True
    assert any("fixed line output" in registro.getMessage() for registro in caplog.records)
    # Asked once and never again: it is a fact of the model and not of the moment.
    assert "GetOutputFixed" not in _acoes(servidor)


@pytest.mark.parametrize(
    "acao", ["ligar", "desligar", "tecla", "modo", "temperatura", "comando_extra", "formatar"]
)
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(caixa, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar(acao, 50) == "nao_suportado"
        assert servidor.pedidos == []


async def test_a_caixa_nao_pareia_e_o_contrato_nao_pede_pareamento(caixa):
    """The base refuses the inherited autenticar only when the manifest declares an
    auth, and this protocol declares none: there is no credential, no code and no popup.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = caixa(servidor)
        assert await driver.autenticar() == "pareado"
        assert servidor.pedidos == []

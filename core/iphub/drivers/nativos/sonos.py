# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sonos multiroom speaker over the UPnP interface it answers on the local network.

One transport, TCP 1400: a plain GET on the description of the device for the identity, and
one SOAP POST per service for everything else. says a Sonos is always on, so this
driver declares no power at all, and Estado.ligado stays None for its whole life.

What the module that owns the blocks has to know, because each of these costs a service call
on a customer site to find out:

- the identity is the uid of the description (RINCON_...) and never the address, so
identidade_do_aparelho is what says the box answering at this ip is still the registered one;
- the transport belongs to the COORDINATOR and the volume belongs to EACH box, which is the
opposite half of the multiroom driver of the same category in this image: a slave here is
refused play, pause, stop, track, input and shortcut, and is still given volume and mute,
because a group whose members have no volume bar is a group nobody can balance;
- the mark of a slave is the x-rincon: of the CurrentURI of GetMediaInfo, never the TrackURI
of GetPositionInfo, and while it is a slave the whole media read is skipped: a slave answers
the LAST track it played by itself, so what it says about itself is stale by design;
- a slave reads what the master plays through espelhar, for the same reason;
- TRANSITIONING is not a stop, it is the gap between two tracks, and reading it as a stop makes
the application send a play to a speaker that is already playing;
- PAUSED_PLAYBACK with no media loaded is a normal state of this speaker and not a defect;
- the UPnP errors 701, 711 and 712 are routine (a Next on a radio answers 701), so they come
back as nao_suportado and never as erro_aparelho;
- an HTTP 403 on every request means the owner turned the local interface off in the
application of the manufacturer; there is no handshake that fixes it from here and no screen
of the panel reads a fault of a driver by name, so the diary is what names it.

Details of the wire that look optional and are not: the content type carries the quotes, the
SOAPACTION goes without quotes around its value, the content directory only answers well with
an agent of its own family in the request, and the Speed of a pause, a stop, a next and a
previous is not in the specification and is what these boxes are known to take.
"""

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlsplit
from xml.etree import ElementTree

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import (
    VALOR_DE_LISTA_MAXIMO,
    Descoberta,
    Manifesto,
    Sugestao,
)

log = logging.getLogger("iphub.drivers.nativos.sonos")

# The actions of the poll, written once in the transcript and not every ten seconds.
PERGUNTAS_DE_ROTINA = (
    "GetMediaInfo",
    "GetMute",
    "GetOutputFixed",
    "GetPositionInfo",
    "GetTransportInfo",
    "GetVolume",
    "GetZoneGroupState",
)

TIPO = "multiroom_sonos"

# This protocol fixes the port, and the description, every control and the discovery all
# name the same one, so a registration has nothing to choose and no field to get wrong.
PORTA = 1400

TEMPO_LIMITE_S = 4.0

# The topology of a house and a page of favourites are the two big answers of this
# speaker, and a device on the LAN still must never size the memory of this daemon.
CORPO_MAXIMO = 256 * 1024
TEXTO_MAXIMO = 120
ENDERECO_MAXIMO = 2048
METADADO_MAXIMO = 8 * 1024
ESCRAVOS_MAXIMO = 12

# One lost poll is not a speaker that went away; two in a row is.
FALHAS_ATE_OFFLINE = 2

# A speaker out of the slave mode for two polls in a row lost its group to a reboot or to
# the application of the owner, and the logical state has to be reconciled.
POLLS_ATE_RECONCILIAR = 2

CAMINHO_DESCRICAO = "/xml/device_description.xml"


@dataclass(frozen=True)
class Servico:
    """One service of the speaker: the name that goes in the action and the control it takes."""

    nome: str
    caminho: str


RENDERIZACAO = Servico("RenderingControl", "/MediaRenderer/RenderingControl/Control")
TRANSPORTE = Servico("AVTransport", "/MediaRenderer/AVTransport/Control")
CONTEUDO = Servico("ContentDirectory", "/MediaServer/ContentDirectory/Control")
TOPOLOGIA = Servico("ZoneGroupTopology", "/ZoneGroupTopology/Control")

ENVELOPE = (
    '<?xml version="1.0"?>'
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"'
    ' s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/">'
    '<s:Body><u:{acao} xmlns:u="urn:schemas-upnp-org:service:{servico}:1">'
    "{argumentos}</u:{acao}></s:Body></s:Envelope>"
)
ACAO_SOAP = "urn:schemas-upnp-org:service:{servico}:1#{acao}"
TIPO_DO_CORPO = 'text/xml; charset="utf-8"'

# The content directory of this speaker answers a browse well only when the request
# carries an agent of its own family, which is the one header that is not the same everywhere.
AGENTE = "Sonos/83.1-61210"

NS_ENVELOPE = "http://schemas.xmlsoap.org/soap/envelope/"
NS_CONTROLE = "urn:schemas-upnp-org:control-1-0"

ARG_INSTANCIA = (("InstanceID", "0"),)
ARG_CANAL = (("InstanceID", "0"), ("Channel", "Master"))
# The Speed of a pause, a stop, a next and a previous is not in the specification of the
# service and is what these boxes are known to take, so it is written on all five.
ARG_VELOCIDADE = (("InstanceID", "0"), ("Speed", "1"))

# The favourites of the house, which is the only list of this speaker a shortcut reads.
OBJETO_FAVORITOS = "FV:2"
FAVORITOS_MAXIMO = 100
ARG_FAVORITOS = (
    ("ObjectID", OBJETO_FAVORITOS),
    ("BrowseFlag", "BrowseDirectChildren"),
    ("Filter", "*"),
    ("StartingIndex", "0"),
    ("RequestedCount", str(FAVORITOS_MAXIMO)),
    ("SortCriteria", ""),
)

PEDE_VOLUME = "GetVolume"
PEDE_MUDO = "GetMute"
PEDE_MIDIA = "GetMediaInfo"
PEDE_TRANSPORTE = "GetTransportInfo"
PEDE_POSICAO = "GetPositionInfo"
PEDE_SAIDA_FIXA = "GetOutputFixed"
PEDE_TOPOLOGIA = "GetZoneGroupState"
CAMPO_TOPOLOGIA = "ZoneGroupState"
PEDE_FAVORITOS = "Browse"

MANDA_VOLUME = "SetVolume"
MANDA_MUDO = "SetMute"
MANDA_URI = "SetAVTransportURI"
MANDA_TOCAR = "Play"
SOZINHA = "BecomeCoordinatorOfStandaloneGroup"

ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_PROXIMA = "proxima"
ACAO_ANTERIOR = "anterior"
ACAO_AGRUPAR = "agrupar"
ACAO_ATALHO = "atalho"

# The transport of the contract in the actions of this service.
TRANSPORTES = {
    ACAO_TOCAR: MANDA_TOCAR,
    ACAO_PAUSAR: "Pause",
    ACAO_PARAR: "Stop",
    ACAO_PROXIMA: "Next",
    ACAO_ANTERIOR: "Previous",
}

# On this speaker the transport and what is loaded belong to the coordinator, so each of
# these on a slave either fails or takes the group down; the volume and the mute are NOT here,
# because each box of a group keeps its own, which is what makes a group balanceable.
ACOES_DO_MESTRE = (*TRANSPORTES, ACAO_FONTE, ACAO_ATALHO)

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

# A code of its own would be a phrase the panel cannot translate, and of the
# five stable codes this is the one that says the speaker cannot do it as it stands: in a group
# what it plays is the master's, and so is what it loads.
RECUSA_DE_GRUPO = "nao_suportado"
NAO_SUPORTADO = "nao_suportado"

PROIBIDO = 403

# A Next on a radio, a Play with nothing loaded and a browse of an object that is gone all
# answer one of these, and none of them is a speaker with a fault; they are the equipment saying
# it cannot do that right now, which is exactly what nao_suportado says on this bus.
ERROS_DE_ROTINA = ("701", "711", "712")

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100

TOCANDO = ("PLAYING", "TRANSITIONING")
TRANSICAO = "TRANSITIONING"

FONTE_LINHA = "line_in"
FONTE_TV = "tv"

# On a line input and on the TV this speaker answers the metadata of the last track of the
# network it played, so a title read there names something it is not playing.
ENTRADAS_SEM_TITULO = (FONTE_LINHA, FONTE_TV)

# The uri of an input is made of the uid of the speaker ITSELF: the same string with the uid of
# another box plays the line input of that other box instead.
URIS_DE_ENTRADA = {
    FONTE_LINHA: "x-rincon-stream:{uid}",
    FONTE_TV: "x-sonos-htastream:{uid}:spdif",
}
URI_DE_MESTRE = "x-rincon:{uid}"
MARCA_DE_ESCRAVO = "x-rincon:"

# Which inputs a model is known to carry, read from the model name of the description: a fixed
# table of models ages with every launch, so it only SUGGESTS what the panel offers, and the
# list of the registration is what decides; what is refused is a word with no address above.
MODELOS_DE_LINHA = ("CONNECT", "CONNECT:AMP", "PORT", "PLAY:5")
MODELOS_DE_TV = ("ARC", "BEAM", "PLAYBAR", "PLAYBASE", "ULTRA")
MODELOS_DOS_DOIS = ("AMP",)

# What the address of a track says about where the audio comes from; the value is a driver
# value, which the entradas list of the registration maps to a label of the app.
FONTES_POR_PREFIXO = (
    ("x-rincon-stream:", FONTE_LINHA),
    ("x-sonos-htastream:", FONTE_TV),
    ("x-rincon-mp3radio:", "radio"),
    ("x-sonosapi-stream:", "radio"),
    ("x-sonosapi-radio:", "radio"),
    ("x-sonosapi-hls:", "radio"),
    ("x-sonos-http:sonos", "radio"),
    ("hls-radio:", "radio"),
    ("aac:", "radio"),
    ("x-file-cifs:", "biblioteca"),
    ("https://", "web"),
    ("http://", "web"),
)
PREFIXO_VIRTUAL = "x-sonos-vli:"
FONTES_VIRTUAIS = ((",airplay:", "airplay"), (",spotify:", "spotify"))

# A favourite of a track or of a playlist is a queue to clear, fill, point at and seek,
# which is four more exchanges and a queue to keep; a favourite of a radio or of a line input
# is one load and one play, so those are the ones this driver takes.
PREFIXOS_DIRETOS = (
    "x-sonosapi-stream:",
    "x-sonosapi-radio:",
    "x-rincon-mp3radio:",
    "hls-radio:",
    "aac:",
    "x-rincon-stream:",
)

# The identity of this speaker, which is the uid of its description with the uuid: cut off.
PREFIXO_UUID = "uuid:"
_UID = re.compile(r"RINCON_[0-9A-Za-z]{4,40}")

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
_NUMERO = re.compile(r"-?[0-9]{1,10}")

# Two answers of this speaker carry a whole XML document escaped inside another one, and
# the parser of the standard library reads a document type and an entity as instructions. A
# device on the LAN of a customer never gets to hand this daemon either of them.
_PERIGO = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)", re.IGNORECASE)

# The value of a shortcut is the TITLE of a favourite of the house and never its position,
# because the position walks the moment the owner reorders the favourites in the application; the
# ceiling is the one of a value of a list, because a longer title never fits a registration.
TITULO_MAXIMO = VALOR_DE_LISTA_MAXIMO

# The inputs of a Sonos are two words nobody guesses in the shape the driver takes them,
# and the favourites are NOT suggested: they are the ones of the house and not of the driver.
SUGESTOES = (
    Sugestao("entradas", "Line-in", FONTE_LINHA),
    Sugestao("entradas", "TV", FONTE_TV),
)

TEXTOS = {
    "en": {
        "descricao": (
            "Sonos multiroom speaker over the interface it answers on the local network. "
            "Always on, so it declares no power: volume, mute, line input and TV, play, pause, "
            "stop, track, the favourites of the house and native grouping."
        ),
        "preparo": (
            "In the Sonos app keep the local interface on (Settings, System, Network): with it off "
            "the speaker answers 403 to everything. Speaker and hub on the same network, address "
            "reserved on the router."
        ),
        "cap_fonte": (
            "The two inputs of this speaker are line_in and tv; the model says which of them it "
            "is known to carry and the list of the registration decides. The input is refused "
            "while the speaker is in a group, because what is loaded belongs to the master."
        ),
        "cap_tocar": (
            "Play resumes what is loaded in the speaker and takes no value: what to load is an "
            "input or a shortcut. In a group it belongs to the master."
        ),
        "cap_atalho": (
            "A shortcut is a favourite of the house, written as its title exactly as the "
            "application shows it. A favourite of a radio or of a line input plays; one of a "
            "track or of a playlist is a queue and is refused."
        ),
        "cap_agrupar": (
            "Grouping takes the address of the master to join, and with no value it leaves the "
            "group. Only speakers of the same kind share a group."
        ),
        "lista_entradas": "line_in is the line input and tv the one of the home theatre models.",
        "lista_atalhos": (
            "The title of a favourite of the house, exactly as the application shows it, of a "
            "radio or of a line input."
        ),
        "upnp_desligado": (
            "If every command answers a device error, the local interface of the speaker is "
            "off: turn it back on in the application of the manufacturer. No setting of the hub "
            "replaces it."
        ),
    },
    "pt": {
        "descricao": (
            "Caixa multiroom Sonos pela interface que ela responde na rede local. Sempre ligada, "
            "então não declara energia: volume, mudo, entrada de linha e TV, tocar, pausar, "
            "parar, faixa, os favoritos da casa e agrupamento nativo."
        ),
        "preparo": (
            "No aplicativo Sonos mantenha a interface local ligada (Configurações, Sistema, Rede): "
            "com ela desligada a caixa responde 403 a tudo. Caixa e hub na mesma rede, endereço "
            "reservado no roteador."
        ),
        "cap_fonte": (
            "As duas entradas desta caixa são line_in e tv; o modelo diz quais delas ela é "
            "conhecida por ter e a lista do cadastro decide. A entrada é recusada enquanto a "
            "caixa está num grupo, porque o que está carregado é do mestre."
        ),
        "cap_tocar": (
            "Tocar retoma o que está carregado na caixa e não recebe valor: o que carregar é uma "
            "entrada ou um atalho. Num grupo ele é do mestre."
        ),
        "cap_atalho": (
            "Um atalho é um favorito da casa, escrito com o título dele exatamente como o "
            "aplicativo mostra. Um favorito de rádio ou de entrada de linha toca; um de faixa ou "
            "de lista é uma fila e é recusado."
        ),
        "cap_agrupar": (
            "Agrupar recebe o endereço do mestre em que entrar, e sem valor sai do grupo. Só "
            "caixas do mesmo tipo dividem um grupo."
        ),
        "lista_entradas": "line_in é a entrada de linha e tv a dos modelos de home theater.",
        "lista_atalhos": (
            "O título de um favorito da casa, exatamente como o aplicativo o mostra, de uma "
            "rádio ou de uma entrada de linha."
        ),
        "upnp_desligado": (
            "Se todo comando responde erro de aparelho, a interface local da caixa está "
            "desligada: religue-a no aplicativo do fabricante. Nenhum ajuste do hub substitui "
            "isso."
        ),
    },
}


@dataclass(frozen=True)
class Cartao:
    """What the description of the speaker says about who it is and what it carries."""

    uid: str
    modelo: str = ""


@dataclass(frozen=True)
class Escravo:
    """One member of a group as the topology lists it: the uid is the key, the ip is today's."""

    identidade: str
    ip: str
    nome: str = ""


@dataclass(frozen=True)
class Grupo:
    """The group the speaker really leads, read from the speaker and not from our own books."""

    escravos: tuple[Escravo, ...] = ()


class _Falha(Exception):
    """A stable code on the way out of an exchange, so no exception escapes executar."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Sonos(Driver):
    """Volume, mute, inputs, transport, the favourites of the house and native grouping of one
    Sonos speaker.
    """

    # This speaker has no power at all, so the pair of capabilities is OMITTED
    # instead of implemented to refuse, and the power data point stays quiet.
    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Multiroom Sonos", "en": "Sonos multiroom"},
        categoria="multiroom",
        capacidades=(
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TOCAR,
            ACAO_PAUSAR,
            ACAO_PARAR,
            ACAO_PROXIMA,
            ACAO_ANTERIOR,
            ACAO_AGRUPAR,
            ACAO_ATALHO,
        ),
        descoberta=Descoberta(ssdp_st=("urn:schemas-upnp-org:device:ZonePlayer:1",)),
        textos=TEXTOS,
        marca="Sonos",
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._identidade: str | None = None
        self._fontes: tuple[str, ...] = ()
        self._falhas = 0
        self._escravo = False
        self._polls_fora = 0
        self._saiu_do_grupo = False
        # A box that just joined a master is a member of a group a whole poll before it
        # says so, and in that window a command of the master would dissolve the group.
        self._no_grupo = False
        self._espelho: str | None = None
        self._espelho_reproduzindo: bool | None = None
        # A radio names nothing of itself until the station sends metadata, which many
        # never do, and the hub asked for that stream by a favourite the owner named; an empty
        # "now playing" over a speaker that is audibly playing is the panel calling itself broken.
        self._pedido: str | None = None
        self._fonte_pedida: str | None = None
        self._saida_conferida = False

    async def iniciar(self) -> None:
        await self._abrir()

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The uid the speaker at that address answers, for a finding of the sweep."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        url = f"http://{_hospedeiro(endereco)}:{PORTA}{CAMINHO_DESCRICAO}"
        try:
            async with ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao:
                async with sessao.get(url, allow_redirects=False) as resposta:
                    bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                    if resposta.status >= 400:
                        return None
        except (TimeoutError, ClientError, OSError, ValueError):
            return None
        cartao = _cartao(bruto)
        return None if cartao is None else cartao.uid

    async def atualizar(self) -> None:
        try:
            await self._conferir_identidade()
            await self._ler_estado()
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        # A poll of this speaker is five exchanges, and clearing the count in the middle of
        # them would let a box that fails a different one of the five each time stay online for
        # ever, which is the box an integrator is called about.
        self._falhas = 0

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    def identidade_do_aparelho(self) -> str | None:
        """The uid the speaker answered, which is what says the box here is still ours."""
        return self._identidade

    def e_escravo(self) -> bool:
        """The last poll saw the speaker following a coordinator."""
        return self._escravo

    def saiu_do_grupo(self) -> bool:
        """It followed a coordinator and stopped doing so for two polls: the group dissolved."""
        return self._saiu_do_grupo

    def no_grupo(self) -> bool:
        """The speaker is a member of a group, either because a poll saw it following a
        coordinator or because it has just joined one.
        """
        return self._escravo or self._no_grupo

    def marcar_grupo(self, dentro: bool) -> None:
        """Where the owner of the group logic says this speaker stands right now."""
        # Whoever owns the group logic has just declared where this speaker stands, and a
        # verdict left over from an earlier group would otherwise make the reconcile tear down
        # the group that owner formed one moment ago. Being inside a group is NOT taken from
        # here: the master of a group is marked with this same word, and on this speaker the
        # master is exactly the box that loads an input and starts the audio of everybody.
        self._saiu_do_grupo = False
        if not dentro:
            self._no_grupo = False
            self._espelho = None
            self._espelho_reproduzindo = None

    def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
        """A slave answers the last track it played by itself, so the transport of the master is
        pinned here by whoever owns the group.
        """
        self._espelho = None if tocando is None else _texto(tocando)
        self._espelho_reproduzindo = reproduzindo

    async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
        """Run on the SLAVE: it joins the master at that address."""
        endereco = ip_literal(ip_do_mestre)
        if endereco is None:
            return INVALID_VALUE
        try:
            # The invite is made of the uid of the master and the contract
            # hands an address, which is only where that master answered today; asking the
            # master who it is, right now, is what keeps a stale uid from forming a group with
            # whoever holds that address.
            cartao = await self._descricao(endereco)
            # A box that already leads a group of its own carries that group into the new
            # one and its members inherit a queue nobody asked for, so it stands alone first;
            # on a box that is already alone this is the move that does nothing.
            await self._falar(TRANSPORTE, SOZINHA, ARG_INSTANCIA)
            await self._carregar(URI_DE_MESTRE.format(uid=cartao.uid), "")
        except _Falha as falha:
            return falha.codigo
        # From here on this box is a member, and the mark of a slave it answers is a whole
        # poll away; a command of the master in that window makes it the coordinator of itself
        # and the group falls apart while the books of the hub still say it is a member.
        self._no_grupo = True
        return None

    async def desfazer_grupo(self) -> str | None:
        """Run on the MASTER: it stands alone again, which is what dismantles the group."""
        try:
            await self._falar(TRANSPORTE, SOZINHA, ARG_INSTANCIA)
        except _Falha as falha:
            return falha.codigo
        # This is also the move by which a member leaves, so the box is standing alone now.
        self._no_grupo = False
        return None

    async def tirar_do_grupo(self, ip_do_escravo: object) -> str | None:
        """Run on the MASTER, spoken to the SLAVE: this protocol has no move that expels a
        member, only one by which a member leaves, so the master says it at the address of the
        member and the rest of the group goes on playing.
        """
        endereco = ip_literal(ip_do_escravo)
        if endereco is None:
            return INVALID_VALUE
        try:
            await self._falar(TRANSPORTE, SOZINHA, ARG_INSTANCIA, endereco=endereco)
        except _Falha as falha:
            return falha.codigo
        return None

    async def volume_de_escravo(self, ip_do_escravo: object, valor: object) -> str | None:
        """Run on the MASTER, spoken to the SLAVE: on this speaker the volume is of each box,
        and there is no proxy through the master to go around.
        """
        endereco = ip_literal(ip_do_escravo)
        if endereco is None or not _volume_valido(valor):
            return INVALID_VALUE
        try:
            await self._falar(
                RENDERIZACAO, MANDA_VOLUME, _com_volume(int(valor)), endereco=endereco
            )
        except _Falha as falha:
            return falha.codigo
        return None

    async def ler_grupo(self) -> Grupo | None:
        """Run on the MASTER: the group the topology says this speaker coordinates, or None when
        it could not be read. the key of a member is its uid, never its address.
        """
        try:
            if self._identidade is None:
                await self._conferir_identidade()
            lido = await self._falar(TOPOLOGIA, PEDE_TOPOLOGIA)
        except _Falha as falha:
            log.warning("speaker %s did not list its group: %s", self.cadastro.identidade, falha)
            return None
        uid = self._identidade
        if uid is None:
            return None
        # A topology this driver could not read is not an empty group, and publishing one
        # would take down a group the customer is listening to right now.
        grupo = _grupo_de(lido.get(CAMPO_TOPOLOGIA, ""), uid)
        if grupo is None:
            log.warning("speaker %s answered a topology this driver cannot read", uid)
        return grupo

    async def _agir(self, acao: str, valor: object) -> str | None:
        # On this speaker what plays and what is loaded are of the coordinator, so each of
        # these on a member either fails or takes the group down; the volume and the mute are
        # deliberately absent from that list, because each box of a group keeps its own. The
        # question is no_grupo and not the mark of the poll: the input is the one action the
        # command channel routes straight to a member, and it would arrive in the
        # ten seconds between joining a group and the first poll that sees it.
        if acao in ACOES_DO_MESTRE and self.no_grupo():
            return RECUSA_DE_GRUPO
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._trocar_fonte(valor)
        if acao in TRANSPORTES:
            return await self._transportar(acao, valor)
        if acao == ACAO_AGRUPAR:
            return await self._agrupar(valor)
        if acao == ACAO_ATALHO:
            return await self._atalho(valor)
        return await super().executar(acao, valor)

    async def _trocar_volume(self, valor: object) -> str | None:
        if not _volume_valido(valor):
            return INVALID_VALUE
        pedido = int(valor)
        # Fixes the scale at 0 to 100 and this speaker speaks the same one, so
        # there is nothing to convert and the constant is what keeps that visible.
        await self._falar(RENDERIZACAO, MANDA_VOLUME, _com_volume(pedido))
        self._defina(volume=pedido)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        if not isinstance(valor, bool):
            return INVALID_VALUE
        argumentos = (*ARG_CANAL, ("DesiredMute", str(int(valor))))
        await self._falar(RENDERIZACAO, MANDA_MUDO, argumentos)
        self._defina(mudo=valor)
        return None

    async def _trocar_fonte(self, valor: object) -> str | None:
        """The line input and the TV of THIS box, which is an address made of its own uid.

        What is accepted is the vocabulary of this driver and not the table of models,
        which only suggests: a table refusing an input the integrator registered is a button of
        the panel that answers invalid for ever, on every model launched after this line.
        """
        if not isinstance(valor, str) or valor not in URIS_DE_ENTRADA:
            return INVALID_VALUE
        uid = self._identidade
        if uid is None:
            # The address of an input is made of the uid of the speaker, so a box that has
            # not said who it is has no input to load; commanding one would load the input of
            # whatever box the last uid named.
            return EQ_OFFLINE
        await self._carregar(URIS_DE_ENTRADA[valor].format(uid=uid), "")
        await self._falar(TRANSPORTE, MANDA_TOCAR, ARG_VELOCIDADE)
        self._esquecer_pedido()
        self._defina(fonte=valor, reproduzindo=True, tocando=None)
        return None

    async def _transportar(self, acao: str, valor: object) -> str | None:
        # This speaker plays what is loaded in it, and what to load is an input or a
        # shortcut; a value here would be an address the driver would have to invent a meaning
        # for, and a play that silently ignored it would play something else than what was asked.
        if acao == ACAO_TOCAR and valor is not None and valor != "":
            return INVALID_VALUE
        await self._falar(TRANSPORTE, TRANSPORTES[acao], ARG_VELOCIDADE)
        if acao == ACAO_TOCAR:
            self._defina(reproduzindo=True)
        if acao in (ACAO_PAUSAR, ACAO_PARAR):
            self._esquecer_pedido()
            self._defina(reproduzindo=False, tocando=None)
        return None

    async def _agrupar(self, valor: object) -> str | None:
        if valor is None or valor == "":
            return await self.desfazer_grupo()
        return await self.entrar_no_grupo(valor)

    async def _atalho(self, valor: object) -> str | None:
        """A favourite of the house, named by its title exactly as the application shows it."""
        titulo = _titulo_pedido(valor)
        if titulo is None:
            return INVALID_VALUE
        achado = await self._favorito(titulo)
        if achado is None:
            # A favourite the owner removed or renamed is a registration that names
            # something the house no longer has, which the panel fixes by editing the list.
            log.warning("no favourite of this speaker is titled %r", titulo[:TEXTO_MAXIMO])
            return INVALID_VALUE
        endereco, metadado = achado
        if not endereco.startswith(PREFIXOS_DIRETOS):
            log.warning("the favourite %r is a queue and not a stream", titulo[:TEXTO_MAXIMO])
            return NAO_SUPORTADO
        await self._carregar(endereco, metadado)
        await self._falar(TRANSPORTE, MANDA_TOCAR, ARG_VELOCIDADE)
        self._pedido = titulo
        # The name of the favourite stands in only while the speaker plays what was asked,
        # and the source of the address is what says so at the next poll; a radio keeps its name
        # through a station that renames the stream, and a line input pressed on the box drops
        # it, which is the only way the title stops naming what nobody is listening to.
        self._fonte_pedida = _fonte_de(endereco)
        self._defina(reproduzindo=True, tocando=titulo)
        return None

    async def _favorito(self, titulo: str) -> tuple[str, str] | None:
        lido = await self._falar(CONTEUDO, PEDE_FAVORITOS, ARG_FAVORITOS)
        return _achar_favorito(lido.get("Result", ""), titulo)

    async def _carregar(self, endereco: str, metadado: str) -> None:
        """What the speaker is to play next, with the metadata of the object it came from."""
        argumentos = (
            ("InstanceID", "0"),
            ("CurrentURI", endereco),
            ("CurrentURIMetaData", metadado),
        )
        await self._falar(TRANSPORTE, MANDA_URI, argumentos)

    async def _conferir_identidade(self) -> None:
        """The identity is the uid and the address is only where it answered today.

        Asking once and never again leaves a lease that moved to another box with this hub
        commanding whatever now holds the address, under the name of this block, for as long as
        the daemon runs. The description is one small document and the price of never commanding
        the speaker of a neighbour.
        """
        cartao = await self._descricao(self.cadastro.ip)
        if self._identidade and cartao.uid != self._identidade:
            raise _Falha(EQ_OFFLINE)
        self._identidade = cartao.uid
        self._fontes = _fontes_do_modelo(cartao.modelo)
        await self._conferir_saida_fixa()

    async def _conferir_saida_fixa(self) -> None:
        """Says in the diary when the line output of this box has a fixed volume.

        With it on, a volume command is accepted and does nothing, and the speaker does not
        complain, so the only alternative to this line is an integrator chasing a volume bar that
        is dead by design.
        """
        if self._saida_conferida:
            return
        self._saida_conferida = True
        try:
            lido = await self._falar(RENDERIZACAO, PEDE_SAIDA_FIXA, ARG_INSTANCIA)
        except _Falha:
            # A model with no line output answers a fault to this, which is not news.
            return
        if _verdade(lido.get("CurrentFixed")):
            log.warning(
                "speaker %s has a fixed line output: it accepts a volume and does not change it",
                self._identidade,
            )

    async def _ler_estado(self) -> None:
        """One poll: the volume and the mute of this box, then who it follows, then what plays."""
        volume = _do_aparelho(
            (await self._falar(RENDERIZACAO, PEDE_VOLUME, ARG_CANAL)).get("CurrentVolume")
        )
        mudo = _verdade((await self._falar(RENDERIZACAO, PEDE_MUDO, ARG_CANAL)).get("CurrentMute"))
        midia = await self._falar(TRANSPORTE, PEDE_MIDIA, ARG_INSTANCIA)
        self._marcar_escravo(_mestre_de(midia.get("CurrentURI")) is not None)
        if self._escravo:
            # A slave answers the last track it played by itself, so reading its media here
            # would publish an old title and a transport that has nothing to do with the group.
            self._defina(
                online=True,
                volume=volume,
                mudo=mudo,
                fonte=None,
                fontes=self._fontes,
                reproduzindo=self._espelho_reproduzindo,
                tocando=self._espelho,
                detalhe="",
            )
            return
        situacao = _texto(
            (await self._falar(TRANSPORTE, PEDE_TRANSPORTE, ARG_INSTANCIA)).get(
                "CurrentTransportState"
            )
        ).upper()
        if situacao == TRANSICAO:
            # Between two tracks this speaker answers the media of neither of them, so the
            # last one read stands and only the transport is published; it is playing.
            self._defina(
                online=True,
                volume=volume,
                mudo=mudo,
                fontes=self._fontes,
                reproduzindo=True,
                detalhe="",
            )
            return
        posicao = await self._falar(TRANSPORTE, PEDE_POSICAO, ARG_CANAL)
        endereco = _texto(posicao.get("TrackURI"))
        fonte = _fonte_de(endereco)
        # The owner presses the TV button on the box and the favourite the hub asked for is
        # over; a source this driver can read that is not the one asked for says the request is
        # gone, and a source it cannot read is no reason to drop a name that may still be right.
        if fonte is not None and fonte != self._fonte_pedida:
            self._esquecer_pedido()
        reproduzindo = None if not situacao else situacao in TOCANDO
        self._defina(
            online=True,
            volume=volume,
            mudo=mudo,
            fonte=fonte,
            fontes=self._fontes,
            reproduzindo=reproduzindo,
            tocando=self._tocando(posicao.get("TrackMetaData"), reproduzindo, fonte),
            detalhe="",
        )

    def _tocando(
        self, metadado: object, reproduzindo: bool | None, fonte: str | None
    ) -> str | None:
        """The title of what is playing right now, and nothing else."""
        # The transport and the title are different facts; a speaker that is not
        # playing carries a title of what it played before, and the DP of the title reads this.
        if not reproduzindo:
            return None
        if fonte in ENTRADAS_SEM_TITULO:
            # The metadata here is the one of the last track of the network, so what is
            # left is the name the owner gave the input, when the hub is the one that loaded it.
            return self._pedido
        # The name of the favourite only stands in while the station names nothing itself,
        # and a station that starts sending metadata takes the line over on the next poll.
        return _titulo_do_metadado(metadado) or self._pedido

    def _esquecer_pedido(self) -> None:
        """The favourite the hub asked for is over, and so is the name that stood in for it."""
        self._pedido = None
        self._fonte_pedida = None

    def _marcar_escravo(self, escravo: bool) -> None:
        if escravo:
            self._escravo = True
            self._polls_fora = 0
            self._saiu_do_grupo = False
            return
        if not self._escravo:
            return
        self._polls_fora += 1
        if self._polls_fora < POLLS_ATE_RECONCILIAR:
            return
        self._escravo = False
        self._espelho = None
        self._espelho_reproduzindo = None
        self._saiu_do_grupo = True

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        # A poll that failed is the first line of every diagnosis of a speaker that "does
        # nothing", and it is the one thing the diary must never be quiet about.
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        # While the speaker was away the lease may have handed its address to another box,
        # so the identity is asked again before this hub commands whatever answers there.
        self._identidade = None
        self._defina(online=False, tocando=None, detalhe=codigo)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    async def _descricao(self, ip: object) -> Cartao:
        """The identity card of the speaker at that address, asked with no SOAP at all."""
        endereco = ip_literal(ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        url = f"http://{_hospedeiro(endereco)}:{PORTA}{CAMINHO_DESCRICAO}"
        sessao = await self._abrir()
        try:
            async with sessao.get(
                url,
                # A speaker answering a redirect would send the hub to whatever host it
                # names, which is the LAN proxy refuses.
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            raise _Falha(EQ_OFFLINE) from erro
        if estado >= 400:
            self._avisar(estado, CAMINHO_DESCRICAO)
            raise _Falha(ERRO_APARELHO)
        cartao = _cartao(bruto)
        if cartao is None:
            log.warning("the speaker at %s answered a description with no uid", endereco)
            raise _Falha(ERRO_APARELHO)
        return cartao

    async def _falar(
        self,
        servico: Servico,
        acao: str,
        argumentos: tuple[tuple[str, str], ...] = (),
        *,
        endereco: object = None,
    ) -> dict[str, str]:
        """One exchange with a service of the speaker, answered as the fields of its action."""
        alvo = ip_literal(self.cadastro.ip if endereco is None else endereco)
        if alvo is None:
            raise _Falha(EQ_OFFLINE)
        url = f"http://{_hospedeiro(alvo)}:{PORTA}{servico.caminho}"
        cabecalhos = {
            "Content-Type": TIPO_DO_CORPO,
            "SOAPACTION": ACAO_SOAP.format(servico=servico.nome, acao=acao),
        }
        if servico is CONTEUDO:
            cabecalhos["USER-AGENT"] = AGENTE
        pedido = ENVELOPE.format(
            acao=acao, servico=servico.nome, argumentos=_argumentos(argumentos)
        )
        # The transcript names the action and its arguments and not the envelope, which
        # is the same forty lines of SOAP around every one of them.
        rotina = acao in PERGUNTAS_DE_ROTINA
        transcricao = self._transcricao()
        transcricao.enviado(f"{servico.nome}.{acao} {dict(argumentos)}", rotina=rotina)
        sessao = await self._abrir()
        try:
            async with sessao.post(
                url,
                data=pedido.encode("utf-8"),
                headers=cabecalhos,
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            transcricao.falhou(f"{servico.nome}.{acao}", erro)
            raise _Falha(EQ_OFFLINE) from erro
        transcricao.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        return self._ler_resposta(acao, estado, bruto)

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self.cadastro.identidade or self.cadastro.ip)
            self._fio = transcricao
        return transcricao

    def _ler_resposta(self, acao: str, estado: int, bruto: bytes) -> dict[str, str]:
        raiz = _arvore(bruto)
        if estado >= 400:
            codigo = _erro_upnp(raiz)
            if codigo in ERROS_DE_ROTINA:
                log.debug("%s answered the routine error %s to %s", self._identidade, codigo, acao)
                raise _Falha(NAO_SUPORTADO)
            self._avisar(estado, acao, codigo)
            raise _Falha(ERRO_APARELHO)
        if raiz is None:
            log.warning("the speaker answered %s with something that is not an envelope", acao)
            raise _Falha(ERRO_APARELHO)
        return _valores(raiz)

    def _avisar(self, estado: int, acao: str, codigo: str = "") -> None:
        if estado == PROIBIDO:
            # This is the local interface turned off in the application of the owner, and
            # there is no handshake here that turns it back on; naming it is what keeps an
            # integrator from changing a cable to chase a network fault that does not exist.
            log.warning(
                "speaker %s refused %s: the local interface is off in the application of its owner",
                self.cadastro.identidade,
                acao,
            )
            return
        log.warning("the speaker answered HTTP %d (%s) to %s", estado, codigo or "-", acao)


def _argumentos(argumentos: tuple[tuple[str, str], ...]) -> str:
    return "".join(f"<{nome}>{_escapar(valor)}</{nome}>" for nome, valor in argumentos)


def _escapar(valor: str) -> str:
    """A value inside an element of the envelope, including a whole document of the speaker."""
    limpo = _CONTROLE.sub("", valor)
    return (
        limpo.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")
    )


def _com_volume(valor: int) -> tuple[tuple[str, str], ...]:
    return (*ARG_CANAL, ("DesiredVolume", str(valor)))


def _arvore(bruto: bytes) -> ElementTree.Element | None:
    """The document the speaker answered, or None when it is not one this daemon will read."""
    if _PERIGO.search(bruto):
        # A document type and an entity are instructions to the parser, and one of them is
        # how a device on the LAN makes a reader open a file of this host or never come back.
        log.warning("a device answered a document carrying a doctype or an entity, refused")
        return None
    try:
        return ElementTree.fromstring(bruto)
    except (ElementTree.ParseError, ValueError):
        return None


def _local(etiqueta: object) -> str:
    """The name of an element without its namespace, which is how this driver matches one."""
    if not isinstance(etiqueta, str):
        return ""
    return etiqueta.rpartition("}")[2]


def _valores(raiz: ElementTree.Element) -> dict[str, str]:
    """The fields of the action inside the body of the envelope, by their bare names."""
    corpo_soap = raiz.find(f"{{{NS_ENVELOPE}}}Body")
    if corpo_soap is None or len(corpo_soap) == 0:
        return {}
    return {_local(filho.tag): (filho.text or "") for filho in corpo_soap[0]}


def _erro_upnp(raiz: ElementTree.Element | None) -> str:
    if raiz is None:
        return ""
    achado = raiz.find(f".//{{{NS_CONTROLE}}}errorCode")
    return "" if achado is None else _texto(achado.text)


def _cartao(bruto: bytes) -> Cartao | None:
    """The uid and the model of a description, or None when it is not one."""
    raiz = _arvore(bruto)
    if raiz is None:
        return None
    uid = _texto(_primeiro(raiz, "UDN")).removeprefix(PREFIXO_UUID)
    if not _UID.fullmatch(uid):
        return None
    return Cartao(uid, _texto(_primeiro(raiz, "modelName")))


def _primeiro(raiz: ElementTree.Element, nome: str) -> str:
    for elemento in raiz.iter():
        if _local(elemento.tag) == nome:
            return elemento.text or ""
    return ""


def _fontes_do_modelo(modelo: str) -> tuple[str, ...]:
    """The inputs the model of this box carries, which is what a registration starts from."""
    if not modelo:
        return ()
    nome = modelo.split()[-1].upper()
    if nome in MODELOS_DOS_DOIS:
        return (FONTE_LINHA, FONTE_TV)
    if nome in MODELOS_DE_LINHA:
        return (FONTE_LINHA,)
    if nome in MODELOS_DE_TV:
        return (FONTE_TV,)
    return ()


def _mestre_de(bruto: object) -> str | None:
    """The uid of the coordinator this box follows, or None when it follows nobody."""
    endereco = _texto(bruto)
    if not endereco.startswith(MARCA_DE_ESCRAVO):
        return None
    return endereco.rpartition(":")[2] or None


def _fonte_de(endereco: str) -> str | None:
    """Where the audio of a track comes from, read from the shape of its address."""
    if not endereco:
        return None
    if endereco.startswith(PREFIXO_VIRTUAL):
        for marca, fonte in FONTES_VIRTUAIS:
            if marca in endereco:
                return fonte
        return None
    for prefixo, fonte in FONTES_POR_PREFIXO:
        if endereco.startswith(prefixo):
            return fonte
    return None


def _titulo_do_metadado(bruto: object) -> str:
    """The title inside the metadata of a track, which is a document of its own, escaped."""
    documento = _CONTROLE.sub("", str(bruto or "")).strip()
    if not documento:
        return ""
    raiz = _arvore(documento.encode("utf-8"))
    if raiz is None:
        return ""
    return _texto(_primeiro(raiz, "title"))


def _achar_favorito(bruto: str, titulo: str) -> tuple[str, str] | None:
    """The address and the metadata of the favourite with that title, or None for no such one."""
    raiz = _arvore(bruto.encode("utf-8"))
    if raiz is None:
        return None
    procurado = titulo.casefold()
    for elemento in raiz.iter():
        if _local(elemento.tag) != "item":
            continue
        campos = {_local(filho.tag): (filho.text or "") for filho in elemento}
        if _texto(campos.get("title")).casefold() != procurado:
            continue
        endereco = _texto(campos.get("res"), ENDERECO_MAXIMO)
        if not endereco:
            return None
        return endereco, _texto(campos.get("resMD"), METADADO_MAXIMO)
    return None


def _grupo_de(bruto: str, uid: str) -> Grupo | None:
    """The members of the group this uid coordinates, or None when the topology is unreadable."""
    raiz = _arvore(bruto.encode("utf-8"))
    if raiz is None:
        return None
    membros: dict[str, Escravo] = {}
    for grupo in raiz.iter():
        if _local(grupo.tag) != "ZoneGroup" or grupo.get("Coordinator") != uid:
            continue
        # The surrounds and the subwoofer of a home theatre are Satellite elements INSIDE a
        # member, so walking only the direct children of the group leaves them out, which is
        # right: they are not boxes anybody puts on a panel.
        for membro in grupo:
            if len(membros) >= ESCRAVOS_MAXIMO:
                break
            escravo = _membro(membro, uid)
            if escravo is not None:
                membros.setdefault(escravo.identidade, escravo)
    return Grupo(tuple(membros.values()))


def _membro(elemento: ElementTree.Element, uid: str) -> Escravo | None:
    if _local(elemento.tag) != "ZoneGroupMember":
        return None
    identidade = _texto(elemento.get("UUID"))
    if not identidade or identidade == uid:
        return None
    # A bridge and an invisible member are not speakers anybody commands.
    if elemento.get("Invisible") == "1" or elemento.get("IsZoneBridge") == "1":
        return None
    endereco = ip_literal(urlsplit(_texto(elemento.get("Location"), ENDERECO_MAXIMO)).hostname)
    if endereco is None:
        return None
    return Escravo(identidade, endereco, _texto(elemento.get("ZoneName")))


def _titulo_pedido(valor: object) -> str | None:
    """The title of a favourite as the registration carries it, or None for anything else."""
    if not isinstance(valor, str):
        return None
    limpo = _CONTROLE.sub("", valor).strip()
    if not limpo or len(limpo) > TITULO_MAXIMO:
        return None
    return limpo


def _volume_valido(valor: object) -> bool:
    # True is an int in Python, and a mute arriving where a volume belongs would be written
    # as the volume 1, which is a speaker that went silent for no reason.
    return type(valor) is int and VOLUME_MINIMO <= valor <= VOLUME_MAXIMO


def _do_aparelho(bruto: object) -> int | None:
    """What the speaker answered in the 0 to 100, or None when it is not one."""
    numero = _inteiro(bruto)
    if numero is None:
        # A speaker that answers a word where a number belongs is not a volume of zero, and
        # writing zero would tell the panel a speaker is silent while it plays.
        return None
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, numero))


def _inteiro(bruto: object) -> int | None:
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, int):
        return bruto
    if not isinstance(bruto, str) or not _NUMERO.fullmatch(bruto.strip()):
        return None
    return int(bruto.strip())


def _verdade(bruto: object) -> bool | None:
    numero = _inteiro(bruto)
    return None if numero is None else numero != 0


def _texto(bruto: object, maximo: int = TEXTO_MAXIMO) -> str:
    """What the speaker wrote, cleaned and capped, exactly as it wrote it."""
    if isinstance(bruto, bool) or bruto is None:
        return ""
    return _CONTROLE.sub("", str(bruto).strip())[:maximo]


def _hospedeiro(endereco: str) -> str:
    """The address as the HOST of a URL: an IPv6 lives in brackets there, or the colons of the
    address read as a port.
    """
    return f"[{endereco}]" if ":" in endereco else endereco

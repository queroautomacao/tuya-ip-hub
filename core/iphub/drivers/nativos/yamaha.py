# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Yamaha MusicCast receiver over the Yamaha Extended Control API,.

One transport and one door: every exchange is a GET on http://{ip}/YamahaExtendedControl/v1/,
on port 80, with no authentication of any kind. That last fact is the first thing the panel
has to say: anybody on the LAN commands this receiver, which is why the address
is validated as an IP literal and never as a name.

What this file had to learn before the first line of it worked:

- HTTP 200 is not success. The result is the response_code of the JSON body, and the spec is
explicit that nothing else comes with a non zero one, so a driver that only read the status
line would report every refusal as done;
- getFeatures is the map of the model, tens of kilobytes on an AVR, so it is read once per
session and again only after the receiver went away; the poll itself is one getStatus of the
zone, plus one getPlayInfo while the input of the zone is a network one;
- the volume scale belongs to the model and comes from range_step of the zone (min, max and
step: 0 to 194 on an AVR counting half decibels, 0 to 60 on a small speaker). getStatus
answers a max_volume that is NOT the top of the scale, it is the ceiling the customer set on
the receiver, and using it as the divisor breaks the conversion only in the house that
touched that setting. The 0 to 100 also SNAPS to the step, or a model with a
step above one rounds the value itself and the reread reports a number the app
never sent, once per command, forever;
- func_list of the zone is what the zone really has, and a second zone usually has no
sound_program; the capabilities of a manifest are static, so the gate is a refusal inside
executar and a None in the field the zone does not carry;
- an AVR with a second zone is two equipments answering the same device_id, so
the zone is a field of the registration and the identity of a second zone is the device
plus the zone, written by identidade_da_zona. The sweep only knows how to ask a device who
it is, so that second identity is one the operator still writes by hand at registration;
- the transport is the single netusb engine of the whole receiver, not something the zone
owns: on HDMI, on a tuner, on bluetooth or on a line input there is no transport at all, so
reproduzindo stays None there, which is not "paused";
- mc_link and main_sync are ordinary input ids and never offered: choosing one by hand fakes a
group that was never built;
- MusicCast Link is not offered at all in this version: keeps agrupar for the
multiroom category and this is a receiver, so a group here would be a second architecture;
- Network Standby off makes the receiver stop answering HTTP in standby, so the hub reads it
offline and turning it on over the network becomes impossible; the panel says so;
- the event channel of the spec is UDP and only starts when a request carries X-AppName and
X-AppPort, so those headers are never sent ("If no event is required, do not include the
specified fields") and the 10 s poll of the base is the pace of the Yamaha controller itself.
"""

import asyncio
import json
import logging
import re
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.yamaha")

TIPO = "receiver_yamaha"

# The spec fixes the base URL with no port, and neither the reference integration nor its
# library ever asks for one; a field nobody needs is a field somebody fills in wrong.
PORTA_HTTP = 80
BASE = "/YamahaExtendedControl/v1/"

PEDE_APARELHO = "system/getDeviceInfo"
PEDE_RECURSOS = "system/getFeatures"
PEDE_REDE = "system/getNetworkStatus"
PEDE_ESTADO = "{zona}/getStatus"
PEDE_TRANSPORTE = "netusb/getPlayInfo"
# The questions of the poll, written once in the transcript and not every ten seconds.
PERGUNTAS_DE_ROTINA = (PEDE_APARELHO, PEDE_RECURSOS, PEDE_REDE, PEDE_TRANSPORTE)

MANDA_ENERGIA = "{zona}/setPower?power={valor}"
MANDA_VOLUME = "{zona}/setVolume?volume={valor}"
MANDA_MUDO = "{zona}/setMute?enable={valor}"
MANDA_FONTE = "{zona}/setInput?input={valor}&mode={modo}"
MANDA_MODO = "{zona}/setSoundProgram?program={valor}"
MANDA_CENA = "{zona}/recallScene?num={valor}"
# A preset belongs to the single netusb engine and names the zone it plays into, while a
# scene belongs to the zone; the two shapes are different on purpose.
MANDA_PRESET = "netusb/recallPreset?zone={zona}&num={valor}"
MANDA_TRANSPORTE = "netusb/setPlayback?playback={valor}"

LIGADO = "on"
DESLIGADO = "standby"
VERDADEIRO = "true"
FALSO = "false"

# SetInput starts playing the new input by default, so a scene that only
# wanted to switch inputs would start a radio; this mode is what stops that, and it exists
# from API 1.12 on, so an older receiver gets the empty mode the spec accepts.
SEM_AUTOPLAY = "autoplay_disabled"
API_COM_AUTOPLAY = 1.12

ZONA_PADRAO = "main"
ZONAS = ("main", "zone2", "zone3", "zone4")
CAMPO_ZONA = "zona"

# The words of func_list this driver gates on, the zone answers what it really has.
FUNCAO_ENERGIA = "power"
FUNCAO_VOLUME = "volume"
FUNCAO_MUDO = "mute"
FUNCAO_MODO = "sound_program"
FUNCAO_CENA = "scene"

CHAVE_CODIGO = "response_code"
CHAVE_ID = "device_id"
CHAVE_SISTEMA = "system_id"
CHAVE_SERIE = "serial_number"
CHAVE_API = "api_version"
CHAVE_MAC = "mac_address"
MACS = ("wired_lan", "wireless_lan")
CHAVE_SISTEMA_RECURSOS = "system"
CHAVE_ZONAS = "zone"
CHAVE_ITEM = "id"
CHAVE_FUNCOES = "func_list"
CHAVE_ENTRADAS = "input_list"
CHAVE_MODOS = "sound_program_list"
CHAVE_FAIXAS = "range_step"
CHAVE_CENAS = "scene_num"
CHAVE_MINIMO = "min"
CHAVE_MAXIMO = "max"
CHAVE_PASSO = "step"
CHAVE_TIPO_DE_FONTE = "play_info_type"
CHAVE_ENERGIA = "power"
CHAVE_VOLUME = "volume"
CHAVE_MUDO = "mute"
CHAVE_FONTE = "input"
CHAVE_MODO = "sound_program"
CHAVE_TRAVAS = "disable_flags"
CHAVE_REPRODUCAO = "playback"
CHAVE_FAIXA = "track"

FAIXA_DE_VOLUME = "volume"
TIPO_DE_REDE = "netusb"
TOCANDO = "play"
# Playback has five words in the spec and three of them are an engine that is running.
# Reading only "play" as playing reports a fast forward as stopped, which is the defect the
# decision of 3/set/2026 names: the app then sends play to what already plays.
EM_MOVIMENTO = (TOCANDO, "fast_forward", "fast_reverse")

# These two are input ids like any other and they are the inputs of a group; offering one
# lets the integrator pick "group" as if it were a socket, and nothing is ever grouped.
ENTRADAS_DE_GRUPO = ("mc_link", "main_sync")

CODIGO_OK = 0
# The response codes of the spec that mean something the hub can name; every other
# non zero code (1 initializing, 2 internal, 5 guarded, 6 timeout, 99 updating, 200 linking)
# is the device saying it could not, which is erro_aparelho.
CODIGO_METODO_INVALIDO = 3
CODIGO_PARAMETRO_INVALIDO = 4

# Of the spec: bit 0 says volume is inoperative right now, bit 1 says mute is.
TRAVA_VOLUME = 0b01
TRAVA_MUDO = 0b10

PREFIXO_PRESET = "preset:"
PREFIXO_CENA = "scene:"
PRESET_MAXIMO = 40
CENAS_PADRAO = 4

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100
# A model that answered no range_step still has to take a volume, and 0 to 100 step 1 is
# the only guess that cannot be worse than refusing the capability outright.
FAIXA_PADRAO = (0, 100, 1)

TEMPO_LIMITE_S = 5.0
CORPO_MAXIMO = 64 * 1024
# GetFeatures of an AVR is the whole map of the model and is read once per session, so it
# gets a ceiling of its own instead of forcing every other answer to carry that room.
CORPO_DE_RECURSOS = 512 * 1024
FALHAS_ATE_OFFLINE = 2
# The base polls every 10 s, so this is the identity asked again about once a minute,
# which is cheap next to getFeatures and is what a lease that moved mid session runs into.
POLLS_ENTRE_IDENTIDADES = 6
TEXTO_MAXIMO = 120

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
NAO_SUPORTADO = "nao_suportado"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_MODO = "modo"
ACAO_ATALHO = "atalho"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_PROXIMA = "proxima"
ACAO_ANTERIOR = "anterior"
ACAO_EXTRA = "comando_extra"

# The word each transport action puts in setPlayback, of the spec.
TRANSPORTES = {
    ACAO_TOCAR: "play",
    ACAO_PAUSAR: "pause",
    # A pause on a stream keeps the receiver attached to the station and a
    # radio needs to be let go of; stop is the one that lets go.
    ACAO_PARAR: "stop",
    ACAO_PROXIMA: "next",
    ACAO_ANTERIOR: "previous",
}

# Everything this driver writes lands in the path and the query string of the receiver
# behind a fixed base, so ONE guard proves both what this file builds and what an integrator
# typed into comando_extra: one lowercase group, one method, and an optional query of a closed
# alphabet. No scheme, no host, no '..', no '//', no '@' and no ':' can pass it, so the extra
# command can never turn the hub into a door out of this receiver into the LAN.
_NO_FIO = re.compile(r"[a-z0-9_]{1,32}/[A-Za-z0-9_]{1,40}(?:\?[A-Za-z0-9_.=&-]{1,120})?")

# One value of the protocol: an input id, a sound program, an identity. "sci-fi" is why the
# hyphen is here, and the alphabet is a subset of what the guard above lets into a query.
_VALOR = re.compile(r"[A-Za-z0-9_.-]{1,40}")
_MAC = re.compile(r"[0-9A-Fa-f]{12}")
_NUMERO = re.compile(r"[0-9]{1,3}")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

# This driver asks the receiver what it has, so it suggests almost nothing. The input
# ids and the sound programs are a list of the MODEL: an RX-A1080 answers av1 to av7 and
# refuses hdmi1, and it answers 2ch_stereo and straight and refuses movie and game, while
# another MusicCast of the same maker accepts exactly the ones it refuses. A manifest that
# guessed them handed the operator a list of buttons that all answer invalid_value, which is
# rule of never offering what cannot be acted on, broken by the driver itself. So
# getFeatures fills entradas and modos through estado.fontes and estado.modos, with one press
# on the card of lists, and what stays here is the only thing that is the same on every unit
# of the line: the four scenes, which are numbered and not named.
SUGESTOES = (
    Sugestao("atalhos", "Cena 1", "scene:1"),
    Sugestao("atalhos", "Cena 2", "scene:2"),
    Sugestao("atalhos", "Cena 3", "scene:3"),
    Sugestao("atalhos", "Cena 4", "scene:4"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "Yamaha MusicCast receiver, speaker and soundbar over the Yamaha Extended Control "
            "API. The receiver asks for no password, so anybody on this network commands it. "
            "Grouping (MusicCast Link) is not offered in this version."
        ),
        "preparo": (
            "Turn Network Standby on (Setup, Network, Network Standby: On), or the receiver cannot "
            "be turned on from here. No password: anybody on this network commands it. One zone is "
            "one registration: zone2 is another registration of this type at the same address."
        ),
        "campo_zona": (
            "The zone of the receiver: main, zone2, zone3 or zone4. Each zone is a separate "
            "equipment here, with a number of its own; leave main on a device with one zone."
        ),
        "cap_ligar": (
            "Turning on over the network only works with Network Standby on in the menu of "
            "the receiver. With it off the receiver stops answering while in standby, so the "
            "hub reads it as offline and nothing can wake it."
        ),
        "cap_fonte": (
            "The value is the input id of Yamaha, in lower case: hdmi1, tv, bd_dvd, tuner, "
            "net_radio, bluetooth, spotify. The list of the model itself is read from the "
            "receiver. mc_link and main_sync are the inputs of a group and are never offered."
        ),
        "cap_modo": (
            "The value is the sound program of Yamaha: straight, standard, movie, music, "
            "sports, game, 2ch_stereo, surr_decoder. A second zone usually has none of them."
        ),
        "cap_atalho": (
            "A shortcut is a preset of the receiver, written preset:1 up to preset:40, or a "
            "scene of the zone, written scene:1 up to the number of scenes the model has."
        ),
        "cap_tocar": (
            "Play, pause, stop, next and previous only exist while the input of the zone is a "
            "network one (radio, server, streaming service, USB). On HDMI, on a tuner or on "
            "bluetooth the receiver has no transport at all."
        ),
        "cap_comando_extra": (
            "One call of the API written as group/method, with an optional query: "
            "main/setSleep?sleep=30, main/setBass?val=2. The address is always this receiver."
        ),
        "lista_entradas": "The input ids of Yamaha in lower case, such as hdmi1 or net_radio.",
        "lista_atalhos": "preset:1 for a preset of the receiver, scene:1 for a scene of the zone.",
        "lista_modos": "The sound programs of Yamaha, such as straight, movie or 2ch_stereo.",
    },
    "pt": {
        "descricao": (
            "Receiver, caixa e soundbar Yamaha MusicCast pela API Yamaha Extended Control. O "
            "receiver não pede senha nenhuma, então qualquer um nesta rede o comanda. O "
            "agrupamento (MusicCast Link) não é oferecido nesta versão."
        ),
        "preparo": (
            "Ligue o Network Standby (Configuração, Rede, Network Standby: Ligado), ou o receiver "
            "não liga daqui. Sem senha: qualquer um nesta rede o comanda. Uma zona é um cadastro: "
            "a zone2 é outro cadastro deste tipo no mesmo endereço."
        ),
        "campo_zona": (
            "A zona do receiver: main, zone2, zone3 ou zone4. Cada zona é um equipamento "
            "separado aqui, com número próprio; deixe main num aparelho de uma zona só."
        ),
        "cap_ligar": (
            "Ligar pela rede só funciona com o Network Standby ligado no menu do receiver. Com "
            "ele desligado o receiver para de responder enquanto está em standby, então o hub "
            "o lê como offline e nada o acorda."
        ),
        "cap_fonte": (
            "O valor é o id de entrada da Yamaha, em minúsculas: hdmi1, tv, bd_dvd, tuner, "
            "net_radio, bluetooth, spotify. A lista do próprio modelo é lida do receiver. "
            "mc_link e main_sync são as entradas de grupo e nunca são oferecidas."
        ),
        "cap_modo": (
            "O valor é o modo de som da Yamaha: straight, standard, movie, music, sports, "
            "game, 2ch_stereo, surr_decoder. Uma segunda zona normalmente não tem nenhum deles."
        ),
        "cap_atalho": (
            "Um atalho é um preset do receiver, escrito preset:1 até preset:40, ou uma cena da "
            "zona, escrita scene:1 até o número de cenas que o modelo tem."
        ),
        "cap_tocar": (
            "Tocar, pausar, parar, próxima e anterior só existem enquanto a entrada da zona é "
            "de rede (rádio, servidor, serviço de streaming, USB). Em HDMI, num tuner ou em "
            "bluetooth o receiver não tem transporte nenhum."
        ),
        "cap_comando_extra": (
            "Uma chamada da API escrita como grupo/metodo, com query opcional: "
            "main/setSleep?sleep=30, main/setBass?val=2. O endereço é sempre este receiver."
        ),
        "lista_entradas": "Os ids de entrada da Yamaha em minúsculas, como hdmi1 ou net_radio.",
        "lista_atalhos": "preset:1 para um preset do receiver, scene:1 para uma cena da zona.",
        "lista_modos": "Os modos de som da Yamaha, como straight, movie ou 2ch_stereo.",
    },
}


@dataclass(frozen=True)
class Faixa:
    """The volume scale of the model, which is never the 0 to 100."""

    minimo: int
    maximo: int
    passo: int


@dataclass(frozen=True)
class Recursos:
    """The map of the model as getFeatures answers it, read once and kept for the session."""

    funcoes: frozenset[str]
    entradas: tuple[str, ...]
    modos: tuple[str, ...]
    faixa: Faixa
    cenas: int
    # Which engine plays each input, from system.input_list; only netusb has a transport.
    tipos: dict[str, str]


class _Falha(Exception):
    """A stable code on its way out of an exchange, so no exception escapes executar."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Yamaha(Driver):
    """One zone of a Yamaha MusicCast device, read and commanded over the extended control API."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Receiver Yamaha MusicCast", "en": "Yamaha MusicCast receiver"},
        categoria="receiver",
        # Agrupar is missing on purpose. keeps it for the multiroom category,
        # and MusicCast Link is a three step assembly whose confirmation is a reread of
        # getDistributionInfo; it does not belong in the first version of a receiver.
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_MODO,
            ACAO_ATALHO,
            ACAO_TOCAR,
            ACAO_PAUSAR,
            ACAO_PARAR,
            ACAO_PROXIMA,
            ACAO_ANTERIOR,
            ACAO_EXTRA,
        ),
        marca="Yamaha",
        descoberta=Descoberta(ssdp_fabricantes=("yamaha",)),
        # An AVR with a second zone answers ONE device_id for both, so the zone is what
        # separates the two registrations; the port is not a field, the spec fixes it.
        config_campos=(Campo(CAMPO_ZONA, TipoCampo.TEXTO, padrao=ZONA_PADRAO),),
        textos=TEXTOS,
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        # The poll and a command of the integrator land together, and the receiver answers
        # one exchange at a time well and two at a time badly.
        self._trava = asyncio.Lock()
        self._falhas = 0
        self._identidade: str | None = None
        self._api = 0.0
        self._recursos: Recursos | None = None
        # The map of the model is one document and the zone of this registration is a
        # question asked OF it, so a zone the receiver does not have has to be answered from
        # the document already read; resolving it before keeping it downloaded the whole map
        # again on every poll and on every command, for ever, over a field only the
        # integrator can fix.
        self._mapa: dict | None = None
        # The input of the last poll, which is what says whether a transport exists right now.
        self._fonte: str | None = None
        self._travas = 0
        self._ate_conferir = POLLS_ENTRE_IDENTIDADES

    async def iniciar(self) -> None:
        await self._abrir()

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """Who the device at that address says it is, asked with no registration.

        The spec has three identity fields and none of them is guaranteed. device_id came
        with API 1.17, system_id is the legacy one that only shows up in the example of the
        spec, and serial_number is in the table but not in the example. The order below is
        fixed here so the same receiver never becomes two registrations depending on which
        field the hub happened to read first.
        """
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        anfitriao = _hospedeiro(endereco)
        dados = await _buscar(f"http://{anfitriao}:{PORTA_HTTP}{BASE}{PEDE_APARELHO}")
        if dados is None:
            return None
        identidade = _identidade_de(dados)
        if identidade:
            return identidade
        # A receiver old enough to answer none of the three still has a MAC, and
        # takes a MAC as an identity; the address is never one.
        rede = await _buscar(f"http://{anfitriao}:{PORTA_HTTP}{BASE}{PEDE_REDE}")
        return None if rede is None else _mac_de(rede)

    def identidade_do_aparelho(self) -> str | None:
        """The identity this registration should carry: the device plus its zone,."""
        if self._identidade is None:
            return None
        zona = (self.cadastro.campos.get(CAMPO_ZONA) or ZONA_PADRAO).strip().lower()
        return identidade_da_zona(self._identidade, zona)

    async def atualizar(self) -> None:
        try:
            await self._preparar()
            await self._reconferir()
            estado = await self._perguntar(PEDE_ESTADO.format(zona=self._zona()))
            tocando = await self._tocando_agora(estado)
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        self._aplicar(estado, tocando)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        # The manager already refuses what the manifest does not declare, and a driver
        # asked directly answers the same thing without dialling out for a map it will not use.
        if acao not in self.MANIFESTO.capacidades:
            return await super().executar(acao, valor)
        try:
            # The gates of this driver are the func_list of the zone and the engine of the
            # current input, so a command that arrives before the first poll reads the map
            # first instead of guessing that the zone has everything.
            await self._preparar()
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            return await self._energia(LIGADO, True)
        if acao == ACAO_DESLIGAR:
            return await self._energia(DESLIGADO, False)
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._trocar_fonte(valor)
        if acao == ACAO_MODO:
            return await self._trocar_modo(valor)
        if acao == ACAO_ATALHO:
            return await self._atalho(valor)
        if acao in TRANSPORTES:
            return await self._transporte(acao)
        if acao == ACAO_EXTRA:
            return await self._extra(valor)
        return await super().executar(acao, valor)

    async def _energia(self, palavra: str, ligado: bool) -> str | None:
        self._exige(FUNCAO_ENERGIA)
        await self._mandar(MANDA_ENERGIA.format(zona=self._zona(), valor=palavra))
        self._defina(ligado=ligado)
        return None

    async def _trocar_volume(self, valor: object) -> str | None:
        self._exige(FUNCAO_VOLUME)
        # True is an int in Python, and a mute arriving where a volume fits would set the
        # receiver to one step above its floor instead of silencing it. The value is judged
        # before the guard of the device, so a caller writing nonsense hears about the nonsense.
        if type(valor) is not int or not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
            return INVALID_VALUE
        self._exige_destravado(TRAVA_VOLUME, FUNCAO_VOLUME)
        bruto = _para_o_aparelho(valor, self._faixa())
        await self._mandar(MANDA_VOLUME.format(zona=self._zona(), valor=bruto))
        # What is published is what the poll will read back, not what was asked for. The
        # value snapped to the step of the model comes back as another number of the 0 to 100
        # , and publishing the request instead makes the reread find
        # a divergence that is not one: a pair of reports per command, for ever, in class A.
        self._defina(volume=_do_aparelho(bruto, self._faixa()))
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        self._exige(FUNCAO_MUDO)
        if not isinstance(valor, bool):
            return INVALID_VALUE
        self._exige_destravado(TRAVA_MUDO, FUNCAO_MUDO)
        palavra = VERDADEIRO if valor else FALSO
        await self._mandar(MANDA_MUDO.format(zona=self._zona(), valor=palavra))
        self._defina(mudo=valor)
        return None

    async def _trocar_fonte(self, valor: object) -> str | None:
        palavra = _palavra_valida(valor)
        if palavra is None or palavra in ENTRADAS_DE_GRUPO:
            return INVALID_VALUE
        entradas = self._recursos.entradas if self._recursos is not None else ()
        if entradas and palavra not in entradas:
            return INVALID_VALUE
        modo = SEM_AUTOPLAY if self._api >= API_COM_AUTOPLAY else ""
        await self._mandar(MANDA_FONTE.format(zona=self._zona(), valor=palavra, modo=modo))
        # The transport of the new input is not the transport of the old one, and the
        # title of the old one is not the title of anything; the next poll says what is true.
        self._fonte = palavra
        self._defina(fonte=palavra, reproduzindo=None, tocando=None)
        return None

    async def _trocar_modo(self, valor: object) -> str | None:
        self._exige(FUNCAO_MODO)
        palavra = _palavra_valida(valor)
        if palavra is None:
            return INVALID_VALUE
        modos = self._recursos.modos if self._recursos is not None else ()
        if modos and palavra not in modos:
            return INVALID_VALUE
        await self._mandar(MANDA_MODO.format(zona=self._zona(), valor=palavra))
        self._defina(modo=palavra)
        return None

    async def _atalho(self, valor: object) -> str | None:
        """A preset of the receiver or a scene of the zone, both by number."""
        if not isinstance(valor, str):
            return INVALID_VALUE
        pedido = valor.strip()
        numero = _numero_de(pedido, PREFIXO_PRESET, PRESET_MAXIMO)
        if numero is not None:
            await self._mandar(MANDA_PRESET.format(zona=self._zona(), valor=numero))
            return None
        cenas = self._recursos.cenas if self._recursos is not None else CENAS_PADRAO
        numero = _numero_de(pedido, PREFIXO_CENA, cenas)
        if numero is None:
            return INVALID_VALUE
        self._exige(FUNCAO_CENA)
        await self._mandar(MANDA_CENA.format(zona=self._zona(), valor=numero))
        return None

    async def _transporte(self, acao: str) -> str | None:
        """The transport belongs to the single netusb engine, never to the zone."""
        if not await self._entrada_de_rede():
            return NAO_SUPORTADO
        await self._mandar(MANDA_TRANSPORTE.format(valor=TRANSPORTES[acao]))
        if acao == ACAO_TOCAR:
            self._defina(reproduzindo=True)
        elif acao in (ACAO_PAUSAR, ACAO_PARAR):
            # The poll only reads a title while the engine is moving, so leaving the old
            # one here keeps a stopped receiver showing what it was playing for up to ten
            # seconds; the two facts stop together or one of them is a lie.
            self._defina(reproduzindo=False, tocando=None)
        return None

    async def _extra(self, valor: object) -> str | None:
        if not isinstance(valor, str) or not _NO_FIO.fullmatch(valor.strip()):
            return INVALID_VALUE
        await self._mandar(valor.strip())
        return None

    async def _preparar(self) -> None:
        """The map of the model, read once per session: who the device is and what it has."""
        # A zone outside the vocabulary is a registration nothing can be dialled from, and
        # saying so before the first byte keeps a typo from becoming traffic on the LAN.
        zona = self._zona()
        if self._recursos is not None:
            return
        if self._mapa is None:
            aparelho = await self._perguntar(PEDE_APARELHO)
            await self._conferir_identidade(aparelho)
            self._api = _numero(aparelho.get(CHAVE_API))
            self._mapa = await self._perguntar(PEDE_RECURSOS, CORPO_DE_RECURSOS)
        self._recursos = _recursos_de(self._mapa, zona)
        self._ate_conferir = POLLS_ENTRE_IDENTIDADES

    async def _reconferir(self) -> None:
        """The identity again, once a minute, because a lease moves mid session.

        GetFeatures is the map of the model and costs tens of kilobytes, so it is read
        once and never again; getDeviceInfo is small, and asking it now and then is what keeps
        the hub from commanding whatever took this address over, under the name of this
        registration, until the daemon is restarted.
        """
        self._ate_conferir -= 1
        if self._ate_conferir > 0:
            return
        self._ate_conferir = POLLS_ENTRE_IDENTIDADES
        await self._conferir_identidade(await self._perguntar(PEDE_APARELHO))

    async def _conferir_identidade(self, aparelho: dict) -> None:
        """The identity is the key and the address is only where it answered today.

        Storing the identity and never comparing it means a lease that moved to another
        device leaves the hub commanding whoever now holds the address, under the name of this
        registration, for as long as the daemon runs. Two things make that comparison real
        instead of decorative. The MAC is asked here for the same reason identificar asks it,
        because a receiver that answers none of the three identity fields would otherwise
        never fill this in and never compare anything. And the first identity of a session is
        judged against the registration, because a daemon that starts after the lease moved
        has nothing of its own to compare with and would adopt the stranger in silence.
        """
        identidade = _identidade_de(aparelho) or await self._mac()
        # A device that names itself in no way this driver can read is a device there is
        # nothing to compare; refusing here would take a working receiver off the bus.
        if not identidade:
            return
        if not self._e_esperada(identidade):
            log.warning("%s: the address now answers %s", self.cadastro.identidade, identidade)
            # This is not a lost poll, it is a verdict. One lost poll keeps the last state
            # and lets the next command go out, and the next command would reach the stranger
            # holding this address; the equipment is offline from this instant.
            self._falhas = FALHAS_ATE_OFFLINE
            raise _Falha(EQ_OFFLINE)
        self._identidade = identidade

    def _e_esperada(self, identidade: str) -> bool:
        """Whether that identity is the one this equipment was registered for,."""
        if self._identidade is not None:
            return identidade == self._identidade
        cadastrada = _texto(self.cadastro.identidade)
        # The registration of a second zone carries the device plus the zone, so both
        # shapes of the key answer for the same receiver; a registration with no identity at
        # all has nothing to be compared against and takes what answers.
        if not cadastrada:
            return True
        return cadastrada in (identidade, identidade_da_zona(identidade, self._zona()))

    async def _mac(self) -> str:
        """The last resort of identificar, asked again here: takes a MAC as a key."""
        try:
            rede = await self._perguntar(PEDE_REDE)
        except _Falha as falha:
            log.debug("%s: no network status: %s", self.cadastro.identidade, falha.codigo)
            return ""
        return _mac_de(rede) or ""

    async def _tocando_agora(self, estado: dict) -> dict | None:
        """The netusb engine, asked only while the input of the zone is a network one."""
        if not _de_rede(_texto(estado.get(CHAVE_FONTE)), self._recursos):
            return None
        try:
            return await self._perguntar(PEDE_TRANSPORTE)
        except _Falha as falha:
            # The zone already answered, so the receiver is here; only the title and the
            # transport are missing, and losing those is not losing the equipment.
            log.debug("%s: netusb did not answer: %s", self.cadastro.identidade, falha.codigo)
            return None

    def _aplicar(self, estado: dict, tocando: dict | None) -> None:
        recursos = self._recursos
        funcoes = frozenset() if recursos is None else recursos.funcoes
        fonte = _texto(estado.get(CHAVE_FONTE))
        self._fonte = fonte or None
        self._travas = _inteiro(estado.get(CHAVE_TRAVAS)) or 0
        modo = _texto(estado.get(CHAVE_MODO))
        self._defina(
            online=True,
            ligado=_ligado_de(estado) if FUNCAO_ENERGIA in funcoes else None,
            volume=_do_aparelho(estado.get(CHAVE_VOLUME), self._faixa())
            if FUNCAO_VOLUME in funcoes
            else None,
            mudo=_verdade(estado.get(CHAVE_MUDO)) if FUNCAO_MUDO in funcoes else None,
            fonte=fonte or None,
            fontes=() if recursos is None else recursos.entradas,
            modos=() if recursos is None else recursos.modos,
            modo=(modo or None) if FUNCAO_MODO in funcoes else None,
            reproduzindo=_reproduzindo_de(tocando),
            tocando=_titulo_de(tocando),
            detalhe="",
        )

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        # What answers at this address after a silence may be another device, or the same
        # one after a firmware update that changed its map, so the map is read again. The
        # identity is NOT forgotten with it: it is the key and it does not change
        # because the receiver went quiet, and forgetting it is exactly what would let whoever
        # took this address over be adopted under the name of this registration.
        self._recursos = None
        # A refusal about a VALUE is not the device going away, and the map of the model
        # does not change because a zone of the registration is not in it or because the
        # receiver refused a parameter; throwing the document out here is what downloaded
        # tens of kilobytes every ten seconds, for ever, on a registration nobody fixed yet.
        if codigo != INVALID_VALUE:
            self._mapa = None
        self._fonte = None
        self._defina(online=False, reproduzindo=None, tocando=None, detalhe=codigo)

    def _zona(self) -> str:
        """The zone of the registration, which is half of the identity of this equipment."""
        zona = (self.cadastro.campos.get(CAMPO_ZONA) or ZONA_PADRAO).strip().lower()
        if zona not in ZONAS:
            raise _Falha(INVALID_VALUE)
        return zona

    def _faixa(self) -> Faixa:
        return Faixa(*FAIXA_PADRAO) if self._recursos is None else self._recursos.faixa

    def _exige(self, funcao: str) -> None:
        """A capability the zone does not carry is refused here, not on the wire."""
        recursos = self._recursos
        if recursos is not None and funcao not in recursos.funcoes:
            raise _Falha(NAO_SUPORTADO)

    def _exige_destravado(self, bit: int, funcao: str) -> None:
        """Disable_flags of the last poll: what the receiver is guarding right now.

        With the bit on, the receiver answers response code 5 and nothing happens, and
        reading the field is what tells a refusal of the device from a mistake of the hub.
        """
        if self._travas & bit:
            log.warning("%s: the receiver is guarding %s", self.cadastro.identidade, funcao)
            raise _Falha(ERRO_APARELHO)

    async def _entrada_de_rede(self) -> bool:
        """Whether the input playing right now runs on the netusb engine."""
        if self._fonte is None:
            estado = await self._perguntar(PEDE_ESTADO.format(zona=self._zona()))
            self._fonte = _texto(estado.get(CHAVE_FONTE)) or None
        return _de_rede(self._fonte or "", self._recursos)

    async def _mandar(self, caminho: str) -> None:
        """One command; the answer carries nothing but the response code, which _perguntar reads."""
        await self._perguntar(caminho)

    async def _perguntar(self, caminho: str, teto: int = CORPO_MAXIMO) -> dict:
        """One exchange with the receiver, answered as the object of a response code zero."""
        if not _NO_FIO.fullmatch(caminho):
            raise _Falha(INVALID_VALUE)
        url = f"http://{_hospedeiro(self._endereco())}:{PORTA_HTTP}{BASE}{caminho}"
        # The poll asks the same four things every ten seconds; the transcript writes
        # them once and writes every command, which is what a diagnosis reads.
        rotina = caminho in PERGUNTAS_DE_ROTINA or caminho.endswith("/getStatus")
        transcricao = self._transcricao()
        transcricao.enviado(f"GET {BASE}{caminho}", rotina=rotina)
        async with self._trava:
            sessao = await self._abrir()
            try:
                async with sessao.get(
                    url,
                    # A receiver answering a redirect would send the hub to whatever host
                    # it names, which is the LAN proxy refuses.
                    allow_redirects=False,
                ) as resposta:
                    bruto = await corpo.inteiro(resposta.content, teto)
                    estado = resposta.status
            except (TimeoutError, ClientError, OSError, ValueError) as erro:
                transcricao.falhou(f"GET {BASE}{caminho}", erro)
                raise _Falha(EQ_OFFLINE) from erro
        transcricao.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        if estado >= 400:
            log.warning("the receiver answered HTTP %d to %s", estado, caminho)
            raise _Falha(ERRO_APARELHO)
        return _documento(bruto, caminho)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self.cadastro.identidade or self.cadastro.ip)
            self._fio = transcricao
        return transcricao

    def _endereco(self) -> str:
        """Only an IP literal reaches a device, so the hub is never a resolver."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco


def identidade_da_zona(identidade: str, zona: str) -> str:
    """The two zones of one AVR answer the same device_id, so the zone joins the key."""
    return identidade if zona == ZONA_PADRAO else f"{identidade}_{zona}"


async def _buscar(url: str) -> dict | None:
    """The object of one GET that answered a response code zero, or None for anything else."""
    try:
        async with ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao:
            async with sessao.get(url, allow_redirects=False) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                if resposta.status >= 400:
                    return None
    except (TimeoutError, ClientError, OSError, ValueError):
        return None
    try:
        dados = json.loads(bruto.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return None
    return dados if isinstance(dados, dict) and _codigo_de(dados) == CODIGO_OK else None


def _documento(bruto: bytes, caminho: str) -> dict:
    """Of this file: HTTP 200 is not success, the response code of the body is."""
    try:
        dados = json.loads(bruto.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError) as erro:
        raise _Falha(ERRO_APARELHO) from erro
    if not isinstance(dados, dict):
        raise _Falha(ERRO_APARELHO)
    codigo = _codigo_de(dados)
    if codigo == CODIGO_OK:
        return dados
    log.warning("the receiver answered response code %r to %s", codigo, caminho)
    raise _Falha(_codigo_estavel(codigo))


def _codigo_estavel(codigo: int | None) -> str:
    """The response code of the spec as one of the stable codes."""
    if codigo == CODIGO_METODO_INVALIDO:
        return NAO_SUPORTADO
    if codigo == CODIGO_PARAMETRO_INVALIDO:
        return INVALID_VALUE
    return ERRO_APARELHO


def _codigo_de(dados: dict) -> int | None:
    """The response code as an integer, where a bool is not one; False would read as zero."""
    codigo = dados.get(CHAVE_CODIGO)
    return codigo if type(codigo) is int else None


def _identidade_de(dados: dict) -> str:
    """The identity of the device, in the fixed order of the docstring of identificar."""
    for chave in (CHAVE_ID, CHAVE_SISTEMA, CHAVE_SERIE):
        valor = _texto(dados.get(chave))
        if _VALOR.fullmatch(valor):
            return valor
    return ""


def _mac_de(dados: dict) -> str | None:
    """The MAC of the wired or of the wireless interface, with no separator, as keys."""
    enderecos = dados.get(CHAVE_MAC)
    if not isinstance(enderecos, dict):
        return None
    for chave in MACS:
        mac = _texto(enderecos.get(chave)).replace(":", "").replace("-", "")
        if _MAC.fullmatch(mac):
            return mac.upper()
    return None


def _recursos_de(dados: dict, zona: str) -> Recursos:
    """The map of the model for ONE zone, or a refusal when the receiver has no such zone."""
    achada = _da_lista(dados.get(CHAVE_ZONAS), zona)
    if achada is None:
        # A registration that names zone3 on a two zone AVR is a registration that can
        # never be commanded, and saying so by code is what gets the field corrected.
        log.warning("the receiver has no zone %r", zona)
        raise _Falha(INVALID_VALUE)
    entradas = tuple(e for e in _palavras(achada.get(CHAVE_ENTRADAS)) if e not in ENTRADAS_DE_GRUPO)
    cenas = _inteiro(achada.get(CHAVE_CENAS))
    return Recursos(
        funcoes=frozenset(_palavras(achada.get(CHAVE_FUNCOES))),
        entradas=entradas,
        modos=_palavras(achada.get(CHAVE_MODOS)),
        faixa=_faixa_de(achada.get(CHAVE_FAIXAS)),
        cenas=cenas if cenas and cenas > 0 else CENAS_PADRAO,
        tipos=_tipos_de(dados.get(CHAVE_SISTEMA_RECURSOS)),
    )


def _da_lista(bruto: object, identificador: str) -> dict | None:
    if not isinstance(bruto, list):
        return None
    for item in bruto:
        if isinstance(item, dict) and _texto(item.get(CHAVE_ITEM)) == identificador:
            return item
    return None


def _tipos_de(sistema: object) -> dict[str, str]:
    """Which engine plays each input of the whole device, from system.input_list."""
    tipos: dict[str, str] = {}
    if not isinstance(sistema, dict):
        return tipos
    entradas = sistema.get(CHAVE_ENTRADAS)
    if not isinstance(entradas, list):
        return tipos
    for item in entradas:
        if not isinstance(item, dict):
            continue
        nome = _texto(item.get(CHAVE_ITEM))
        if _VALOR.fullmatch(nome):
            tipos[nome] = _texto(item.get(CHAVE_TIPO_DE_FONTE))
    return tipos


def _faixa_de(bruto: object) -> Faixa:
    """The volume scale of the zone, from range_step; anything unreadable falls back to 0 a 100."""
    item = _da_lista(bruto, FAIXA_DE_VOLUME)
    if item is None:
        return Faixa(*FAIXA_PADRAO)
    minimo = _inteiro(item.get(CHAVE_MINIMO))
    maximo = _inteiro(item.get(CHAVE_MAXIMO))
    passo = _inteiro(item.get(CHAVE_PASSO))
    if minimo is None or maximo is None or maximo <= minimo:
        return Faixa(*FAIXA_PADRAO)
    return Faixa(minimo, maximo, passo if passo and passo > 0 else 1)


def _de_rede(fonte: str, recursos: Recursos | None) -> bool:
    """Whether that input runs on the netusb engine, which is the only one with a transport."""
    if not fonte or recursos is None:
        return False
    return recursos.tipos.get(fonte) == TIPO_DE_REDE


def _reproduzindo_de(tocando: dict | None) -> bool | None:
    """The transport, which is a different fact from the title.

    Outside the netusb engine there is no transport at all, and answering False there
    would tell the app the receiver is paused while an HDMI plays at full volume.
    """
    if tocando is None:
        return None
    estado = _texto(tocando.get(CHAVE_REPRODUCAO)).lower()
    return None if not estado else estado in EM_MOVIMENTO


def _titulo_de(tocando: dict | None) -> str | None:
    """The track of the netusb engine while it runs, and nothing else.

    The spec warns that text stays in artist, album and track with playback stopped, so
    the title is only true while the engine is moving, which a fast forward also is.
    """
    if tocando is None or _texto(tocando.get(CHAVE_REPRODUCAO)).lower() not in EM_MOVIMENTO:
        return None
    return _texto(tocando.get(CHAVE_FAIXA)) or None


def _ligado_de(estado: dict) -> bool | None:
    palavra = _texto(estado.get(CHAVE_ENERGIA)).lower()
    return None if not palavra else palavra == LIGADO


def _palavra_valida(valor: object) -> str | None:
    """One value of the registration list on its way to the query string of the receiver."""
    if not isinstance(valor, str):
        return None
    palavra = valor.strip()
    return palavra if _VALOR.fullmatch(palavra) else None


def _numero_de(pedido: str, prefixo: str, teto: int) -> int | None:
    """The number behind preset: or scene:, inside the ceiling of the model."""
    if not pedido.startswith(prefixo):
        return None
    resto = pedido[len(prefixo) :]
    if not _NUMERO.fullmatch(resto):
        return None
    numero = int(resto)
    return numero if 1 <= numero <= teto else None


def _palavras(bruto: object) -> tuple[str, ...]:
    """The words of a list the receiver answered, in order, with no repeats and no surprises."""
    if not isinstance(bruto, list):
        return ()
    palavras: list[str] = []
    for item in bruto:
        palavra = _texto(item)
        if _VALOR.fullmatch(palavra) and palavra not in palavras:
            palavras.append(palavra)
    return tuple(palavras)


def _do_aparelho(bruto: object, faixa: Faixa) -> int | None:
    """The volume of the model as the 0 to 100."""
    valor = _inteiro(bruto)
    if valor is None or faixa.maximo <= faixa.minimo:
        return None
    convertido = (valor - faixa.minimo) * VOLUME_MAXIMO / (faixa.maximo - faixa.minimo)
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, round(convertido)))


def _para_o_aparelho(valor: int, faixa: Faixa) -> int:
    """The 0 to 100 as a volume of the model, SNAPPED to the step of the model.

    A receiver counting in steps of five rounds anything else by itself, and then the
    reread finds a value nobody sent and reports it, once per command.
    """
    largura = faixa.maximo - faixa.minimo
    alvo = faixa.minimo + largura * valor / VOLUME_MAXIMO
    bruto = faixa.minimo + round((alvo - faixa.minimo) / faixa.passo) * faixa.passo
    if bruto > faixa.maximo:
        bruto = faixa.minimo + (largura // faixa.passo) * faixa.passo
    return int(max(faixa.minimo, bruto))


def _texto(bruto: object) -> str:
    """A string of the device, without the control characters and inside a ceiling."""
    if not isinstance(bruto, str):
        return ""
    return _CONTROLE.sub("", bruto).strip()[:TEXTO_MAXIMO]


def _inteiro(bruto: object) -> int | None:
    if type(bruto) is int:
        return bruto
    if isinstance(bruto, str) and bruto.strip().lstrip("-").isdigit():
        return int(bruto.strip())
    return None


def _numero(bruto: object) -> float:
    if isinstance(bruto, bool):
        return 0.0
    if isinstance(bruto, int | float):
        return float(bruto)
    try:
        return float(bruto) if isinstance(bruto, str) else 0.0
    except ValueError:
        return 0.0


def _verdade(bruto: object) -> bool | None:
    if isinstance(bruto, bool):
        return bruto
    lido = _texto(bruto).lower()
    if lido in ("true", "on", "1"):
        return True
    if lido in ("false", "off", "0"):
        return False
    return None


def _hospedeiro(endereco: str) -> str:
    return f"[{endereco}]" if ":" in endereco else endereco

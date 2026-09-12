# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""LG webOS TV over the SSAP WebSocket the TV itself serves,.

The TV speaks one protocol and it is not HTTP: a WebSocket on 3000 in the clear, or on 3001
over TLS with a self signed certificate, carrying JSON messages that are answered by the id
they were sent with. There is no REST and no line on a TCP port, so this driver writes the
protocol itself over aiohttp and the standard library, with no dependency added.

What the TV does, so nobody has to read the protocol again:

- the handshake is a hello that answers deviceUUID BEFORE any pairing, which is the identity
 and the same uuid the SSDP announcement of the TV carries in its UDN;
- getSystemInfo is asked BEFORE the register, because a new firmware refuses to register
without it, and it may fail after the register; a driver that treated an inventory failure
as a connection failure would never connect to a recent TV;
- the register shows a dialog on the screen and answers a client-key that opens every later
session with no dialog at all, and the TV may ROTATE that key, so the answer of every
register is read back instead of assumed;
- a person walks to the TV and looks for the remote, which no ten second deadline covers, so
the pairing answers "aguardando" at once and the wait for the dialog lives in a task the
next call collects, exactly as allows;
- ligar does not exist over IP, because the WebSocket server dies with the screen: the power
on is a Wake-on-LAN packet to the MAC of the registration, and only draws the
power key when ligar and desligar are BOTH declared, so the MAC is a required field;
- desligar on a TV that is already off TURNS IT ON, so it is guarded here, and the answer to
it is unreliable, so nothing waits for one;
- getVolume answers the level nested under volumeStatus on some firmwares and loose on
others, and reading only one of the two breaks on half of the TVs;
- with the sound on lineout the TV has no volume of its own to set or to report, and on an
external speaker it takes steps but not an absolute level, so a value that would be
swallowed in silence is refused instead;
- the navigation keys do NOT go through the main socket: they need a second one, whose
address the TV hands over at each request and which speaks lines of text and not JSON, so
it is opened on the first key and never on iniciar;
- the list of inputs and of apps comes back EMPTY while the TV is off, so the previous one is
kept, or the panel would lose the lists of the equipment every night;
- there is no next track and no previous track in the protocol, and no single play and pause
button either, so this driver declares neither and offers the channel keys, which is what
they are.

The client-key is a secret of the registration: it never goes back to the panel
and never lands in the log. A key the TV hands over during pairing is held for the life of
the daemon and published by chave_do_aparelho, because the contract gives a
driver its registration and nothing that writes one.
"""

import asyncio
import ipaddress
import json
import logging
import re
import socket
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import urlsplit

from aiohttp import (
    ClientError,
    ClientSession,
    ClientTimeout,
    ClientWebSocketResponse,
    ClientWSTimeout,
    WSMsgType,
)

from iphub.config import ip_literal
from iphub.drivers import fio
from iphub.drivers.base import RESULTADOS, Cadastro, Driver
from iphub.drivers.manifesto import Auth, Campo, Descoberta, Manifesto, Sugestao, TipoCampo

log = logging.getLogger("iphub.drivers.nativos.lg_webos")

TIPO = "tv_lg_webos"

# The TV serves the protocol in the clear on 3000 and over TLS on 3001, and no field of a
# registration tells which one this firmware accepts; the one that answered is kept.
PORTAS = (3000, 3001)
PORTA_SEGURA = 3001
ESQUEMA = "ws"
ESQUEMA_SEGURO = "wss"

# The certificate of the TV is self signed, so validating it would refuse every TV on
# every LAN; already says there is no TLS to trust inside the house.
SEM_CERTIFICADO = False

CONEXAO_S = 2.0
RESPOSTA_S = 2.0
FECHAMENTO_S = 2.0
BATIMENTO_S = 5.0

# The deadline of the gestor is half of its interval, and one poll asks the TV eight
# questions; the budget is what keeps a TV that went quiet from spending the whole deadline on
# the first of them and leaving the panel with no state at all.
ORCAMENTO_DO_POLL_S = 3.5

# A person has to walk to the TV, find the remote and press OK, which the ten seconds of
# the reference client never covered; the wait lives in a task and this is its ceiling.
PAREAMENTO_S = 90.0

# The channel list of a TV with hundreds of channels travels in one message, which is why
# the reference client raised the ceiling; the default of four megabytes drops the connection
# on a full TV and the symptom appears far from the cause.
MENSAGEM_MAXIMA = 8 * 1024 * 1024
# The second socket only ever carries a line of text, so it needs no room at all.
MENSAGEM_DE_ENTRADA_MAXIMA = 8 * 1024

# One lost poll is not a TV that went away, two in a row is; a TV that is off refuses the
# connection, which is the normal state of a TV that is off and not a defect.
FALHAS_ATE_OFFLINE = 2

# An answer of the TV reaches the panel and the DP-bus, so what it writes is capped here.
TEXTO_MAXIMO = 120
APLICATIVOS_MAXIMO = 200
ENTRADAS_MAXIMO = 32
URL_MAXIMA = 200
MENSAGENS_ATE_DESISTIR = 8

PREFIXO = "ssap://"

URI_SISTEMA = "system/getSystemInfo"
URI_FIRMWARE = "com.webos.service.update/getCurrentSWInformation"
URI_LIGAR = "system/turnOn"
URI_DESLIGAR = "system/turnOff"
URI_ENERGIA = "com.webos.service.tvpower/power/getPowerState"
URI_VOLUME = "audio/getVolume"
URI_DEFINIR_VOLUME = "audio/setVolume"
URI_MAIS = "audio/volumeUp"
URI_MENOS = "audio/volumeDown"
URI_AUDIO = "audio/getStatus"
URI_MUDO = "audio/setMute"
URI_APLICATIVO = "com.webos.applicationManager/getForegroundAppInfo"
URI_APLICATIVOS = "com.webos.applicationManager/listLaunchPoints"
URI_ABRIR = "system.launcher/launch"
URI_ENTRADAS = "tv/getExternalInputList"
URI_TROCAR_ENTRADA = "tv/switchInput"
URI_CANAL_MAIS = "tv/channelUp"
URI_CANAL_MENOS = "tv/channelDown"
URI_CANAL = "tv/getCurrentChannel"
URI_SAIDA = "com.webos.service.apiadapter/audio/getSoundOutput"
URI_TROCAR_SAIDA = "com.webos.service.apiadapter/audio/changeSoundOutput"
URI_MIDIA = "com.webos.media/getForegroundAppInfo"
# The questions of the poll, written once in the transcript and not every ten seconds.
PERGUNTAS_DE_ROTINA = (
    URI_APLICATIVO,
    URI_ENERGIA,
    URI_VOLUME,
    URI_SAIDA,
    URI_ENTRADAS,
    URI_APLICATIVOS,
    URI_AUDIO,
    URI_MIDIA,
)
URI_TOCAR = "media.controls/play"
URI_PAUSAR = "media.controls/pause"
URI_PARAR = "media.controls/stop"
URI_PONTEIRO = "com.webos.service.networkinput/getPointerInputSocket"
URI_TELA_DESLIGADA = "com.webos.service.tvpower/power/turnOffScreen"
URI_TELA_LIGADA = "com.webos.service.tvpower/power/turnOnScreen"

ID_OLA = "hello"
ID_SISTEMA = "get_sys_info"
ID_FIRMWARE = "get_sw_info"
ID_REGISTRO = "register_0"

TIPO_OLA = "hello"
TIPO_PEDIDO = "request"
TIPO_RESPOSTA = "response"
TIPO_REGISTRO = "register"
TIPO_REGISTRADO = "registered"
TIPO_ERRO = "error"

PAREAMENTO_POR_DIALOGO = "PROMPT"

CHAVE_UUID = "deviceUUID"
CHAVE_CLIENTE = "client-key"
CHAVE_TIPO_DE_PAREAMENTO = "pairingType"
CHAVE_ESTADO_DE_ENERGIA = "state"
CHAVE_VOLUME = "volume"
CHAVE_ESTADO_DE_VOLUME = "volumeStatus"
CHAVE_MUDO = "mute"
CHAVE_APLICATIVO = "appId"
CHAVE_ENTRADAS = "devices"
CHAVE_APLICATIVOS = "launchPoints"
CHAVE_TITULO = "title"
CHAVE_ROTULO = "label"
CHAVE_ID = "id"
CHAVE_SAIDA = "soundOutput"
CHAVE_MIDIA = "foregroundAppInfo"
CHAVE_TRANSPORTE = "playState"
CHAVE_CANAL = "channelName"
CHAVE_MODELO = "modelName"
CHAVE_MAIOR = "major_ver"
CHAVE_MENOR = "minor_ver"
CHAVE_RETORNO = "returnValue"
CHAVE_ASSINADO = "subscribed"
CHAVE_ASSINATURA = "subscription"
CHAVE_SOCKET = "socketPath"
CHAVE_ERRO = "error"

# This is the ONE error that means the model does not have the function, and the answer
# to it is nao_suportado; every other error of the TV is a device that failed.
ERRO_NAO_EXISTE = "404 no such service or method"

# The power states of a TV that is not on, from the rule the reference client applies.
ENERGIAS_APAGADAS = ("power off", "suspend", "active standby")

APP_TV_ABERTA = "com.webos.app.livetv"

TRANSPORTE_TOCANDO = "playing"
TRANSPORTE_PAUSADO = "paused"

# With the sound going out on the line output the TV has no level of its own, and with an
# external speaker it takes steps but not an absolute level; sending one would be swallowed in
# silence and the panel would show a bar the TV never moved.
SAIDA_SEM_VOLUME = "lineout"
# Measured at the bench, a TV whose sound leaves by ARC to a receiver answers an error to
# setVolume: it has no level of its own, it relays a step over CEC and the receiver owns the
# number. external_arc was missing from this list, so the panel drew a bar the television
# could never move and every drag of it came back an error. Optical and the bluetooth soundbar
# are the same shape of output and are here for the same reason.
SAIDAS_SEM_NIVEL = (
    "external_speaker",
    "external_arc",
    "external_optical",
    "bt_soundbar",
)

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100

# Wake-on-LAN, the container runs on the host network, so the broadcast leaves on
# the LAN of the customer; the two ports are the ones a magic packet is listened for on.
ENDERECO_WOL = "255.255.255.255"
PORTAS_WOL = (9, 7)
ENVIOS_WOL = 3
INTERVALO_WOL_S = 0.1

# The button of the remote control that opens Netflix turns the television on first, and
# an automation that cannot do the same is an automation the customer does by hand. The
# deadline of one action is half a poll interval, far less than a TV takes to boot, so the
# waiting belongs to a task of the driver: the magic packet leaves, the action is accepted,
# and the app opens when the television answers. A ceiling on the wait, because a set that
# never wakes must not leave a task of this daemon alive until the next restart.
ESPERA_ATE_ACORDAR_S = 30.0
PASSO_DE_ESPERA_S = 1.5

CAMPO_MAC = "mac"
CAMPO_CHAVE = "chave_cliente"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"
AUTH_PENDENTE = "auth_pendente"
NAO_SUPORTADO = "nao_suportado"

PAREADO = "pareado"
AGUARDANDO = "aguardando"
FALHOU = "falhou"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_ATALHO = "atalho"
ACAO_MODO = "modo"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_TECLA = "tecla"
ACAO_EXTRA = "comando_extra"

# Opening an app or choosing an input on a set that is off is the gesture of the remote
# control, which turns the television on first; volume and keys on a dark screen are not.
ACOES_QUE_ACORDAM = (ACAO_ATALHO, ACAO_FONTE)

TRANSPORTES = {ACAO_TOCAR: URI_TOCAR, ACAO_PAUSAR: URI_PAUSAR, ACAO_PARAR: URI_PARAR}

# Four keys have an endpoint of their own on the main socket, and using it
# spares a TV that only ever changes volume and channel the cost of the second socket.
TECLAS_COM_ENDPOINT = {
    "mais": URI_MAIS,
    "menos": URI_MENOS,
    "canal_mais": URI_CANAL_MAIS,
    "canal_menos": URI_CANAL_MENOS,
}

# The words as the names the remote socket of the TV takes.
NOMES_DE_TECLA = {
    "cima": "UP",
    "baixo": "DOWN",
    "esquerda": "LEFT",
    "direita": "RIGHT",
    "ok": "ENTER",
    "voltar": "BACK",
    "inicio": "HOME",
    "menu": "MENU",
    "guia": "GUIDE",
    "sair": "EXIT",
    "info": "INFO",
    **{f"digito_{numero}": str(numero) for numero in range(10)},
}

TECLAS = (*TECLAS_COM_ENDPOINT, *NOMES_DE_TECLA)

BOTAO = "type:button\nname:{nome}\n\n"

# The extra channel carries a name and nothing else, so the set of extras is
# CLOSED here; a free uri would be raw passage into a device of the LAN, which refuses.
EXTRAS = {"tela_desligada": URI_TELA_DESLIGADA, "tela_ligada": URI_TELA_LIGADA}

# The permissions the register asks for, literal and in this order, as the TV expects them.
PERMISSOES = (
    "APP_TO_APP",
    "CLOSE",
    "CONTROL_AUDIO",
    "CONTROL_DISPLAY",
    "CONTROL_INPUT_JOYSTICK",
    "CONTROL_INPUT_MEDIA_PLAYBACK",
    "CONTROL_INPUT_MEDIA_RECORDING",
    "CONTROL_INPUT_TEXT",
    "CONTROL_INPUT_TV",
    "CONTROL_MOUSE_AND_KEYBOARD",
    "CONTROL_POWER",
    "CONTROL_TV_SCREEN",
    "LAUNCH",
    "LAUNCH_WEBAPP",
    "READ_APP_STATUS",
    "READ_COUNTRY_INFO",
    "READ_CURRENT_CHANNEL",
    "READ_INPUT_DEVICE_LIST",
    "READ_INSTALLED_APPS",
    "READ_LGE_SDX",
    "READ_LGE_TV_INPUT_EVENTS",
    "READ_NETWORK_STATE",
    "READ_NOTIFICATIONS",
    "READ_POWER_STATE",
    "READ_RUNNING_APPS",
    "READ_SETTINGS",
    "READ_TV_CHANNEL_LIST",
    "READ_TV_CURRENT_TIME",
    "READ_UPDATE_INFO",
    "SEARCH",
    "TEST_OPEN",
    "TEST_PROTECTED",
    "TEST_SECURE",
    "UPDATE_FROM_REMOTE_APP",
    "WRITE_NOTIFICATION_ALERT",
    "WRITE_NOTIFICATION_TOAST",
    "WRITE_SETTINGS",
)

VERSAO_DO_MANIFESTO = 1
VERSAO_DO_APLICATIVO = "1.1"

# The value of an input, of a shortcut and of a sound mode is a word of the TV that lands
# inside a message on the wire, so what is not one of these bytes never leaves this file.
_NO_FIO = re.compile(r"[A-Za-z0-9._:\-]{1,64}")
_MAC = re.compile(r"[0-9A-Fa-f]{12}")
_SEPARADOR_DE_MAC = re.compile(r"[:.\- ]")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

# The inputs of a TV are the sockets on its back, the shortcut is the id of an app in the
# list the TV itself publishes, and the sound outputs change with the model; these are what a
# new registration is born with, and the integrator renames and completes them.
SUGESTOES = (
    Sugestao("entradas", "HDMI 1", "HDMI_1"),
    Sugestao("entradas", "HDMI 2", "HDMI_2"),
    Sugestao("entradas", "HDMI 3", "HDMI_3"),
    Sugestao("entradas", "HDMI 4", "HDMI_4"),
    Sugestao("atalhos", "TV aberta", APP_TV_ABERTA),
    Sugestao("atalhos", "Netflix", "netflix"),
    Sugestao("atalhos", "YouTube", "youtube.leanback.v4"),
    Sugestao("modos", "Soundbar (ARC)", "external_arc"),
    Sugestao("modos", "Caixa externa", "external_speaker"),
    Sugestao("modos", "Saida de linha", SAIDA_SEM_VOLUME),
)

TEXTOS = {
    "en": {
        "descricao": (
            "LG webOS TV over the WebSocket the TV serves. Power on goes by Wake-on-LAN, "
            "because the TV answers nothing over IP while it is off, and everything else goes "
            "over the socket: level, mute, input, apps, sound mode, transport and the keys."
        ),
        "preparo": (
            "1. Turn LG Connect Apps on: Settings, General (or Network), External Devices, LG "
            "Connect Apps.\n"
            "2. Turn Wake on LAN on so the hub can turn the TV on: on a 2025 set, Support, IP "
            "Control Settings; on a 2017 to 2024 set, General, External Devices, Mobile TV On "
            "(Turn on via Wi-Fi). Prefer a cable to Wi-Fi.\n"
            "3. Write the MAC of the TV (Network, Network Status) into the MAC field; the network "
            "sweep shows it too.\n"
            "4. Save, press Pair, and accept the dialog on the screen of the TV."
        ),
        "campo_mac": (
            "MAC of the TV, which is what turns it on. Enable Wake on LAN under Support, IP "
            "control settings on a 2025 TV, or Mobile TV On, Turn on via Wi-Fi under General "
            "on a 2017 one, and prefer a cable to Wi-Fi."
        ),
        "campo_chave_cliente": (
            "Key the TV hands over when it is paired. Leave it empty and pair: it is only "
            "asked for here to move an installation that already has one."
        ),
        "auth_ajuda": (
            "Turn LG Connect Apps on under the network settings of the TV, or the dialog never "
            "shows up and it looks exactly like a TV that is off. Pair, walk to the TV and "
            "accept on the screen, then pair again to collect the answer."
        ),
        "cap_ligar": (
            "Power on is a Wake-on-LAN packet to the MAC of the registration, and the TV takes "
            "a few seconds to accept a command after it, so a scene waits between the two."
        ),
        "cap_volume": (
            "The level is refused while the sound goes out on the line output or on an "
            "external speaker, which take steps and not a level; the keys still work."
        ),
        "cap_fonte": (
            "The value is the id of a physical input of the TV, HDMI_1 and the like, which the "
            "TV lists by itself and the panel shows in the inputs of the equipment."
        ),
        "cap_atalho": (
            "The value is the id of an app on the TV, netflix or com.webos.app.livetv for the "
            "open channels, from the list of apps the TV publishes."
        ),
        "cap_modo": (
            "The value is a sound output of this model, external_arc for a soundbar; the one "
            "the TV is on right now is shown in the mode of the equipment."
        ),
        "cap_comando_extra": (
            "Two names, and no more: tela_desligada turns the screen off while the sound goes "
            "on, and tela_ligada brings it back. A model without them refuses once and is "
            "never asked again."
        ),
        "lista_entradas": "The id of a socket on the back of the TV, HDMI_1 and the like.",
        "lista_atalhos": "The id of an app, netflix, youtube.leanback.v4, com.webos.app.livetv.",
        "lista_modos": (
            "A sound output of this model. The one the equipment shows as its mode right now is "
            "the word this TV uses for the speakers it is playing on."
        ),
    },
    "pt": {
        "descricao": (
            "TV LG webOS pelo WebSocket que a TV serve. Ligar vai por Wake-on-LAN, porque a TV "
            "não responde nada por IP enquanto está apagada, e todo o resto vai pelo socket: "
            "nível, mudo, entrada, aplicativos, modo de som, transporte e as teclas."
        ),
        "preparo": (
            "1. Ligue o LG Connect Apps: Configurações, Geral (ou Rede), Dispositivos externos, LG "
            "Connect Apps.\n"
            "2. Ligue o Wake on LAN para o hub acender a TV: numa TV de 2025, Suporte, "
            "Configurações de controle IP; numa de 2017 a 2024, Geral, Dispositivos externos, "
            "Ligar via Wi-Fi (Mobile TV On). Prefira cabo a Wi-Fi.\n"
            "3. Anote o MAC da TV (Rede, Status da rede) no campo MAC; a varredura de rede também "
            "o mostra.\n"
            "4. Salve, aperte Parear e aceite a caixa na tela da TV."
        ),
        "campo_mac": (
            "MAC da TV, que é o que a liga. Habilite Wake on LAN em Suporte, Configurações de "
            "controle IP numa TV de 2025, ou Mobile TV On, Ligar via Wi-Fi em Geral numa de "
            "2017, e prefira cabo a Wi-Fi."
        ),
        "campo_chave_cliente": (
            "Chave que a TV entrega quando é pareada. Deixe vazio e pareie: ela só é pedida "
            "aqui para mudar de lugar uma instalação que já tem uma."
        ),
        "auth_ajuda": (
            "Ligue o LG Connect Apps nas configurações de rede da TV, ou a caixa de diálogo "
            "nunca aparece e isso fica igualzinho a uma TV apagada. Pareie, caminhe até a TV e "
            "aceite na tela, depois pareie de novo para colher a resposta."
        ),
        "cap_ligar": (
            "Ligar é um pacote Wake-on-LAN para o MAC do cadastro, e a TV leva alguns segundos "
            "para aceitar um comando depois dele, então uma cena espera entre os dois."
        ),
        "cap_volume": (
            "O nível é recusado enquanto o som sai na saída de linha ou numa caixa externa, que "
            "aceitam passo e não nível; as teclas seguem funcionando."
        ),
        "cap_fonte": (
            "O valor é o id de uma entrada física da TV, HDMI_1 e parecidos, que a TV lista "
            "sozinha e o painel mostra nas entradas do equipamento."
        ),
        "cap_atalho": (
            "O valor é o id de um aplicativo da TV, netflix ou com.webos.app.livetv para os "
            "canais abertos, da lista de aplicativos que a TV publica."
        ),
        "cap_modo": (
            "O valor é uma saída de som deste modelo, external_arc para uma soundbar; a que a "
            "TV está usando agora aparece no modo do equipamento."
        ),
        "cap_comando_extra": (
            "Dois nomes, e mais nenhum: tela_desligada apaga a tela e mantém o som, e "
            "tela_ligada a traz de volta. Um modelo que não os tem recusa uma vez e nunca mais "
            "é perguntado."
        ),
        "lista_entradas": "O id de um conector de trás da TV, HDMI_1 e parecidos.",
        "lista_atalhos": (
            "O id de um aplicativo, netflix, youtube.leanback.v4, com.webos.app.livetv."
        ),
        "lista_modos": (
            "Uma saída de som deste modelo. A que o equipamento mostra como modo agora é a "
            "palavra que esta TV usa para as caixas em que ela está tocando."
        ),
    },
}


@dataclass(frozen=True)
class Entrada:
    """One physical input of the TV: the id a command takes, the label the TV wrote and the
    app that answers for it while it is showing.
    """

    codigo: str
    rotulo: str
    aplicativo: str


class _Falha(Exception):
    """A stable code on the way out of an exchange, so no exception escapes executar."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class LgWebos(Driver):
    """One LG webOS TV, paired by the dialog on its screen and commanded over its WebSocket."""

    # Only draws the power key when ligar and desligar are BOTH declared, and
    # half of a pair is no key at all: a TV with no power key is a house whose scene of
    # arriving cannot turn the television on. That is what the required MAC buys.
    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV LG (webOS)", "en": "LG TV (webOS)"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_ATALHO,
            ACAO_MODO,
            ACAO_TOCAR,
            ACAO_PAUSAR,
            ACAO_PARAR,
            ACAO_TECLA,
            ACAO_EXTRA,
        ),
        teclas=TECLAS,
        auth=Auth.POPUP_NO_APARELHO,
        # The TV announces a service of its own, which names it exactly, and the fragment
        # of the maker is what reads an answer that came for another search; a fragment this
        # short would also match a stranger, so the signature carries both and the exact one
        # wins, because the plan asks for a declared ST and only ever reads a fragment.
        descoberta=Descoberta(
            ssdp_st=("urn:lge-com:service:webos-second-screen:1",),
            ssdp_fabricantes=("lg",),
        ),
        config_campos=(
            Campo(CAMPO_MAC, TipoCampo.TEXTO, obrigatorio=True),
            Campo(CAMPO_CHAVE, TipoCampo.SEGREDO),
        ),
        textos=TEXTOS,
        marca="LG",
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._ws: ClientWebSocketResponse | None = None
        # The second socket costs a request and a connection, and a TV that only ever
        # appears in a scene of power and volume must never pay for it.
        self._entrada_remota: ClientWebSocketResponse | None = None
        self._registrado = False
        self._numero = 0
        self._porta: int | None = None
        self._identidade: str | None = None
        self._chave = ""
        self._despertar: asyncio.Task[None] | None = None
        self._fio = fio.Fio(log, self._id())
        self._falhas = 0
        # The poll and a command of a scene land together, and two exchanges at once on
        # the same socket read the answer of each other.
        self._trava = asyncio.Lock()
        self._pareamento: asyncio.Task[str] | None = None
        self._entradas: tuple[Entrada, ...] = ()
        self._aplicativos: dict[str, str] = {}
        self._saida = ""
        # A model without the screen commands answers the 404 of a function that does not
        # exist, and asking it again on every scene is a round trip that will never work.
        self._sem_suporte: set[str] = set()
        self._recusadas: set[str] = set()

    async def iniciar(self) -> None:
        """Opens the session and NOTHING else: registering is what shows the dialog."""
        await self._abrir()

    async def parar(self) -> None:
        espera, self._pareamento = self._pareamento, None
        if espera is not None and not espera.done():
            espera.cancel()
            await asyncio.gather(espera, return_exceptions=True)
        await self._descartar()
        sessao, self._sessao = self._sessao, None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The deviceUUID of the hello, which the TV answers before any pairing and
        which is the same uuid its SSDP announcement carries.
        """
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        sessao = ClientSession(timeout=ClientTimeout(total=None, connect=CONEXAO_S))
        try:
            for porta in PORTAS:
                identidade = await _perguntar_quem_e(sessao, endereco, porta)
                if identidade:
                    return identidade
        finally:
            await sessao.close()
        return None

    def chave_do_aparelho(self) -> str:
        """The client-key in force, which the TV may have rotated since the registration.

        Keeps this inside the daemon, and whoever persists a registration reads
        it from here; it never travels to the panel and never lands in the log.
        """
        return self._chave_atual()

    def credenciais_do_aparelho(self) -> dict[str, str]:
        """The client-key the TV handed over, so the pairing survives a restart."""
        chave = self._chave_atual()
        return {CAMPO_CHAVE: chave} if chave else {}

    async def autenticar(self) -> str:
        """Pareado, aguardando while the dialog is on the screen, or falhou."""
        espera = self._pareamento
        if espera is not None:
            if not espera.done():
                return AGUARDANDO
            self._pareamento = None
            return _colher(espera)
        try:
            return await self._parear()
        except _Falha as falha:
            log.warning("%s: pairing failed with %s", self._id(), falha.codigo)
            await self._descartar()
            return FALHOU

    async def atualizar(self) -> None:
        try:
            async with self._trava:
                await self._garantir()
                await self._ler_tudo()
        except _Falha as falha:
            # Only a socket that died is thrown away here. A poll that discarded on every
            # code would close the socket that is holding the dialog on the screen, and the
            # person walking to the television would find it gone.
            if falha.codigo == EQ_OFFLINE:
                await self._descartar()
            self._falhar(falha.codigo)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                await self._descartar()
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        # Waking a TV is a packet on the LAN and needs no pairing and no socket, so it is
        # the one action that works on a TV nobody has paired yet.
        if acao == ACAO_LIGAR:
            return await self._acordar()
        # TurnOff on a TV that is already off TURNS IT ON, so the scene of
        # leaving the house must never send it to a television that is already dark.
        if acao == ACAO_DESLIGAR and self.estado().ligado is False:
            log.debug("%s: the TV is already off, so turnOff is not sent", self._id())
            return None
        if acao == ACAO_EXTRA and isinstance(valor, str) and valor in self._sem_suporte:
            return NAO_SUPORTADO
        if not self._chave_atual():
            return AUTH_PENDENTE
        # Waking is for a television the last poll found OFF, and never for one whose
        # socket simply is not open yet: on the first action after a boot there is no socket
        # and the set is on, and a magic packet there would be a wake nobody asked for while
        # the ordinary path connects and sends. A state nobody read yet is None, not False.
        if acao in ACOES_QUE_ACORDAM and self.estado().ligado is False and not self._no_ar():
            # A value the TV would refuse is refused HERE, before a packet leaves this
            # host. Waking a television to then discover the word was never sendable spends a
            # minute of the customer and a screen turning on for nothing.
            if not isinstance(valor, str) or not _NO_FIO.fullmatch(valor.strip()):
                return INVALID_VALUE
            return self._acordar_e_depois(acao, valor)
        async with self._trava:
            await self._garantir()
            return await self._comandar(acao, valor)

    def _no_ar(self) -> bool:
        ws = self._ws
        return ws is not None and not ws.closed and self._registrado

    def _acordar_e_depois(self, acao: str, valor: object) -> str | None:
        """Wakes the TV and does the thing when it answers, which is what the remote does.

        The deadline of one action is half a poll interval and a television takes longer
        than that to boot, so waiting inside the call would answer a timeout on the one
        automation the customer wants most: the set turning on already showing the app. The
        packet leaves now, the wait is a task, and a second press while that task is alive is
        not a second wake.
        """
        pacote = _pacote_magico(self.cadastro.campos.get(CAMPO_MAC, ""))
        if pacote is None:
            # With no MAC there is nothing to wake with, and the TV really is offline.
            return EQ_OFFLINE
        espera = self._despertar
        if espera is not None and not espera.done():
            return None
        try:
            _soprar(pacote, self._talvez_endereco())
        except OSError as erro:
            log.warning("%s: the magic packet did not leave: %s", self._id(), erro)
            return EQ_OFFLINE
        self._despertar = asyncio.create_task(self._quando_acordar(acao, valor))
        return None

    def _talvez_endereco(self) -> str:
        """The address of the registration when it is a literal, for the directed broadcast."""
        return ip_literal(self.cadastro.ip) or ""

    async def _quando_acordar(self, acao: str, valor: object) -> None:
        """Waits for the television to answer and then does what was asked of it, blowing the
        magic packet again at every step.

        Measured at the bench, a set that has JUST been turned off ignores the packet for
        the first half minute, and one sent only at the start of the wait is a packet the
        television was never awake to hear. Blowing it again on every step costs three
        datagrams and turns "it did not wake" into "it is not listening at all", which is a
        setting of the television and not a doubt about this driver.
        """
        pacote = _pacote_magico(self.cadastro.campos.get(CAMPO_MAC, ""))
        fim = asyncio.get_running_loop().time() + ESPERA_ATE_ACORDAR_S
        while asyncio.get_running_loop().time() < fim:
            await asyncio.sleep(PASSO_DE_ESPERA_S)
            if pacote is not None:
                with suppress(OSError):
                    _soprar(pacote, self._talvez_endereco())
            try:
                async with self._trava:
                    await self._garantir()
                    codigo = await self._comandar(acao, valor)
            except _Falha:
                continue
            if codigo is not None:
                log.warning("%s: %s after waking answered %s", self._id(), acao, codigo)
            else:
                log.info("%s: %s done after waking the TV", self._id(), acao)
            return
        log.warning(
            "%s: the TV did not answer within %.0fs of the magic packet, so %s was not sent",
            self._id(),
            ESPERA_ATE_ACORDAR_S,
            acao,
        )

    async def _comandar(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_DESLIGAR:
            # A TV that is shutting down answers the turnOff unreliably, and
            # waiting for an answer spends the whole deadline on every power off.
            await self._mandar(URI_DESLIGAR, {})
            self._defina(ligado=False)
            return None
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._com_valor(URI_TROCAR_ENTRADA, "inputId", valor, fonte=True)
        if acao == ACAO_ATALHO:
            return await self._com_valor(URI_ABRIR, CHAVE_ID, valor)
        if acao == ACAO_MODO:
            return await self._com_valor(URI_TROCAR_SAIDA, "output", valor, modo=True)
        if acao in TRANSPORTES:
            await self._pedir(TRANSPORTES[acao], {}, RESPOSTA_S)
            return None
        if acao == ACAO_TECLA:
            return await self._tecla(valor)
        if acao == ACAO_EXTRA:
            return await self._extra(valor)
        return await super().executar(acao, valor)

    async def _trocar_volume(self, valor: object) -> str | None:
        if type(valor) is not int or not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
            return INVALID_VALUE
        if self._saida == SAIDA_SEM_VOLUME or self._saida in SAIDAS_SEM_NIVEL:
            # The TV takes this command and does nothing with it, so answering the code of
            # what it cannot do as it stands is the honest answer and the panel says so.
            return NAO_SUPORTADO
        await self._pedir(URI_DEFINIR_VOLUME, {CHAVE_VOLUME: valor}, RESPOSTA_S)
        self._defina(volume=valor)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        if not isinstance(valor, bool):
            return INVALID_VALUE
        if self._saida == SAIDA_SEM_VOLUME:
            return NAO_SUPORTADO
        await self._pedir(URI_MUDO, {CHAVE_MUDO: valor}, RESPOSTA_S)
        self._defina(mudo=valor)
        return None

    async def _com_valor(
        self, uri: str, campo: str, valor: object, *, fonte: bool = False, modo: bool = False
    ) -> str | None:
        """One word of the TV inside the field the endpoint reads it from."""
        if not isinstance(valor, str) or not _NO_FIO.fullmatch(valor.strip()):
            return INVALID_VALUE
        palavra = valor.strip()
        await self._pedir(uri, {campo: palavra}, RESPOSTA_S)
        if fonte:
            self._defina(fonte=palavra)
        if modo:
            self._saida = palavra
            self._defina(modo=palavra)
        return None

    async def _tecla(self, valor: object) -> str | None:
        """A key, by the endpoint of the main socket when it has one and by the
        remote socket of the TV when it does not.
        """
        if not isinstance(valor, str):
            return INVALID_VALUE
        uri = TECLAS_COM_ENDPOINT.get(valor)
        if uri is not None:
            await self._pedir(uri, {}, RESPOSTA_S)
            return None
        nome = NOMES_DE_TECLA.get(valor)
        if nome is None:
            return NAO_SUPORTADO
        ws = await self._socket_de_entrada()
        try:
            await ws.send_str(BOTAO.format(nome=nome))
        except (ClientError, OSError, RuntimeError) as erro:
            self._entrada_remota = None
            raise _Falha(EQ_OFFLINE) from erro
        return None

    async def _extra(self, valor: object) -> str | None:
        uri = EXTRAS.get(valor) if isinstance(valor, str) else None
        if uri is None:
            return INVALID_VALUE
        try:
            await self._pedir(uri, {}, RESPOSTA_S)
        except _Falha as falha:
            if falha.codigo != NAO_SUPORTADO:
                raise
            # This model does not have the function and never will, so it is asked once
            # and the answer is remembered instead of a round trip per scene.
            self._sem_suporte.add(valor)
            log.warning("%s: this model has no %s", self._id(), uri)
            return NAO_SUPORTADO
        return None

    async def _acordar(self) -> str | None:
        """The TV answers nothing over IP while it is off, so it is woken by the
        magic packet, and the socket that is already open takes the turnOn as well.
        """
        pacote = _pacote_magico(self.cadastro.campos.get(CAMPO_MAC, ""))
        if pacote is None:
            log.warning("%s: the registration carries no readable MAC to wake the TV", self._id())
            return INVALID_VALUE
        try:
            for numero in range(ENVIOS_WOL):
                if numero:
                    # A switch that has just learned the port drops the first broadcast,
                    # and three packets spaced out cost nothing on a LAN.
                    await asyncio.sleep(INTERVALO_WOL_S)
                _soprar(pacote, self._talvez_endereco())
        except OSError as erro:
            log.warning("%s: the magic packet did not leave the host: %s", self._id(), erro)
            return EQ_OFFLINE
        if self._registrado:
            # A TV in the active standby of Quick Start+ keeps the socket alive, and there
            # the turnOn is what actually lights the screen. It goes under the lock like every
            # other exchange, or it would cross ids with the poll of the same second.
            async with self._trava:
                with suppress(_Falha):
                    await self._pedir(URI_LIGAR, {}, RESPOSTA_S)
        self._defina(ligado=True)
        return None

    async def _ler_tudo(self) -> None:
        """One poll: eight questions inside the budget, and one state published at the end."""
        prazo_final = asyncio.get_running_loop().time() + ORCAMENTO_DO_POLL_S
        aplicativo = _texto(_de(await self._talvez(URI_APLICATIVO, prazo_final), CHAVE_APLICATIVO))
        energia = await self._talvez(URI_ENERGIA, prazo_final)
        # The sound output rides INSIDE the answer about the volume, which this poll asks
        # for anyway, and the endpoint of its own is a 404 on this firmware: measured on a
        # webOS 6.5.3, com.webos.service.apiadapter/audio/getSoundOutput does not exist, so the
        # driver never learned the set was on ARC, published a level for a television that has
        # none, and the panel drew a bar whose every drag came back an error. Asking the
        # dedicated endpoint after, and only as a fallback, keeps a firmware that has it. This
        # is the guard that refuses a level the TV would swallow in silence, so a question that
        # failed or that the budget skipped must not open it; like the two lists below, the
        # last word of the TV is kept until the TV says another one.
        audio = await self._talvez(URI_VOLUME, prazo_final)
        saida = _saida_de(audio)
        if not saida:
            saida = _texto(_de(await self._talvez(URI_SAIDA, prazo_final), CHAVE_SAIDA))
        self._saida = saida or self._saida
        # Both lists come back EMPTY while the TV is off, and a driver that
        # zeroed them each poll would wipe the lists of the panel every night.
        lidas = _entradas_de(await self._talvez(URI_ENTRADAS, prazo_final))
        self._entradas = lidas or self._entradas
        achados = _aplicativos_de(await self._talvez(URI_APLICATIVOS, prazo_final))
        self._aplicativos = achados or self._aplicativos
        # A TV with no level of its own must publish none, because a number the panel
        # draws as a bar is a promise that dragging it moves something, and dragging it on a
        # television whose sound belongs to a receiver only ever returns an error.
        sem_volume = self._saida == SAIDA_SEM_VOLUME
        sem_nivel = sem_volume or self._saida in SAIDAS_SEM_NIVEL
        volume = None if sem_nivel else _volume_de(audio)
        mudo = None if sem_volume else _mudo_de(await self._talvez(URI_AUDIO, prazo_final))
        reproduzindo = _reproduzindo_de(await self._talvez(URI_MIDIA, prazo_final))
        # The title is one more question, so the counter is only cleared once every
        # question of the poll is in. Clearing it before the last one would zero it at each
        # poll of a TV whose failure lives exactly there, the ceiling would never be reached
        # and a dead socket would stay published as a TV that is on.
        tocando = await self._tocando(aplicativo, prazo_final)
        self._falhas = 0
        self._defina(
            online=True,
            ligado=_ligado_de(energia, aplicativo),
            volume=volume,
            mudo=mudo,
            fonte=_fonte_de(self._entradas, aplicativo),
            fontes=tuple(entrada.codigo for entrada in self._entradas),
            # The apps of THIS television, which it already lists at every poll to name
            # what is on the screen; publishing them is what lets the panel fill the shortcuts
            # with Prime Video, Disney+ or whatever this set actually has, with the id the
            # television itself uses, instead of the three guesses a manifest can carry.
            atalhos=tuple(sorted(self._aplicativos.items(), key=lambda par: par[1] or par[0])),
            reproduzindo=reproduzindo,
            tocando=tocando,
            modo=self._saida or None,
            detalhe="",
        )

    async def _tocando(self, aplicativo: str, prazo_final: float) -> str | None:
        """The title of what is on the screen: the channel while the TV is on the open
        channels, and the name of the app everywhere else.
        """
        if not aplicativo:
            return None
        if aplicativo == APP_TV_ABERTA:
            canal = await self._talvez(URI_CANAL, prazo_final)
            return _texto(_de(canal, CHAVE_CANAL)) or None
        return self._aplicativos.get(aplicativo) or None

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        # A TV that came back may be another one on the same lease, and the port that
        # answered belonged to the firmware of the one that left. The uuid learned is NOT
        # forgotten here: forgetting it would let whatever now holds the address be adopted
        # under the name of this equipment, and a legitimate move of address arrives as a new
        # registration, which builds a driver of its own.
        self._porta = None
        # The WebSocket server of the TV dies with the screen, so a connection that is
        # refused IS the power state, and the lists are kept because they are what the panel
        # draws the equipment with while it is dark.
        self._defina(
            online=False,
            ligado=False,
            volume=None,
            mudo=None,
            fonte=None,
            reproduzindo=None,
            tocando=None,
            modo=None,
            detalhe=codigo,
        )

    async def _parear(self) -> str:
        """The register, which shows the dialog when there is no key that the TV still honours."""
        async with self._trava:
            await self._descartar()
            ws = await self._conectar()
            await self._apresentar(ws)
            documento = await _registrar(ws, self._chave_atual(), RESPOSTA_S)
            tipo = documento.get("type")
            if tipo == TIPO_REGISTRADO:
                self._guardar_chave(documento)
                self._registrado = True
                return PAREADO
            pediu_a_tela = _de(documento.get("payload"), CHAVE_TIPO_DE_PAREAMENTO)
            if tipo == TIPO_RESPOSTA and pediu_a_tela == PAREAMENTO_POR_DIALOGO:
                # The dialog is on the screen NOW and dies with the socket, so the wait
                # goes to a task and this call answers at once; nothing else touches the socket
                # meanwhile, because _garantir refuses while this task is alive.
                self._pareamento = asyncio.create_task(self._esperar_a_tela(ws))
                return AGUARDANDO
        log.warning("%s: the TV answered %r to the register", self._id(), tipo)
        await self._descartar()
        return FALHOU

    async def _esperar_a_tela(self, ws: ClientWebSocketResponse) -> str:
        """Waits for the person to accept on the TV, which no ten second deadline covers."""
        try:
            async with asyncio.timeout(PAREAMENTO_S):
                while True:
                    documento = await _receber(ws, PAREAMENTO_S)
                    if documento.get("id") != ID_REGISTRO:
                        continue
                    if documento.get("type") != TIPO_REGISTRADO:
                        log.warning("%s: the dialog on the TV was refused", self._id())
                        await self._descartar()
                        return FALHOU
                    self._guardar_chave(documento)
                    self._registrado = True
                    return PAREADO
        except (TimeoutError, _Falha) as erro:
            log.warning("%s: the dialog on the TV was not accepted: %s", self._id(), erro)
            await self._descartar()
            return FALHOU

    async def _garantir(self) -> None:
        """The socket open and registered, or a stable code saying why it is not."""
        espera = self._pareamento
        if espera is not None and not espera.done():
            # The dialog on the screen dies with the socket, so a poll must not take it
            # away from the person walking to the television.
            raise _Falha(AUTH_PENDENTE)
        ws = self._ws
        if ws is not None and not ws.closed and self._registrado:
            return
        await self._descartar()
        chave = self._chave_atual()
        if not chave:
            # Registering with no key shows the dialog on the screen, and says
            # pairing is explicit and never a side effect of a poll or of a command.
            raise _Falha(AUTH_PENDENTE)
        ws = await self._conectar()
        await self._apresentar(ws)
        documento = await _registrar(ws, chave, RESPOSTA_S)
        if documento.get("type") != TIPO_REGISTRADO:
            # A TV that asks for the dialog again does not honour this key any more, and
            # leaving the socket open would leave a dialog on a screen nobody is looking at.
            await self._descartar()
            raise _Falha(AUTH_PENDENTE)
        self._guardar_chave(documento)
        self._registrado = True
        # This question is the one most likely to fail on the new handshake,
        # so it lives here, after the register, and its failure is not a failure to connect.
        with suppress(_Falha):
            versao = await self._pedir(URI_FIRMWARE, {}, RESPOSTA_S)
            log.info("%s: firmware %s", self._id(), _versao_de(versao))

    async def _apresentar(self, ws: ClientWebSocketResponse) -> None:
        """The hello, which names the TV, and the system info a new firmware asks for BEFORE
        the register.
        """
        documento = await _ola(ws, RESPOSTA_S)
        identidade = _texto(_de(documento.get("payload"), CHAVE_UUID))
        # The identity is the uuid and the address is only where it answered
        # today; without this line a lease that moved leaves the hub commanding whatever TV now
        # holds the address, under the name of this equipment.
        if identidade and self._identidade and identidade != self._identidade:
            raise _Falha(EQ_OFFLINE)
        if identidade:
            self._identidade = identidade
        # A new firmware refuses to register without this question and an old
        # one may fail it; a driver that treated the failure as fatal would never connect.
        with suppress(_Falha):
            sistema = await _requisitar(ws, ID_SISTEMA, URI_SISTEMA, {}, RESPOSTA_S)
            log.info("%s: model %s", self._id(), _texto(sistema.get(CHAVE_MODELO)) or "unknown")

    async def _conectar(self) -> ClientWebSocketResponse:
        """The socket on the port this firmware answers, remembered for the next time."""
        endereco = self._endereco()
        sessao = await self._abrir()
        portas = (self._porta,) if self._porta is not None else PORTAS
        for porta in portas:
            try:
                # The deadline of the client covers reaching the socket and not the
                # upgrade that follows, so a stranger holding this port open and saying
                # nothing would hold this driver for as long as it felt like.
                async with asyncio.timeout(CONEXAO_S):
                    ws = await sessao.ws_connect(
                        _url(endereco, porta),
                        ssl=SEM_CERTIFICADO,
                        heartbeat=BATIMENTO_S,
                        max_msg_size=MENSAGEM_MAXIMA,
                        timeout=ClientWSTimeout(ws_receive=None, ws_close=FECHAMENTO_S),
                    )
            except (ClientError, OSError, TimeoutError) as erro:
                log.debug("%s: port %d refused the socket: %s", self._id(), porta, erro)
                continue
            self._porta = porta
            self._ws = ws
            return ws
        self._porta = None
        raise _Falha(EQ_OFFLINE)

    async def _socket_de_entrada(self) -> ClientWebSocketResponse:
        """The second socket, the one the navigation keys need, opened on the first key."""
        ws = self._entrada_remota
        if ws is not None and not ws.closed:
            return ws
        carga = await self._pedir(URI_PONTEIRO, {}, RESPOSTA_S)
        url = _url_de_entrada(carga.get(CHAVE_SOCKET), self._endereco())
        if url is None:
            # The address of this socket comes from the device, and following
            # it wherever it points would make the hub a proxy into the LAN of the customer.
            log.warning("%s: the TV named a remote socket outside its own address", self._id())
            raise _Falha(ERRO_APARELHO)
        sessao = await self._abrir()
        try:
            # The deadline of the client covers reaching the socket and not the upgrade
            # that follows, and THIS address was dictated by the device; whatever answers on
            # the port it named would otherwise hold the key for as long as it felt like.
            async with asyncio.timeout(CONEXAO_S):
                ws = await sessao.ws_connect(
                    url,
                    ssl=SEM_CERTIFICADO,
                    heartbeat=BATIMENTO_S,
                    max_msg_size=MENSAGEM_DE_ENTRADA_MAXIMA,
                    timeout=ClientWSTimeout(ws_receive=None, ws_close=FECHAMENTO_S),
                )
        except (ClientError, OSError, TimeoutError) as erro:
            raise _Falha(EQ_OFFLINE) from erro
        self._entrada_remota = ws
        return ws

    async def _pedir(self, uri: str, carga: dict, prazo_s: float) -> dict:
        """One request and its answer, matched by the id it went out with."""
        ws = self._ws
        if ws is None or ws.closed:
            raise _Falha(EQ_OFFLINE)
        self._numero += 1
        # The poll asks the same eight things every ten seconds; the transcript writes
        # them once after a connection and every command always, with the payload redacted.
        rotina = uri in PERGUNTAS_DE_ROTINA
        pedido = f"request {uri} {fio.redigir(carga)}" if carga else f"request {uri}"
        self._fio.enviado(pedido, rotina=rotina)
        try:
            documento = await _requisitar(ws, self._numero, uri, carga, prazo_s)
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                self._fio.falhou(pedido, falha.codigo)
            else:
                self._fio.recusado(pedido, falha.codigo)
            raise
        self._fio.recebido(documento, rotina=rotina)
        return documento

    async def _mandar(self, uri: str, carga: dict) -> None:
        """A command nothing waits an answer for."""
        ws = self._ws
        if ws is None or ws.closed:
            raise _Falha(EQ_OFFLINE)
        self._numero += 1
        self._fio.enviado(f"request {uri} {fio.redigir(carga)}" if carga else f"request {uri}")
        await _enviar(ws, _pedido(self._numero, uri, carga))

    async def _talvez(self, uri: str, prazo_final: float) -> dict | None:
        """One question of the poll: a TV that refuses it answers None, and a socket that died
        stops the poll.
        """
        # A model that does not have an endpoint does not grow one, so asking it again
        # every ten seconds spends budget the poll needs for the questions this TV does answer,
        # and writes the same refusal into the log forever. Measured on a webOS 6.5.3, which
        # has no com.webos.media/getForegroundAppInfo at all. The set dies with the socket,
        # because a firmware update is a new television as far as this driver knows.
        if uri in self._recusadas:
            return None
        comecou = asyncio.get_running_loop().time()
        restante = prazo_final - comecou
        if restante <= 0:
            log.debug("%s: the poll ran out of budget before %s", self._id(), uri)
            return None
        prazo_s = min(RESPOSTA_S, restante)
        try:
            return await self._pedir(uri, {}, prazo_s)
        except _Falha as falha:
            if falha.codigo == EQ_OFFLINE:
                # The budget of the poll and the deadline of the TV are two different
                # reasons to run out of time, and only the second is a TV that went away. A TV
                # that answers every question, just slowly, spends the budget in the middle of
                # one of them, and reading that as offline would throw the socket away and
                # publish no state at all for a television that is answering.
                gastou = asyncio.get_running_loop().time() - comecou
                if prazo_s < RESPOSTA_S and gastou >= prazo_s:
                    log.debug("%s: the poll ran out of budget on %s", self._id(), uri)
                    return None
                raise
            if falha.codigo == NAO_SUPORTADO:
                log.info("%s: this model has no %s, so it is not asked again", self._id(), uri)
                self._recusadas.add(uri)
                return None
            log.debug("%s: the TV answered %s to %s", self._id(), falha.codigo, uri)
            return None

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            # A total deadline would kill the socket in the middle of the day, because a
            # WebSocket is one request that lives as long as the TV does; the connect deadline
            # is the one that matters and every read here carries its own.
            sessao = ClientSession(timeout=ClientTimeout(total=None, connect=CONEXAO_S))
            self._sessao = sessao
        return sessao

    async def _descartar(self) -> None:
        self._registrado = False
        self._recusadas.clear()
        for ws in (self._entrada_remota, self._ws):
            if ws is not None and not ws.closed:
                with suppress(ClientError, OSError, TimeoutError):
                    await ws.close()
        self._entrada_remota = None
        self._ws = None

    def _guardar_chave(self, documento: dict) -> None:
        """The TV rotates the key, and a driver that ignored the rotation would
        lose the pairing by itself on the next firmware update.
        """
        chave = _texto(_de(documento.get("payload"), CHAVE_CLIENTE))
        if not chave or chave == self._chave_atual():
            return
        # A secret of a device never lands in the log, so what is written here
        # is that it changed and never what it is.
        log.info("%s: the TV handed over a new client key", self._id())
        self._chave = chave

    def _chave_atual(self) -> str:
        """The key in force: the one the TV handed over wins over the one of the registration."""
        return self._chave or _texto(self.cadastro.segredos.get(CAMPO_CHAVE))

    def _endereco(self) -> str:
        """Only an IP literal reaches a device, so the hub is never a resolver."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco

    def _id(self) -> str:
        return self.cadastro.identidade


def _pedido(identificador: object, uri: str, carga: dict) -> dict:
    return {"id": identificador, "type": TIPO_PEDIDO, "uri": PREFIXO + uri, "payload": carga}


async def _requisitar(
    ws: ClientWebSocketResponse, identificador: object, uri: str, carga: dict, prazo_s: float
) -> dict:
    return _carga(await _trocar(ws, _pedido(identificador, uri, carga), prazo_s))


async def _registrar(ws: ClientWebSocketResponse, chave: str, prazo_s: float) -> dict:
    """The register, whose first answer is the dialog on the screen or the key itself."""
    mensagem = {
        "type": TIPO_REGISTRO,
        "id": ID_REGISTRO,
        "payload": {
            "forcePairing": False,
            "pairingType": PAREAMENTO_POR_DIALOGO,
            CHAVE_CLIENTE: chave or None,
            "manifest": {
                "appVersion": VERSAO_DO_APLICATIVO,
                "manifestVersion": VERSAO_DO_MANIFESTO,
                "permissions": list(PERMISSOES),
            },
        },
    }
    return await _trocar(ws, mensagem, prazo_s)


async def _ola(ws: ClientWebSocketResponse, prazo_s: float) -> dict:
    """The hello, whose answer is matched by TYPE, because the TV does not echo the id.

    Measured on a webOS 6.5.3 at the bench, the answer to the hello is
    {"type":"hello","payload":{...}} with NO id in it, while every other answer of the
    protocol carries the id of the question. Matched by id, this exchange read the answer,
    threw it away as somebody else's, and waited out the deadline: pairing failed in two
    seconds with eq_offline and nothing ever appeared on the television, and identificar
    answered nothing, so the sweep never found a TV that was right there. A firmware that
    does echo the id still matches, because either one is accepted.
    """
    await _enviar(ws, {"id": ID_OLA, "type": TIPO_OLA, "payload": {}})
    for _ in range(MENSAGENS_ATE_DESISTIR):
        documento = await _receber(ws, prazo_s)
        if documento.get("type") == TIPO_OLA or documento.get("id") == ID_OLA:
            return documento
    raise _Falha(ERRO_APARELHO)


async def _trocar(ws: ClientWebSocketResponse, mensagem: dict, prazo_s: float) -> dict:
    """Sends one message and reads until the answer that carries its id."""
    await _enviar(ws, mensagem)
    identificador = mensagem["id"]
    for _ in range(MENSAGENS_ATE_DESISTIR):
        documento = await _receber(ws, prazo_s)
        if documento.get("id") == identificador:
            return documento
    raise _Falha(ERRO_APARELHO)


async def _enviar(ws: ClientWebSocketResponse, mensagem: dict) -> None:
    try:
        await ws.send_str(json.dumps(mensagem))
    except (ClientError, OSError, RuntimeError, ValueError, TypeError) as erro:
        raise _Falha(EQ_OFFLINE) from erro


async def _receber(ws: ClientWebSocketResponse, prazo_s: float) -> dict:
    """One JSON message of the TV, or a stable code for a socket that is not answering."""
    try:
        async with asyncio.timeout(prazo_s):
            while True:
                mensagem = await ws.receive()
                if mensagem.type is WSMsgType.TEXT:
                    break
                if mensagem.type in (WSMsgType.BINARY, WSMsgType.PING, WSMsgType.PONG):
                    continue
                raise _Falha(EQ_OFFLINE)
    except TimeoutError as erro:
        raise _Falha(EQ_OFFLINE) from erro
    except (ClientError, OSError) as erro:
        raise _Falha(EQ_OFFLINE) from erro
    try:
        documento = json.loads(mensagem.data)
    except (ValueError, RecursionError) as erro:
        raise _Falha(ERRO_APARELHO) from erro
    if not isinstance(documento, dict):
        raise _Falha(ERRO_APARELHO)
    return documento


def _carga(documento: dict) -> dict:
    """The payload of a valid answer, or the stable code the answer deserves."""
    if documento.get("type") == TIPO_ERRO:
        erro = _texto(documento.get(CHAVE_ERRO)).lower()
        # This one error means the model does not have the function, and
        # every other error of a TV is a device that failed.
        raise _Falha(NAO_SUPORTADO if ERRO_NAO_EXISTE in erro else ERRO_APARELHO)
    carga = documento.get("payload")
    if not isinstance(carga, dict):
        raise _Falha(ERRO_APARELHO)
    if carga.get(CHAVE_RETORNO) or carga.get(CHAVE_ASSINADO) or carga.get(CHAVE_ASSINATURA):
        return carga
    raise _Falha(ERRO_APARELHO)


async def _perguntar_quem_e(sessao: ClientSession, endereco: str, porta: int) -> str:
    """The deviceUUID of the TV at that address on that port, or an empty string."""
    try:
        # The sweep asks this of every address that answered, and one of them accepting
        # the socket and going quiet must not hold the whole sweep.
        async with asyncio.timeout(CONEXAO_S + RESPOSTA_S):
            async with sessao.ws_connect(
                _url(endereco, porta),
                ssl=SEM_CERTIFICADO,
                max_msg_size=MENSAGEM_MAXIMA,
                timeout=ClientWSTimeout(ws_receive=None, ws_close=FECHAMENTO_S),
            ) as ws:
                documento = await _ola(ws, RESPOSTA_S)
                return _texto(_de(documento.get("payload"), CHAVE_UUID))
    except (ClientError, OSError, TimeoutError, _Falha):
        return ""


def _url(endereco: str, porta: int) -> str:
    """The clear socket on 3000 and the TLS one on 3001, which is the only
    difference between the two doors of the same protocol.
    """
    esquema = ESQUEMA_SEGURO if porta == PORTA_SEGURA else ESQUEMA
    return f"{esquema}://{_hospedeiro(endereco)}:{porta}"


def _url_de_entrada(bruto: object, endereco: str) -> str | None:
    """The address of the remote socket, accepted only when it points at this same TV."""
    if not isinstance(bruto, str) or not 0 < len(bruto) <= URL_MAXIMA:
        return None
    try:
        partes = urlsplit(bruto)
        porta = partes.port
    except ValueError:
        return None
    if partes.scheme not in (ESQUEMA, ESQUEMA_SEGURO) or porta is None:
        return None
    if ip_literal(partes.hostname) != endereco:
        return None
    return bruto


def _hospedeiro(endereco: str) -> str:
    """The address as the HOST of a URL, where an IPv6 lives inside brackets."""
    return f"[{endereco}]" if ":" in endereco else endereco


def _pacote_magico(mac: object) -> bytes | None:
    """The Wake-on-LAN frame of a MAC written in any of the usual ways."""
    if not isinstance(mac, str):
        return None
    limpo = _SEPARADOR_DE_MAC.sub("", mac.strip())
    if not _MAC.fullmatch(limpo):
        return None
    return b"\xff" * 6 + bytes.fromhex(limpo) * 16


def _soprar(pacote: bytes, endereco: str = "") -> None:
    """The magic packet on every address that can carry it to the set, on both ports.

    The limited broadcast is what a daemon on the network of the installation uses, and it
    is enough there. The directed broadcast of the subnet of the equipment is a routable
    address, so it also crosses a hub that lives behind a bridge, and the unicast reaches a set
    whose entry the switch still remembers. Sending all three costs three datagrams and removes
    the whole class of "the packet never left this network" from the diagnosis.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as fio:
        fio.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        for destino in _destinos_do_wol(endereco):
            for porta in PORTAS_WOL:
                with suppress(OSError):
                    fio.sendto(pacote, (destino, porta))


def _destinos_do_wol(endereco: str) -> tuple[str, ...]:
    """The limited broadcast, the directed broadcast of that address, and the address itself."""
    destinos = [ENDERECO_WOL]
    if endereco:
        with suppress(ValueError):
            # /24 and not the real mask, because a hub behind a bridge cannot know the
            # mask of a network it is not on, and a home segment is a /24 in practice.
            rede = ipaddress.ip_network(f"{endereco}/24", strict=False)
            destinos.append(str(rede.broadcast_address))
            destinos.append(endereco)
    return tuple(destinos)


def _colher(espera: asyncio.Task[str]) -> str:
    """The answer of the wait for the dialog, and falhou for anything that is not one."""
    if espera.cancelled():
        return FALHOU
    erro = espera.exception()
    if erro is not None:
        log.warning("the wait for the dialog on the TV ended in %r", erro)
        return FALHOU
    resultado = espera.result()
    return resultado if resultado in RESULTADOS else FALHOU


def _ligado_de(energia: dict | None, aplicativo: str) -> bool | None:
    """The TV is on unless the power state is one of the sleeping ones, and a
    firmware that has no power state is read by the app that is on the screen.
    """
    estado = _texto(_de(energia, CHAVE_ESTADO_DE_ENERGIA)).lower()
    if estado:
        return estado not in ENERGIAS_APAGADAS
    return True if aplicativo else None


def _saida_de(audio: dict | None) -> str:
    """The sound output as it rides inside the answer about the volume.

    It is nested under volumeStatus on some firmwares and loose on others, exactly like
    the level beside it, and the endpoint of its own does not exist on every model.
    """
    if audio is None:
        return ""
    aninhado = audio.get(CHAVE_ESTADO_DE_VOLUME)
    fonte = aninhado if isinstance(aninhado, dict) else audio
    return _texto(fonte.get(CHAVE_SAIDA))


def _volume_de(audio: dict | None) -> int | None:
    """The level is nested under volumeStatus on some firmwares and loose on
    others, and reading only one of the two breaks on half of the TVs.
    """
    if audio is None:
        return None
    aninhado = audio.get(CHAVE_ESTADO_DE_VOLUME)
    fonte = aninhado if isinstance(aninhado, dict) else audio
    numero = fonte.get(CHAVE_VOLUME)
    if isinstance(numero, bool) or not isinstance(numero, int):
        return None
    # The scale of the TV is already the 0 to 100, so there is no conversion
    # here and the clamp is only against a firmware that answered outside its own scale.
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, numero))


def _versao_de(firmware: dict | None) -> str:
    """The firmware of the TV, which goes to the log because Estado.detalhe
    carries a stable code and never a line to read.
    """
    maior = _texto(_de(firmware, CHAVE_MAIOR))
    menor = _texto(_de(firmware, CHAVE_MENOR))
    return f"{maior}.{menor}" if maior else "unknown"


def _mudo_de(audio: dict | None) -> bool | None:
    mudo = _de(audio, CHAVE_MUDO)
    return mudo if isinstance(mudo, bool) else None


def _entradas_de(lista: dict | None) -> tuple[Entrada, ...]:
    """The physical inputs the TV lists, which change with what is plugged into it."""
    brutas = _de(lista, CHAVE_ENTRADAS)
    if not isinstance(brutas, list):
        return ()
    achadas = []
    for item in brutas[:ENTRADAS_MAXIMO]:
        if not isinstance(item, dict):
            continue
        codigo = _texto(item.get(CHAVE_ID))
        if codigo:
            achadas.append(
                Entrada(codigo, _texto(item.get(CHAVE_ROTULO)), _texto(item.get(CHAVE_APLICATIVO)))
            )
    return tuple(achadas)


def _aplicativos_de(lista: dict | None) -> dict[str, str]:
    """The apps of the TV by id, which is how the title of what is showing is found."""
    brutos = _de(lista, CHAVE_APLICATIVOS)
    if not isinstance(brutos, list):
        return {}
    achados = {}
    for item in brutos[:APLICATIVOS_MAXIMO]:
        if not isinstance(item, dict):
            continue
        codigo = _texto(item.get(CHAVE_ID))
        if codigo:
            achados[codigo] = _texto(item.get(CHAVE_TITULO))
    return achados


def _fonte_de(entradas: tuple[Entrada, ...], aplicativo: str) -> str | None:
    """The input that is on the screen, or None while the TV is inside an app."""
    if not aplicativo:
        return None
    for entrada in entradas:
        if entrada.aplicativo and entrada.aplicativo == aplicativo:
            return entrada.codigo
    return None


def _reproduzindo_de(midia: dict | None) -> bool | None:
    """The transport is a different fact from the title, and only some apps publish
    it, so a TV that does not say leaves it None.
    """
    brutos = _de(midia, CHAVE_MIDIA)
    if not isinstance(brutos, list):
        return None
    estados = [
        _texto(item.get(CHAVE_TRANSPORTE)).lower() for item in brutos if isinstance(item, dict)
    ]
    if TRANSPORTE_TOCANDO in estados:
        return True
    if TRANSPORTE_PAUSADO in estados:
        return False
    return None


def _de(documento: object, chave: str) -> object:
    return documento.get(chave) if isinstance(documento, dict) else None


def _texto(bruto: object) -> str:
    """What the TV wrote, cleaned and capped, exactly as it wrote it."""
    if isinstance(bruto, bool) or bruto is None:
        return ""
    return _CONTROLE.sub("", str(bruto).strip())[:TEXTO_MAXIMO]

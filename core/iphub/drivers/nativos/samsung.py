# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Samsung TV of the Tizen generation, 2016 and later.

This TV answers on three wires at once, and each one exists because the other two cannot do
that job:

- a WebSocket on 8002 (TLS) or 8001 (plain), /api/v2/channels/samsung.remote.control, which
takes every key, every application launch and the power off, and confirms nothing;
- the REST door of the same pair of ports, GET /api/v2/, which answers who the TV is, whether
it is on, whether it is a Frame and the MAC of its wifi, and is the ONLY reading that does
not wake the TV up;
- the UPnP renderer on 9197, /upnp/control/RenderingControl1, which is the only absolute
volume and the only deterministic mute; the remote key of volume is a blind step and the
key of mute is a toggle over a state nobody can read back.

What the wire cost to learn, so nobody has to learn it again:

- OPENING THE WEBSOCKET TURNS SOME TVs ON. So the poll never opens one: it reads PowerState
over REST, and a model that does not publish that field leaves ligado in None instead of
being probed with a socket that would switch the room on every ten seconds;
- the TLS of 8002 is a certificate the TV signs for itself and no authority backs, so the
connection does not verify it. The consequence is written down instead of hidden: the wire
is confidential against a passive listener on the segment and worth nothing against someone
who can sit in the middle of it, and the pairing token rides exactly this wire;
- the TV acknowledges no command. A key that the model does not have leaves on the wire like
any other, so the reread of the poll is the only check that exists here;
- pairing is a popup on the screen, and on this side it is just an open and silent socket.
The gestor gives a driver half the poll interval, so this driver waits a budget that fits
inside it and answers "aguardando", which is the word for a popup still on the
screen and an integrator who presses pair again;
- a Frame TV reads a KEY_POWER click as Art Mode and stays on, so the power off of a Frame is
the key held down for three seconds, which is press, wait, release;
- there is no reading of the active input, of the title or of the transport anywhere in this
protocol. is explicit that a driver which cannot tell leaves the field in None, so
fonte, tocando and reproduzindo stay None and the DP 146 and the DP 148 stay
quiet for this equipment;
- an H or J TV (Orsay, port 8000) and the legacy protocol on 55000 need a cipher that section
10 keeps out of this image, so this driver detects them by the answer they give and refuses
with a stable code, instead of guessing.
"""

import asyncio
import base64
import ipaddress
import json
import logging
import re
import socket

from aiohttp import ClientError, ClientSession, ClientTimeout, ClientWebSocketResponse, WSMsgType

from iphub.config import ip_literal
from iphub.drivers import corpo
from iphub.drivers import fio as transcricao_do_fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import (
    Auth,
    Campo,
    Descoberta,
    Manifesto,
    Sugestao,
    TipoCampo,
)

log = logging.getLogger("iphub.drivers.nativos.samsung")

TIPO = "tv_samsung"

# The port decides the scheme and whether the token travels, and this is the literal
# rule of the protocol: 8002 is the TLS door and everything else is the plain one.
PORTA_TLS = 8002
PORTA_ABERTA = 8001
PORTAS = (PORTA_TLS, PORTA_ABERTA)
PORTA_DMR = 9197
# Wake-on-LAN, the discard port every magic packet is thrown at.
PORTA_WOL = 9
DIFUSAO = "255.255.255.255"

ESQUEMA_SEGURO = "https"
ESQUEMA_ABERTO = "http"
FIO_SEGURO = "wss"
FIO_ABERTO = "ws"

CAMINHO_REST = "/api/v2/"
CAMINHO_CONTROLE = "/api/v2/channels/samsung.remote.control"
CAMINHO_DMR = "/upnp/control/RenderingControl1"

# This name is what the TV shows in the popup and keeps in its Device Connection
# Manager, so it is a product decision and not a detail; it travels in base64 inside the
# query string, and this one encodes to UUEgSVAgSHVi, with no '+', '/' or '=' to be
# percent encoded, which some firmwares trip over.
NOME_DO_CLIENTE = "QA IP Hub"
NOME_NA_QUERY = base64.b64encode(NOME_DO_CLIENTE.encode("utf-8")).decode("ascii")

# The events of the opening of the socket, of this file.
EVENTO_CONECTADO = "ms.channel.connect"
EVENTO_NEGADO = "ms.channel.unauthorized"
EVENTO_ERRO = "ms.error"
# The TV emits these two before it says anything useful, and a reader that took the
# first frame as the answer would call a paired TV a failure.
EVENTOS_DE_RUIDO = ("ed.edenTV.update", "ms.voiceApp.hide")
CHAVE_TOKEN_DO_EVENTO = "token"

# What the opening of the socket ended in; only this file reads these words.
ABERTURA_PAREADA = "pareada"
ABERTURA_ESPERANDO = "esperando"
ABERTURA_NEGADA = "negada"
ABERTURA_ANTIGA = "antiga"
ABERTURA_MUDA = "muda"

SERVICO_DMR = "urn:schemas-upnp-org:service:RenderingControl:1"
ENVELOPE = (
    '<?xml version="1.0"?>'
    '<s:Envelope s:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"'
    ' xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body>'
    '<u:{acao} xmlns:u="{servico}">{argumentos}</u:{acao}>'
    "</s:Body></s:Envelope>"
)
ARGUMENTOS_DMR = "<InstanceID>0</InstanceID><Channel>Master</Channel>"
PEDE_VOLUME = "GetVolume"
PEDE_MUDO = "GetMute"
POE_VOLUME = "SetVolume"
POE_MUDO = "SetMute"
TIPO_SOAP = 'text/xml; charset="utf-8"'

CAMPO_VOLUME = re.compile(r"<CurrentVolume>\s*(-?[0-9]{1,5})\s*</CurrentVolume>", re.IGNORECASE)
CAMPO_MUDO = re.compile(r"<CurrentMute>\s*([01])\s*</CurrentMute>", re.IGNORECASE)

# What the REST door answers, of this file.
CHAVE_APARELHO = "device"
CHAVE_TIPO = "type"
CHAVE_UDN = "udn"
CHAVE_ID = "id"
CHAVE_ENERGIA = "PowerState"
CHAVE_FRAME = "FrameTVSupport"
CHAVE_MAC = "wifiMac"
TIPO_DE_TV = "samsung smarttv"
LIGADO = "on"
DESLIGADO = "off"
VERDADEIRO = "true"
PREFIXO_DE_UUID = "uuid:"
# This TV answers the string "none" where the MAC of the wifi goes, and reading it as an
# address would put six bytes of nonsense inside the magic packet.
MAC_AUSENTE = "none"

# The frames this driver writes on the socket, of this file.
METODO_TECLA = "ms.remote.control"
METODO_EMISSAO = "ms.channel.emit"
REMOTO = "SendRemoteKey"
CLIQUE = "Click"
APERTA = "Press"
SOLTA = "Release"
EVENTO_APLICATIVO = "ed.apps.launch"
ANFITRIAO = "host"
TIPO_DE_LANCAMENTO = "DEEP_LINK"

TECLA_LIGA = "KEY_POWER"
TECLA_TOCAR = "KEY_PLAY"
TECLA_PAUSAR = "KEY_PAUSE"
TECLA_PARAR = "KEY_STOP"

# The words of TECLAS this driver sends, each one as the key of the reference list
# of this protocol. sair and play_pause are absent because KEY_EXIT and KEY_PLAY_BACK are not
# on that list, and says to omit a capability instead of implementing one to refuse.
# proxima and anterior are absent for a heavier reason: this protocol has no next track at
# all, and the usual shortcut of mapping them to KEY_CHUP and KEY_CHDOWN would put a channel
# change under the transport, so a scene that asks for the next track would change the channel.
TECLA_POR_PALAVRA = {
    "mais": "KEY_VOLUP",
    "menos": "KEY_VOLDOWN",
    "canal_mais": "KEY_CHUP",
    "canal_menos": "KEY_CHDOWN",
    "cima": "KEY_UP",
    "baixo": "KEY_DOWN",
    "esquerda": "KEY_LEFT",
    "direita": "KEY_RIGHT",
    "ok": "KEY_ENTER",
    "voltar": "KEY_RETURN",
    "inicio": "KEY_HOME",
    "menu": "KEY_MENU",
    "guia": "KEY_GUIDE",
    "info": "KEY_INFO",
    **{f"digito_{n}": f"KEY_{n}" for n in range(10)},
}
TECLAS_DECLARADAS = tuple(TECLA_POR_PALAVRA)

# A key travels inside a JSON frame, so it cannot break the frame, but it can reach the
# service menu of the TV; the reference list marks KEY_FACTORY as the one that opens it, and
# a hub that shipped it inside a shortcut of a scene would open it by remote control.
_TECLA = re.compile(r"KEY_[A-Z0-9_]{1,32}")
TECLAS_RECUSADAS = frozenset({"KEY_FACTORY"})
# The identifier of an application, which is what a shortcut of this TV really is.
_APLICATIVO = re.compile(r"[A-Za-z0-9._-]{1,64}")
# The name and the token land in the query string of the socket, so a value with a
# separator in it would write a second parameter nobody wrote in this file.
_NA_QUERY = re.compile(r"[A-Za-z0-9._~-]{1,128}")
_PORTA = re.compile(r"[0-9]{1,5}")
_MAC = re.compile(r"[0-9A-Fa-f]{12}")
_IDENTIDADE = re.compile(r"[A-Za-z0-9._:-]{8,128}")

CAMPO_PORTA = "porta"
CAMPO_PORTA_DMR = "porta_dmr"
CAMPO_MAC = "mac"
CAMPO_TOKEN = "token"

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100

TEMPO_LIMITE_S = 2.0
# The gestor gives a call into a driver half the poll interval, and one poll here is a
# REST reading plus two SOAP readings; a deadline that is only per exchange would let a TV
# that accepts the connection and goes quiet spend three times the limit and be cancelled in
# the middle, so the whole poll shares one budget and the renderer gets what is left of it.
ORCAMENTO_DO_POLL_S = 4.0
PRAZO_DE_ABERTURA_S = 2.5
# The popup is an open and silent socket, so the wait is bounded by the deadline of the
# gestor and not by the patience of the person in front of the TV; what does not fit answers
# "aguardando" and the integrator presses pair again.
PRAZO_DE_PAREAMENTO_S = 3.5
PRAZO_DE_RESPOSTA_S = 0.3
# This socket breaks under frames sent back to back, and the answer of this protocol to
# that is a single retry; the gap makes the retry rare, and it is small because a scene sends
# several steps to the same equipment inside the deadline of the gestor.
INTERVALO_MINIMO_S = 0.3
# A Frame TV takes the power key held down, or it only goes into Art Mode.
SEGUNDOS_SEGURANDO = 3.0
# The gestor cancels a call into a driver at half the poll interval, and the power off of
# a Frame is an opening of the socket, three seconds of held key and the release, all inside
# ONE call. With the opening budget of any other frame that walks past the ceiling, and the
# gestor answers eq_offline for a TV it just turned off, while a scene loses the step after
# this one to the deadline. So this path carries a budget of its own, and every opening inside
# it gets only what is left of that budget.
ORCAMENTO_DO_DESLIGAR_S = 4.5
CORPO_MAXIMO = 64 * 1024
FALHAS_ATE_OFFLINE = 2

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
AUTH_PENDENTE = "auth_pendente"
ERRO_APARELHO = "erro_aparelho"
NAO_SUPORTADO = "nao_suportado"

PAREADO = "pareado"
AGUARDANDO = "aguardando"
FALHOU = "falhou"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_TECLA = "tecla"
ACAO_ATALHO = "atalho"
ACAO_EXTRA = "comando_extra"

TECLA_POR_ACAO = {
    ACAO_TOCAR: TECLA_TOCAR,
    ACAO_PAUSAR: TECLA_PAUSAR,
    ACAO_PARAR: TECLA_PARAR,
}

# An input of this TV is a key of the reference list and not a number, because there is
# no KEY_HDMI1 to KEY_HDMI4 anywhere: KEY_HDMI walks the HDMI inputs and KEY_SOURCE only opens
# the menu. A shortcut is the identifier of an application, which is the only deterministic
# way to land on something, and those identifiers change with the model and with the year, so
# the driver offers few and well known ones.
SUGESTOES = (
    Sugestao("entradas", "TV", "KEY_TV"),
    Sugestao("entradas", "HDMI", "KEY_HDMI"),
    Sugestao("entradas", "Fontes", "KEY_SOURCE"),
    Sugestao("atalhos", "YouTube", "111299001912"),
    Sugestao("atalhos", "Netflix", "3201907018807"),
    Sugestao("atalhos", "Spotify", "3201606009684"),
    Sugestao("atalhos", "Prime Video", "3201910019365"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "Samsung TV of the Tizen generation, 2016 and later. It reads whether the TV is "
            "on over the REST door, sends keys and applications over the WebSocket of the "
            "remote control, and takes the volume and the mute from the UPnP renderer of the "
            "TV. A 2014 or 2015 model (H or J) speaks an encrypted protocol this hub does not "
            "carry, and it is refused with a clear code instead of half working."
        ),
        "preparo": (
            "On the TV: Settings, General, Network, Expert Settings, Power On with Mobile (Wake on "
            "LAN) on, so the hub can turn it on. In Device Connection Manager set Access "
            "Notification to First Time Only. TV and hub on the same network, no VLAN between "
            "them."
        ),
        "auth_ajuda": (
            "Turn the TV on and pair: it shows a popup asking to allow this hub, and the "
            "answer is on the screen of the TV, not here. While the popup is up the hub "
            "answers waiting, so accept it and press pair again. The TV asks once per "
            "connection until it is told otherwise: on the TV, Device Connection Manager, "
            "Access Notification, First Time Only. If pairing stops working, remove the old "
            "entry of this hub from the Device List of the TV and pair again. The TV also "
            "refuses a connection from another subnet or VLAN, and that failure looks like a "
            "timeout and not like a refusal."
        ),
        "campo_porta": "Port of the remote control (8002 with TLS, 8001 without it)",
        "campo_porta_dmr": "Port of the UPnP renderer, which carries the volume (9197)",
        "campo_mac": "MAC of the TV, only used to turn it on (leave empty to read it from it)",
        "campo_token": "Pairing token, filled in by pairing",
        "cap_ligar": (
            "Turning it on is a Wake-on-LAN magic packet, the only way in with the screen off. "
            "It needs Wake on LAN or Wake on Wireless LAN enabled on the TV, the hub on the "
            "same segment, and the MAC of the TV, which it usually answers by itself."
        ),
        "cap_volume": (
            "The volume and the mute go through the UPnP renderer of the TV. A model with it "
            "off or absent has no absolute volume at all, and the hub says so instead of "
            "sending a blind step."
        ),
        "cap_fonte": (
            "The value is a key of this TV: KEY_TV, KEY_HDMI, KEY_SOURCE, KEY_AV1. KEY_HDMI "
            "walks the HDMI inputs instead of picking one, so an exact input is reached by a "
            "shortcut of an application. The TV never says which input is active."
        ),
        "cap_atalho": (
            "The value is the identifier of an application, like 111299001912 for YouTube. "
            "The identifiers change with the model and with the year, so a shortcut that does "
            "nothing is usually the identifier of another generation."
        ),
        "cap_comando_extra": (
            "One key of this TV, written whole, like KEY_CHLIST. The key of the service menu "
            "is refused here."
        ),
    },
    "pt": {
        "descricao": (
            "TV Samsung da geração Tizen, 2016 em diante. Ela lê se a TV está ligada pela "
            "porta REST, manda teclas e aplicativos pela WebSocket do controle remoto, e tira "
            "o volume e o mudo do renderizador UPnP da TV. Um modelo de 2014 ou 2015 (H ou J) "
            "fala um protocolo criptografado que este hub não carrega, e é recusado com um "
            "código claro em vez de funcionar pela metade."
        ),
        "preparo": (
            "Na TV: Configurações, Geral, Rede, Configurações especializadas, Ligar com o celular "
            "(Wake on LAN) ligado, para o hub acendê-la. No Gerenciador de Conexão de "
            "Dispositivos, Notificação de acesso: Apenas na primeira vez. TV e hub na mesma rede, "
            "sem VLAN entre eles."
        ),
        "auth_ajuda": (
            "Ligue a TV e pareie: ela mostra um popup pedindo para permitir este hub, e a "
            "resposta está na tela da TV, não aqui. Enquanto o popup está de pé o hub responde "
            "aguardando, então aceite e aperte parear de novo. A TV pergunta uma vez por "
            "conexão até que se diga o contrário: na TV, Gerenciador de Conexão de "
            "Dispositivos, Configurações de Notificação de Acesso, Apenas na primeira vez. Se "
            "o pareamento parar de funcionar, apague a entrada antiga deste hub na Lista de "
            "Dispositivos da TV e pareie de novo. A TV também recusa conexão de outra sub-rede "
            "ou VLAN, e essa falha parece tempo esgotado e não recusa."
        ),
        "campo_porta": "Porta do controle remoto (8002 com TLS, 8001 sem ele)",
        "campo_porta_dmr": "Porta do renderizador UPnP, que leva o volume (9197)",
        "campo_mac": "MAC da TV, usado só para ligá-la (deixe vazio para lê-lo dela)",
        "campo_token": "Token de pareamento, preenchido pelo parear",
        "cap_ligar": (
            "Ligar é um pacote mágico de Wake-on-LAN, o único caminho com a tela apagada. Ele "
            "exige Wake on LAN ou Wake on Wireless LAN ligado na TV, o hub no mesmo segmento, "
            "e o MAC da TV, que ela costuma responder sozinha."
        ),
        "cap_volume": (
            "O volume e o mudo passam pelo renderizador UPnP da TV. Um modelo com ele "
            "desligado ou ausente não tem volume absoluto nenhum, e o hub diz isso em vez de "
            "mandar um passo cego."
        ),
        "cap_fonte": (
            "O valor é uma tecla desta TV: KEY_TV, KEY_HDMI, KEY_SOURCE, KEY_AV1. A KEY_HDMI "
            "percorre as entradas HDMI em vez de escolher uma, então uma entrada exata se "
            "alcança por um atalho de aplicativo. A TV nunca diz qual entrada está ativa."
        ),
        "cap_atalho": (
            "O valor é o identificador de um aplicativo, como 111299001912 para o YouTube. Os "
            "identificadores mudam com o modelo e com o ano, então um atalho que não faz nada "
            "costuma ser o identificador de outra geração."
        ),
        "cap_comando_extra": (
            "Uma tecla desta TV, escrita inteira, como KEY_CHLIST. A tecla do menu de serviço "
            "é recusada aqui."
        ),
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the TV."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Samsung(Driver):
    """One Samsung TV of the Tizen generation, read over REST and commanded over its socket."""

    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV Samsung (Tizen)", "en": "Samsung TV (Tizen)"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_VOLUME,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TOCAR,
            ACAO_PAUSAR,
            ACAO_PARAR,
            ACAO_TECLA,
            ACAO_ATALHO,
            ACAO_EXTRA,
        ),
        teclas=TECLAS_DECLARADAS,
        marca="Samsung",
        auth=Auth.POPUP_NO_APARELHO,
        descoberta=Descoberta(ssdp_fabricantes=("samsung",)),
        config_campos=(
            Campo(CAMPO_PORTA, TipoCampo.INTEIRO, padrao=str(PORTA_TLS)),
            Campo(CAMPO_PORTA_DMR, TipoCampo.INTEIRO, padrao=str(PORTA_DMR)),
            Campo(CAMPO_MAC, TipoCampo.TEXTO),
            Campo(CAMPO_TOKEN, TipoCampo.SEGREDO),
        ),
        textos=TEXTOS,
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._fio: ClientWebSocketResponse | None = None
        self._falhas = 0
        # Two ports and no field that tells a 2016 model from a 2022 one for sure, so the
        # one that answered is kept and the other is only tried again after the TV goes quiet.
        self._porta: int | None = None
        # The token the TV grants belongs to the registration, and a driver cannot write
        # one: the Cadastro is frozen and only the routes of the panel save it.
        # So it lives here for as long as this daemon runs, which is what keeps the popup off
        # the screen between two commands, and the registered one is what survives a restart.
        self._token_vivo = ""
        self._mac_da_tv = ""
        self._frame = False
        self._ultimo_quadro = 0.0
        # The poll does NOT take the lock below, on purpose: it can spend the whole
        # budget of a poll and a command that waited for it would be cancelled by the gestor.
        # So the instant of the last optimistic publishing is kept, and a reading of the
        # renderer older than it is dropped instead of undoing the command.
        self._escrito_em = 0.0
        # A scene and the panel command the same TV at the same time, and two frames
        # interleaved on one socket is what breaks it.
        self._trava = asyncio.Lock()

    async def parar(self) -> None:
        await self._fechar_fio()
        sessao = self._sessao
        self._sessao = None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The uuid the TV answers, asked with no registration and no token."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        async with ClientSession() as sessao:
            for porta in PORTAS:
                lido = await _buscar(sessao, _url_rest(endereco, porta), TEMPO_LIMITE_S)
                if lido is not None:
                    return _identidade_de(lido)
        return None

    async def autenticar(self) -> str:
        """Pairing is explicit, and it is a popup on the screen of the TV."""
        async with self._trava:
            await self._fechar_fio()
            resultado, fio = await self._abrir_fio(PRAZO_DE_PAREAMENTO_S)
            if resultado == ABERTURA_PAREADA:
                # The socket that earned the pairing is the one the next command writes
                # on, so a TV that has just been paired is not asked to grant it again.
                self._fio = fio
                return PAREADO
        if resultado == ABERTURA_ESPERANDO:
            # The socket stayed open and the TV said nothing, which is the popup waiting
            # for somebody in front of it; telling the panel it failed would send the
            # integrator to the network instead of to the screen.
            log.info("%s: the pairing popup is on the screen of the TV", self._id())
            return AGUARDANDO
        log.warning("%s: pairing ended in %s", self._id(), resultado)
        return FALHOU

    async def atualizar(self) -> None:
        """One poll: the REST door, which never wakes the TV, and then the renderer."""
        prazo_final = asyncio.get_running_loop().time() + ORCAMENTO_DO_POLL_S
        lido = await self._ler_rest(prazo_final)
        if lido is None:
            self._falhar(EQ_OFFLINE)
            return
        self._falhas = 0
        self._aprender(lido)
        # A command lands on the same TV while this reading is in flight, and _defina
        # replaces the whole Estado, so publishing a volume read BEFORE the command would put
        # the old value back over the optimistic one. That is not a half built state: it is a
        # lost update, and on the bus it is the DP of the level reporting the new
        # value, then the old one, then the new one again on the next poll.
        comeco_da_leitura = asyncio.get_running_loop().time()
        volume, mudo = await self._ler_renderizador(prazo_final)
        campos: dict[str, object] = {
            "online": True,
            "ligado": _ligado_de(lido),
            "detalhe": "",
        }
        if self._escrito_em <= comeco_da_leitura:
            campos["volume"] = volume
            campos["mudo"] = mudo
        self._defina(**campos)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            async with self._trava:
                return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            return await self._acordar()
        if acao == ACAO_DESLIGAR:
            return await self._desligar()
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao in (ACAO_FONTE, ACAO_EXTRA):
            return await self._tecla_crua(valor)
        if acao in TECLA_POR_ACAO:
            await self._clicar(TECLA_POR_ACAO[acao])
            return None
        if acao == ACAO_TECLA:
            return await self._trocar_tecla(valor)
        if acao == ACAO_ATALHO:
            return await self._abrir_aplicativo(valor)
        return await super().executar(acao, valor)

    async def _acordar(self) -> str | None:
        """Wake-on-LAN, the only way in with the screen off."""
        mac = self._mac()
        if mac is None:
            # Without a MAC there is no magic packet, and a TV that is off answers
            # nothing that could carry one; the registration has a field for exactly this.
            log.warning("%s: turning on needs the MAC of the TV, which is empty", self._id())
            return ERRO_APARELHO
        if not await _acordar_pela_lan(mac, ip_literal(self.cadastro.ip)):
            # A container with no permission to broadcast, or a segment where the
            # broadcast address is unreachable, ends with the packet never leaving the hub, and
            # None is the word for done: a scene would mark the step as done, the
            # bus would report a success and the TV would stay dark. This is the only way of
            # turning this TV on, so a packet that did not leave is said out loud.
            log.warning("%s: the magic packet did not leave the hub", self._id())
            return ERRO_APARELHO
        # The packet leaves whether or not the TV has Wake on LAN enabled, and the state
        # of the screen takes seconds to change. says a driver that cannot tell
        # leaves the fact alone, so the next poll is what publishes it.
        return None

    async def _desligar(self) -> str | None:
        if not self._frame:
            await self._clicar(TECLA_LIGA)
            return None
        # A Frame TV reads a click of this key as Art Mode and stays on, so the power off
        # of a Frame is the key held down; the integrator would otherwise swear the hub does
        # not turn the TV off.
        codigo = None
        # The opening of the socket and the release both come out of the budget of this
        # call, and the hold in the middle of them is what is left of it; a socket that took
        # its own opening budget here would spend more than the gestor gives the whole call.
        fim = asyncio.get_running_loop().time() + ORCAMENTO_DO_DESLIGAR_S
        await self._quadro(
            _quadro_de_tecla(TECLA_LIGA, APERTA), imediato=True, ate=fim - SEGUNDOS_SEGURANDO
        )
        try:
            await asyncio.sleep(SEGUNDOS_SEGURANDO)
        finally:
            # The deadline of the gestor can fire inside these three seconds, and a call
            # that unwound here would leave the power key held down on the TV of a customer,
            # so the release goes out on the way out.
            codigo = await self._soltar_energia(fim)
        return codigo

    async def _soltar_energia(self, ate: float) -> str | None:
        """The release of the power key, which never raises.

        This leaves inside a finally, and an exception thrown from there would replace
        the reason this call is unwinding, which is the deadline of the gestor.
        """
        try:
            await self._quadro(_quadro_de_tecla(TECLA_LIGA, SOLTA), imediato=True, ate=ate)
        except _Falha as falha:
            log.warning("%s: the power key was not released: %s", self._id(), falha.codigo)
            return falha.codigo
        return None

    async def _trocar_volume(self, valor: object) -> str | None:
        if type(valor) is not int or not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
            return INVALID_VALUE
        await self._falar_com_renderizador(
            POE_VOLUME, f"{ARGUMENTOS_DMR}<DesiredVolume>{valor}</DesiredVolume>"
        )
        self._publicar(volume=valor)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        if not isinstance(valor, bool):
            return INVALID_VALUE
        # The remote key of mute is a toggle over a state this protocol never reads back,
        # so the renderer is the only place a mute can be set to a value and not flipped.
        await self._falar_com_renderizador(
            POE_MUDO, f"{ARGUMENTOS_DMR}<DesiredMute>{int(valor)}</DesiredMute>"
        )
        self._publicar(mudo=valor)
        return None

    def _publicar(self, **campos: object) -> None:
        """What a command publishes as soon as the renderer took it, marked with the instant.

        The poll reports the change of a command right away, and the reading
        of a poll that started before this one must not undo it.
        """
        self._escrito_em = asyncio.get_running_loop().time()
        self._defina(**campos)

    async def _tecla_crua(self, valor: object) -> str | None:
        """One key of this TV, written whole: an input of the list, or a typed command."""
        if not isinstance(valor, str):
            return INVALID_VALUE
        tecla = valor.strip().upper()
        if not _TECLA.fullmatch(tecla) or tecla in TECLAS_RECUSADAS:
            return INVALID_VALUE
        await self._clicar(tecla)
        # KEY_HDMI walks the HDMI inputs instead of picking one, and nothing in this
        # protocol reads the active input back, so publishing the key as the input would be a
        # guess the panel and the DP 146 would show as a fact.
        return None

    async def _trocar_tecla(self, valor: object) -> str | None:
        tecla = TECLA_POR_PALAVRA.get(valor) if isinstance(valor, str) else None
        if tecla is None:
            return INVALID_VALUE
        await self._clicar(tecla)
        return None

    async def _abrir_aplicativo(self, valor: object) -> str | None:
        if not isinstance(valor, str) or not _APLICATIVO.fullmatch(valor.strip()):
            return INVALID_VALUE
        await self._quadro(_quadro_de_aplicativo(valor.strip()))
        return None

    def _aprender(self, lido: dict) -> None:
        """What the REST door says about the TV itself, and only this driver reads."""
        aparelho = lido.get(CHAVE_APARELHO)
        if not isinstance(aparelho, dict):
            return
        self._frame = _texto(aparelho.get(CHAVE_FRAME)).lower() == VERDADEIRO
        mac = _mac_limpo(_texto(aparelho.get(CHAVE_MAC)))
        if mac is not None:
            self._mac_da_tv = mac

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline; the port is tried
        again from the top, because a TV that came back may have come back on the other one.
        """
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self._id(), self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._porta = None
        self._defina(online=False, detalhe=codigo)

    async def _ler_rest(self, prazo_final: float) -> dict | None:
        """The device info of the TV, on the port that answered last time."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            return None
        sessao = await self._abrir()
        for porta in self._portas():
            restante = _restante(prazo_final)
            if restante <= 0:
                break
            # The first port may be the one this model does not have, and a TV that
            # accepts the connection and goes quiet on it would eat the whole budget of the
            # poll, so the second port would never be tried.
            lido = await _buscar(sessao, _url_rest(endereco, porta), min(restante, TEMPO_LIMITE_S))
            if lido is not None:
                self._porta = porta
                return lido
        return None

    async def _ler_renderizador(self, prazo_final: float) -> tuple[int | None, bool | None]:
        """The volume and the mute of the UPnP renderer, or two Nones when it is not there."""
        volume = _volume_lido(await self._perguntar_ao_renderizador(PEDE_VOLUME, prazo_final))
        mudo = _mudo_lido(await self._perguntar_ao_renderizador(PEDE_MUDO, prazo_final))
        return volume, mudo

    async def _perguntar_ao_renderizador(self, acao: str, prazo_final: float) -> str:
        """One SOAP reading inside what is left of the budget of the poll."""
        restante = _restante(prazo_final)
        if restante <= 0:
            return ""
        try:
            return await self._soap(acao, ARGUMENTOS_DMR, min(restante, TEMPO_LIMITE_S))
        except _Falha:
            # A model with the renderer off or absent still answers everything else, and
            # turning that into an offline TV would hide a TV that works.
            log.debug("%s: the UPnP renderer on %d said nothing", self._id(), self._porta_dmr())
            return ""

    async def _falar_com_renderizador(self, acao: str, argumentos: str) -> None:
        try:
            await self._soap(acao, argumentos, TEMPO_LIMITE_S)
        except _Falha as falha:
            # The two codes of _soap are two different facts, and the panel prints them
            # in as many words: eq_offline is "the device did not answer" and erro_aparelho is
            # "the device answered with an error". A renderer that is off, absent, or a TV
            # pulled from the wall answered nothing, so flattening both into erro_aparelho
            # made the SAME dead TV report offline for a key and a fault for the volume, and
            # sent the integrator looking for a defect in a device that is not there.
            log.warning(
                "%s: the UPnP renderer on %d ended %s in %s",
                self._id(),
                self._porta_dmr(),
                acao,
                falha.codigo,
            )
            raise

    async def _soap(self, acao: str, argumentos: str, tempo_s: float) -> str:
        """One exchange with the UPnP renderer of the TV, which is the port of the volume."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        url = f"{ESQUEMA_ABERTO}://{_hospedeiro(endereco)}:{self._porta_dmr()}{CAMINHO_DMR}"
        envelope = ENVELOPE.format(acao=acao, servico=SERVICO_DMR, argumentos=argumentos)
        cabecalhos = {"SOAPAction": f'"{SERVICO_DMR}#{acao}"', "Content-Type": TIPO_SOAP}
        sessao = await self._abrir()
        try:
            async with sessao.post(
                url,
                data=envelope.encode("utf-8"),
                headers=cabecalhos,
                # A device answering a redirect would send the hub to whatever
                # host it names, which is the LAN proxy the hub refuses to be.
                allow_redirects=False,
                timeout=ClientTimeout(total=tempo_s),
            ) as resposta:
                # This answer is one integer inside one tag, and reading it with an XML
                # parser would hand a document of a device on the LAN to expat and its
                # entities; the ceiling is here for the same reason.
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                if resposta.status >= 400:
                    raise _Falha(ERRO_APARELHO)
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            raise _Falha(EQ_OFFLINE) from erro
        return bruto.decode("utf-8", errors="replace")

    async def _clicar(self, tecla: str) -> None:
        await self._quadro(_quadro_de_tecla(tecla, CLIQUE))

    async def _quadro(
        self, quadro: str, *, imediato: bool = False, ate: float | None = None
    ) -> None:
        """One frame on the socket, with the single retry this protocol answers a broken pipe
        with: a socket that broke under a burst is reopened once and the frame goes again.

        Why imediato: between the press and the release of a held key nothing arrives and
        nothing may be waited for, because the three seconds of the hold already spend most of
        the deadline the gestor gives this call.

        Why ate: a caller that spends the deadline of the gestor on something else of its own,
        which is the held key of a Frame, hands the instant its whole budget ends, and the
        opening of this socket, retry included, fits inside what is left of it.
        """
        if not imediato:
            await self._espacar()
        for tentativa in (1, 2):
            fio = await self._fio_vivo(ate)
            # Every frame of the remote is a command the customer pressed, so every one
            # is written; the token never travels in a frame, only in the url of the socket.
            self._transcricao().enviado(quadro)
            try:
                await fio.send_str(quadro)
            except (ClientError, OSError, ValueError, RuntimeError) as erro:
                self._transcricao().falhou(quadro, erro)
                await self._fechar_fio()
                if tentativa == 2:
                    log.warning("%s: the socket of the TV refused the frame", self._id())
                    raise _Falha(EQ_OFFLINE) from erro
                continue
            self._ultimo_quadro = asyncio.get_running_loop().time()
            if not imediato:
                await self._ouvir(fio)
            return

    async def _espacar(self) -> None:
        """The gap between two frames, counted from the last one that left."""
        agora = asyncio.get_running_loop().time()
        restante = INTERVALO_MINIMO_S - (agora - self._ultimo_quadro)
        if restante > 0:
            await asyncio.sleep(restante)

    async def _ouvir(self, fio: ClientWebSocketResponse) -> None:
        """What the TV says right after a frame, which is never a confirmation.

        Nothing here proves the command worked, and the reread of the poll is the only
        check that exists. This read is what keeps the reader of the socket pumped, so a ping
        of the TV is answered, and it is where a rotated token is picked up.
        """
        try:
            mensagem = await fio.receive(timeout=PRAZO_DE_RESPOSTA_S)
        except (TimeoutError, ClientError, OSError, ValueError, RuntimeError):
            return
        if mensagem.type is not WSMsgType.TEXT:
            return
        self._transcricao().recebido(mensagem.data)
        self._guardar_token(_evento_de(mensagem.data))

    async def _fio_vivo(self, ate: float | None = None) -> ClientWebSocketResponse:
        fio = self._fio
        if fio is not None and not fio.closed:
            return fio
        prazo = PRAZO_DE_ABERTURA_S if ate is None else min(PRAZO_DE_ABERTURA_S, _restante(ate))
        if prazo <= 0:
            # The budget of this call is spent, and a socket opened past it would be
            # opened after the gestor already cancelled whoever asked for the frame.
            raise _Falha(EQ_OFFLINE)
        resultado, aberto = await self._abrir_fio(prazo)
        if aberto is None or resultado != ABERTURA_PAREADA:
            raise _Falha(_codigo_da_abertura(resultado))
        self._fio = aberto
        return aberto

    async def _abrir_fio(self, prazo_s: float) -> tuple[str, ClientWebSocketResponse | None]:
        """The socket of the remote control, opened and greeted, or the reason it was not."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            return ABERTURA_MUDA, None
        porta = self._porta or self._portas()[0]
        sessao = await self._abrir()
        # The deadline of the gestor covers the opening AND the greeting, so the two
        # share one budget; giving each one the whole of it would let a TV that accepts the
        # connection and goes quiet be cancelled from outside, with the socket left open.
        fim = asyncio.get_running_loop().time() + prazo_s
        try:
            async with asyncio.timeout_at(fim):
                fio = await sessao.ws_connect(
                    self._url_do_fio(endereco, porta),
                    # The TV signs its own certificate and no authority backs it, so
                    # there is nothing to verify against. What that costs, written here and in
                    # the docstring of this file instead of hidden: the wire is confidential
                    # against a passive listener on the segment and worth nothing against
                    # somebody who can sit in the middle of it, and the pairing token rides
                    # exactly this wire.
                    ssl=False,
                    # A device on the LAN never sizes the memory of the hub, and a frame
                    # of this socket carries an event of a few hundred bytes.
                    max_msg_size=CORPO_MAXIMO,
                )
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            log.debug("%s: the socket of the TV did not open: %s", self._id(), erro)
            return ABERTURA_MUDA, None
        resultado = await self._saudar(fio, fim)
        if resultado != ABERTURA_PAREADA:
            await _fechar(fio)
            return resultado, None
        return resultado, fio

    def _transcricao(self) -> transcricao_do_fio.Fio:
        transcricao = getattr(self, "_transcricao_do_fio", None)
        if transcricao is None:
            transcricao = transcricao_do_fio.Fio(log, self._id())
            self._transcricao_do_fio = transcricao
        return transcricao

    async def _saudar(self, fio: ClientWebSocketResponse, fim: float) -> str:
        """The opening of the socket, read until the TV says which of the four it is."""
        while True:
            restante = _restante(fim)
            if restante <= 0:
                return ABERTURA_ESPERANDO
            try:
                mensagem = await fio.receive(timeout=restante)
            except (TimeoutError, ClientError, OSError, ValueError, RuntimeError):
                return ABERTURA_ESPERANDO
            if mensagem.type is not WSMsgType.TEXT:
                return ABERTURA_MUDA
            self._transcricao().recebido(mensagem.data)
            evento = _evento_de(mensagem.data)
            nome = _texto(evento.get("event"))
            if nome in EVENTOS_DE_RUIDO:
                continue
            if nome == EVENTO_CONECTADO:
                self._guardar_token(evento)
                return ABERTURA_PAREADA
            if nome == EVENTO_NEGADO:
                return ABERTURA_NEGADA
            if nome == EVENTO_ERRO:
                # This answer means the TV does not know the method of the remote control
                # of this generation, which is an H or a J on the encrypted port 8000; section
                # 10 keeps the cipher it needs out of this image.
                log.warning("%s: this TV is not of the Tizen generation", self._id())
                return ABERTURA_ANTIGA
            log.debug("%s: the socket opened with %r", self._id(), nome)
            return ABERTURA_MUDA

    def _guardar_token(self, evento: dict) -> None:
        """The token the TV grants, which it rotates whenever it feels like it."""
        dados = evento.get("data")
        if not isinstance(dados, dict):
            return
        bruto = dados.get(CHAVE_TOKEN_DO_EVENTO)
        # This TV answers the token as a JSON integer, and a number in the query string
        # of the next connection, or in the config.json, breaks whoever reads it as text.
        token = str(bruto).strip() if isinstance(bruto, str | int) else ""
        if not token or not _NA_QUERY.fullmatch(token) or token == self._token_vivo:
            return
        self._token_vivo = token
        # A secret of a device never lands in the log, so what is written is
        # that there is a new one and not which one.
        log.info("%s: the TV granted a new pairing token", self._id())

    async def _fechar_fio(self) -> None:
        fio, self._fio = self._fio, None
        if fio is not None:
            await _fechar(fio)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession()
            self._sessao = sessao
        return sessao

    def _url_do_fio(self, endereco: str, porta: int) -> str:
        esquema = FIO_SEGURO if porta == PORTA_TLS else FIO_ABERTO
        url = f"{esquema}://{_hospedeiro(endereco)}:{porta}{CAMINHO_CONTROLE}?name={NOME_NA_QUERY}"
        token = self._token()
        # The literal rule of this protocol is that the token only rides the TLS door;
        # sending it on the plain one is a token in the clear for nothing in return.
        if token and porta == PORTA_TLS:
            url += f"&token={token}"
        return url

    def credenciais_do_aparelho(self) -> dict[str, str]:
        """The pairing token the TV handed over, which is a SEGREDO."""
        token = self._token()
        return {CAMPO_TOKEN: token} if token else {}

    def _token(self) -> str:
        for bruto in (self._token_vivo, self.cadastro.segredos.get(CAMPO_TOKEN, "")):
            token = bruto.strip()
            if not token:
                continue
            if _NA_QUERY.fullmatch(token):
                return token
            log.warning("%s: the registered pairing token is not a token", self._id())
        return ""

    def _mac(self) -> str | None:
        """The MAC of the TV, which is only ever used to turn it on.

        Why the registration wins: the published text of the field says to leave it empty to
        read the MAC from the TV, so a filled field is the explicit override, and it is the
        only override there is. A TV on cable answers the MAC of its wifi card all the same
        (the device info says networkType wired next to it), and the magic packet would go to
        the interface that is not plugged in, on the only way of turning this TV on.
        """
        return _mac_limpo(self.cadastro.campos.get(CAMPO_MAC, "")) or self._mac_da_tv or None

    def _portas(self) -> tuple[int, ...]:
        if self._porta is not None:
            return (self._porta,)
        registrada = _porta_de(self.cadastro.campos, CAMPO_PORTA, PORTA_TLS)
        return (registrada, *(porta for porta in PORTAS if porta != registrada))

    def _porta_dmr(self) -> int:
        return _porta_de(self.cadastro.campos, CAMPO_PORTA_DMR, PORTA_DMR)

    def _id(self) -> str:
        return self.cadastro.identidade


def _quadro_de_tecla(tecla: str, comando: str) -> str:
    return json.dumps(
        {
            "method": METODO_TECLA,
            "params": {
                "Cmd": comando,
                "DataOfCmd": tecla,
                "Option": "false",
                "TypeOfRemote": REMOTO,
            },
        }
    )


def _quadro_de_aplicativo(aplicativo: str) -> str:
    return json.dumps(
        {
            "method": METODO_EMISSAO,
            "params": {
                "event": EVENTO_APLICATIVO,
                "to": ANFITRIAO,
                "data": {"action_type": TIPO_DE_LANCAMENTO, "appId": aplicativo, "metaTag": ""},
            },
        }
    )


def _codigo_da_abertura(resultado: str) -> str:
    """The stable code for a socket that did not open."""
    if resultado in (ABERTURA_NEGADA, ABERTURA_ESPERANDO):
        # Negada is the TV saying no, and esperando is the socket that opened and went
        # quiet, which is the pairing popup standing on the screen; both are a command waiting
        # on somebody in front of the TV. This is the day one of every install: the poll reads
        # the REST door and publishes the TV as online, so eq_offline here would print
        # "the device did not answer" under a card that says online, and the integrator would
        # hunt the network instead of pressing pair.
        return AUTH_PENDENTE
    if resultado == ABERTURA_ANTIGA:
        # This TV is not the one this driver drives, and eq_offline would send the
        # integrator to look for a device that is answering perfectly.
        return NAO_SUPORTADO
    return EQ_OFFLINE


async def _fechar(fio: ClientWebSocketResponse) -> None:
    try:
        await fio.close()
    except (ClientError, OSError, ValueError, RuntimeError) as erro:
        log.debug("the socket of the TV did not close cleanly: %s", erro)


async def _buscar(sessao: ClientSession, url: str, tempo_s: float) -> dict | None:
    """The device info of one GET, or None when the TV did not answer it."""
    try:
        async with sessao.get(
            url,
            # The certificate of this TV is signed by the TV, so there is nothing to
            # verify it against; asks for the redirect to be refused all the same.
            ssl=False,
            allow_redirects=False,
            timeout=ClientTimeout(total=tempo_s),
        ) as resposta:
            bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
            if resposta.status >= 400:
                log.debug("the TV answered HTTP %d to %s", resposta.status, url)
                return None
    except (TimeoutError, ClientError, OSError, ValueError) as erro:
        log.debug("the TV did not answer %s: %s", url, erro or type(erro).__name__)
        return None
    lido = _evento_de(bruto.decode("utf-8", errors="replace"))
    return lido or None


def _url_rest(endereco: str, porta: int) -> str:
    esquema = ESQUEMA_SEGURO if porta == PORTA_TLS else ESQUEMA_ABERTO
    return f"{esquema}://{_hospedeiro(endereco)}:{porta}{CAMINHO_REST}"


def _evento_de(bruto: object) -> dict:
    """One JSON object, or an empty one for anything a device answered that is not one."""
    if not isinstance(bruto, str):
        return {}
    try:
        lido = json.loads(bruto)
    except (ValueError, RecursionError):
        return {}
    return lido if isinstance(lido, dict) else {}


def _identidade_de(lido: dict) -> str | None:
    """The uuid of the TV, without the prefix it answers it under."""
    aparelho = lido.get(CHAVE_APARELHO)
    aparelho = aparelho if isinstance(aparelho, dict) else {}
    # A soundbar of the same maker answers on this same door and is not this TV, so the
    # type is what says a finding of the sweep is one of these.
    if _texto(aparelho.get(CHAVE_TIPO) or lido.get(CHAVE_TIPO)).lower() != TIPO_DE_TV:
        return None
    bruto = _texto(aparelho.get(CHAVE_UDN)) or _texto(lido.get(CHAVE_ID))
    identidade = bruto.removeprefix(PREFIXO_DE_UUID).strip()
    return identidade if _IDENTIDADE.fullmatch(identidade) else None


def _ligado_de(lido: dict) -> bool | None:
    """Whether the screen is on, read from the REST door and from nowhere else."""
    aparelho = lido.get(CHAVE_APARELHO)
    if not isinstance(aparelho, dict):
        return None
    energia = _texto(aparelho.get(CHAVE_ENERGIA)).lower()
    if energia == LIGADO:
        return True
    if energia == DESLIGADO:
        return False
    # An older model does not publish this field, and the other way of asking is opening
    # the socket, which turns some of these TVs on; a poll every ten seconds would then switch
    # the room on by itself. says a driver that cannot tell leaves it in None.
    return None


def _volume_lido(documento: str) -> int | None:
    """The volume of the renderer, which already speaks the 0 to 100."""
    achado = CAMPO_VOLUME.search(documento)
    if achado is None:
        return None
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, int(achado.group(1))))


def _mudo_lido(documento: str) -> bool | None:
    achado = CAMPO_MUDO.search(documento)
    return None if achado is None else achado.group(1) == "1"


def _texto(bruto: object) -> str:
    return bruto.strip() if isinstance(bruto, str) else ""


def _hospedeiro(endereco: str) -> str:
    return f"[{endereco}]" if ":" in endereco else endereco


def _restante(prazo_final: float) -> float:
    return prazo_final - asyncio.get_running_loop().time()


def _porta_de(campos: dict[str, str], nome: str, padrao: int) -> int:
    """The registered port, or the published default when the field is absent or unusable."""
    # Str.isdigit is true for a superscript and for every other Unicode digit set, which
    # int then refuses, raising inside a poll, or reads as a port nobody asked for.
    bruto = str(campos.get(nome, "")).strip()
    if _PORTA.fullmatch(bruto) and 1 <= int(bruto) <= 65535:
        return int(bruto)
    return padrao


def _mac_limpo(bruto: str) -> str | None:
    """Twelve hexadecimal digits, whatever the person or the TV wrote them with."""
    lido = bruto.strip().replace(":", "").replace("-", "").replace(".", "")
    if lido.lower() == MAC_AUSENTE or not _MAC.fullmatch(lido):
        return None
    return lido.lower()


async def _acordar_pela_lan(mac: str, endereco: str | None) -> bool:
    """The magic packet: six bytes of 0xFF and the MAC sixteen times, thrown at the segment.

    True says the packet left this hub for at least one destination, and never that the TV
    took it: nothing answers a magic packet, and the next poll is what tells.
    """
    pacote = bytes.fromhex("f" * 12 + mac * 16)
    # The TV is asleep, so nothing answers and there is nobody to ask where it is; the
    # packet goes to the address it had and to the broadcast of the segment, because a lease
    # that moved would otherwise take the only way of turning the TV on with it.
    destinos = [DIFUSAO]
    if endereco is not None and ipaddress.ip_address(endereco).version == 4:
        destinos.insert(0, endereco)
    laco = asyncio.get_running_loop()
    try:
        transporte, _protocolo = await laco.create_datagram_endpoint(
            asyncio.DatagramProtocol, family=socket.AF_INET, allow_broadcast=True
        )
    except OSError as erro:
        log.warning("the magic packet did not leave: %s", erro)
        return False
    entregues = 0
    try:
        for destino in destinos:
            try:
                transporte.sendto(pacote, (destino, PORTA_WOL))
            except OSError as erro:
                log.warning("the magic packet did not reach %s: %s", destino, erro)
            else:
                entregues += 1
    finally:
        transporte.close()
    return entregues > 0

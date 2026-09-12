# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sony Bravia TV over the Scalar Web API plus IRCC,.

Everything goes to port 80 of the TV. A command or a reading is a JSON-RPC POST on
/sony/<service>, and a key of the remote is a SOAP POST on /sony/IRCC, on that same port.
The credential is the pre shared key the integrator types on the TV itself, sent in the
X-Auth-PSK header of every request: there is no handshake, no cookie and no pairing
registered inside the TV, which is why this driver takes the key and not the PIN.

What the TV does, and what it cost to find out:

- the documentation of Sony says a URL with a slash at the end of the service name is
invalid, so the path is /sony/system and never /sony/system/, and params is ALWAYS a
list, with the empty one for a method that takes no argument;
- a failure travels inside an HTTP 200, as {"error": [code, message]}, so the status alone
proves nothing; the message "not power-on" means the TV is off, which is success for the
off command and eq_offline for anything else;
- nothing answers without the key, getSystemInformation included, so identificar stays the
None of the base: this driver cannot ask a TV who it is before the registration carries
the key, and the identity (the cid) is read on the first exchange that has one;
- turning on takes three shots in this order, each tolerating its own failure: the magic
packet, setPowerStatus and the single IRCC code written in this file. An old model obeys
only the IRCC and a new one only the REST, none of the three confirms anything, and the
next poll is what says whether the screen came on; a TV that is really off answers neither
of the two shots that travel over IP, so a packet that left and two silent shots is the
ordinary way this command succeeds and it never comes back as a failure;
- the magic packet only works on a TV with wake on lan turned on, which is not how one
leaves the factory, so pairing turns it on with setWolMode and says so in the log; the
address it needs is read from the TV, which a TV that has been off since the hub booted
never answered, so the registration carries an optional field for it and that field is the
only thing that switches on a TV the hub has never seen awake. The packet goes to the
registered address and never to the broadcast of the segment, because this hub
talks to the equipment somebody registered and to nobody else;
- the WebApiCore service of the TV restarts by itself and answers 404 to everything while
it does, for about half a minute, so a 404 is tolerated for ten polls in a row while any
other silence is offline in two;
- polling a TV in standby keeps its network awake, which the documentation of the
integration this protocol was read from measures in watts, so a poll that reads anything
other than "active" asks nothing else;
- the base64 of a key is never written here, apart from the one of the power on: the list
comes from the TV with getRemoteControllerInfo, because it changes with the model, and a
key the TV did not list comes back as nao_suportado instead of a silent no-op;
- the first IRCC after some ten minutes of silence is swallowed with no error at all, so an
empty IRCC frame is sent first to wake the API up, and that same empty frame is what finds
out whether this model wants /sony/IRCC or /sony/ircc, a spelling that is not the same on
every firmware and that is remembered once found;
- the volume of the TV runs from minVolume to maxVolume and not from 0 to 100, so
is converted in both directions here; the entry read is the one whose target is "speaker",
never whatever came last, because a TV with a headphone connected would otherwise publish
the volume of the headphone;
- Estado.reproduzindo is always None: this API carries no transport state, and reading it
from an empty getPlayingContentInfo would report a TV playing inside an app as paused,
which is the defect already registers;
- Estado.fonte carries the uri and never the title, because maps the value of the
list of the registration, and the title is the label the integrator gave that value;
- Estado.fontes stays empty and the inputs come from the suggestions of the manifest: the
panel builds an example of the list out of a value it also shows as the label, and every
input uri of a Bravia is longer than the 16 characters a label may have, so
publishing them would offer the integrator items the registration then refuses.
"""

import asyncio
import json
import logging
import re
from contextlib import suppress
from time import monotonic

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import (
    Auth,
    Campo,
    Descoberta,
    Manifesto,
    Sugestao,
    TipoCampo,
)

log = logging.getLogger("iphub.drivers.nativos.sony")

# The methods of the poll, written once in the transcript and not every ten seconds.
PERGUNTAS_DE_ROTINA = (
    "getPowerStatus",
    "getVolumeInformation",
    "getPlayingContentInfo",
    "getSystemInformation",
    "getRemoteControllerInfo",
)

TIPO = "tv_sony_bravia"

# The REST and the IRCC of a Bravia both answer on 80 and the TV has no field to change
# it; the dmr.xml announced over SSDP names 52323, which is the UPnP door and not this one.
PORTA = 80

CAMINHO_SERVICO = "/sony/{servico}"
SERVICO_SISTEMA = "system"
SERVICO_AUDIO = "audio"
SERVICO_CONTEUDO = "avContent"
SERVICO_APLICATIVOS = "appControl"

# The spelling of the IRCC path is not the same on every firmware, and a model that
# wants the other one answers 404 to this one; the one that answered is remembered.
CAMINHOS_IRCC = ("/sony/IRCC", "/sony/ircc")

VERSAO = "1.0"
PEDIDO_ID = 1

CABECALHO_CHAVE = "X-Auth-PSK"
CABECALHO_ACAO = "SOAPACTION"
ACAO_SOAP = '"urn:schemas-sony-com:service:IRCC:1#X_SendIRCC"'
TIPO_SOAP = "text/xml; charset=UTF-8"

# The envelope of a key press, written once and byte for byte as the TV takes it.
ENVELOPE = (
    '<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/" '
    's:encodingStyle="http://schemas.xmlsoap.org/soap/encoding/"><s:Body>'
    '<u:X_SendIRCC xmlns:u="urn:schemas-sony-com:service:IRCC:1">'
    "<IRCCCode>{codigo}</IRCCCode></u:X_SendIRCC></s:Body></s:Envelope>"
)

PEDE_ENERGIA = "getPowerStatus"
PEDE_SISTEMA = "getSystemInformation"
PEDE_TECLAS = "getRemoteControllerInfo"
PEDE_VOLUME = "getVolumeInformation"
PEDE_CONTEUDO = "getPlayingContentInfo"

MANDA_ENERGIA = "setPowerStatus"
MANDA_WOL = "setWolMode"
MANDA_VOLUME = "setAudioVolume"
MANDA_MUDO = "setAudioMute"
MANDA_CONTEUDO = "setPlayContent"
MANDA_APLICATIVO = "setActiveApp"
MANDA_REINICIO = "requestReboot"
MANDA_FIM_DOS_APPS = "terminateApps"

# The only IRCC code this file writes: an old model obeys nothing else to come on.
CODIGO_LIGAR = "AAAAAQAAAAEAAAAuAw=="

ALVO_DE_AUDIO = "speaker"
LIGADA = "active"
DESLIGADA = "not power-on"

CHAVE_ESTADO = "status"
CHAVE_URI = "uri"
CHAVE_TITULO = "title"
CHAVE_PROGRAMA = "programTitle"
CHAVE_NUMERO = "dispNum"
CHAVE_ALVO = "target"
CHAVE_VOLUME = "volume"
CHAVE_MUDO = "mute"
CHAVE_MINIMO = "minVolume"
CHAVE_MAXIMO = "maxVolume"
CHAVE_CID = "cid"
CHAVE_MAC = "macAddr"
CHAVE_NOME = "name"
CHAVE_VALOR = "value"
CHAVE_ERRO = "error"
CHAVE_RESULTADO = "result"

PREFIXO_ENTRADA = "extInput"
PREFIXO_CANAL = "tv"
# An application is played by another method than a channel or an input, and the TV
# names every one of its own with this prefix, which is the only thing that tells them apart.
PREFIXO_APLICATIVO = "com.sony.dtv."

CAMPO_CHAVE = "psk"
# The address of the TV is read from the TV, and a TV that has been off since the hub
# booted never answered anything, so the magic packet would have nothing to carry; this field
# is the one place the integrator can write it down, and pairing fills nothing behind him
# because a registration is frozen and only the panel writes it.
CAMPO_MAC = "mac"

# The packet goes to the registered address and not to the broadcast of the segment,
# because this hub talks to the equipment somebody registered and to nobody else;
# it is the same rule the receiver driver of this repository already follows.
WOL_PORTA = 9
WOL_REPETICOES = 16
WOL_PREFIXO = "F" * 12

TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 256 * 1024
TEXTO_MAXIMO = 200
TECLAS_MAXIMAS = 200
CHAVE_MAXIMA = 64

# Of the other drivers, one lost poll is not a TV that went away, two is.
FALHAS_ATE_OFFLINE = 2

# The WebApiCore service of the TV restarts by itself and answers 404 to everything for
# about half a minute; two polls of tolerance would take the TV off the panel every time.
REINICIOS_ATE_OFFLINE = 10

# The first key after some ten minutes of silence is swallowed with no error, so an
# empty frame goes first to wake the API; ten minutes is the silence that costs the key.
DESPERTAR_S = 600.0

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100
PASSO_MAIS = "+1"
PASSO_MENOS = "-1"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
AUTH_PENDENTE = "auth_pendente"
ERRO_APARELHO = "erro_aparelho"
NAO_SUPORTADO = "nao_suportado"

PAREADO = "pareado"
FALHOU = "falhou"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_PROXIMA = "proxima"
ACAO_ANTERIOR = "anterior"
ACAO_TECLA = "tecla"
ACAO_ATALHO = "atalho"
ACAO_EXTRA = "comando_extra"

# The transport as the names the TV gives its own keys.
IRCC_DO_TRANSPORTE = {
    ACAO_TOCAR: "Play",
    ACAO_PAUSAR: "Pause",
    ACAO_PARAR: "Stop",
    ACAO_PROXIMA: "Next",
    ACAO_ANTERIOR: "Prev",
}

# Only the names verified in the sources of the protocol are here; a name nobody read
# on a TV would be a button of the panel that never does anything and never says so.
IRCC_DA_TECLA = {
    "canal_mais": "ChannelUp",
    "canal_menos": "ChannelDown",
    "cima": "Up",
    "baixo": "Down",
    "esquerda": "Left",
    "direita": "Right",
    "ok": "Confirm",
    "voltar": "Return",
    "inicio": "Home",
    "menu": "ActionMenu",
    "sair": "Exit",
    **{f"digito_{numero}": f"Num{numero}" for numero in range(10)},
}

# The volume keys go as a relative step of the audio service and never as an IRCC name,
# which is what makes them work without knowing the scale of this model.
TECLAS_RELATIVAS = {"mais": PASSO_MAIS, "menos": PASSO_MENOS}

TECLAS_DO_DRIVER = (*TECLAS_RELATIVAS, *IRCC_DA_TECLA)

# What the command channel may ask for by name, and the pair of the API of each.
EXTRAS = {
    "reboot": (SERVICO_SISTEMA, MANDA_REINICIO),
    "terminate_apps": (SERVICO_APLICATIVOS, MANDA_FIM_DOS_APPS),
}

# The value of an input or of a shortcut is a uri of the TV, and it lands inside the
# JSON of a command; anything outside these bytes is not a uri this API ever answers, so it
# is refused here instead of being handed to a device on the LAN.
_NO_FIO = re.compile(r"[A-Za-z0-9:/?=&.,_~%+@!#\-]{1,64}")
# The alphabet of base64, which is what an IRCC code is and all that goes inside the envelope.
_BASE64 = re.compile(r"[A-Za-z0-9+/=]{1,64}")
# The key travels in a header of every request, where a control character is an injected
# line and a character outside ascii is a byte the TV never saw; the space is inside, because
# the on screen keyboard of a TV has one and nothing on the TV says it may not be used.
_CHAVE = re.compile(rf"[\x20-\x7e]{{1,{CHAVE_MAXIMA}}}")
# The address as the TV writes it, and as somebody copies it off the screen of the TV or off
# the box, which is with colons, with hyphens or with nothing at all between the pairs.
_MAC = re.compile(r"[0-9A-Fa-f]{2}(?::[0-9A-Fa-f]{2}){5}")
_MAC_ESCRITO = re.compile(r"[0-9A-Fa-f]{2}(?:[:-]?[0-9A-Fa-f]{2}){5}")

# The uris of the inputs are the same on every Bravia and nobody types one from memory,
# so a TV that was just added already carries the ones it almost certainly has; the shortcuts
# are not suggested, because the uri of an application changes with the model and a guess
# would put a button on the panel that only fails.
SUGESTOES = (
    Sugestao("entradas", "HDMI 1", "extInput:hdmi?port=1"),
    Sugestao("entradas", "HDMI 2", "extInput:hdmi?port=2"),
    Sugestao("entradas", "HDMI 3", "extInput:hdmi?port=3"),
    Sugestao("entradas", "HDMI 4", "extInput:hdmi?port=4"),
    Sugestao("entradas", "Componente", "extInput:component?port=1"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "Sony Bravia TV over the Scalar Web API of the TV itself, with the keys of the "
            "remote over IRCC. It needs the pre shared key set on the TV, and nothing "
            "answers without it."
        ),
        "preparo": (
            "On the TV: Settings, Network, Home network setup, IP control: Authentication = "
            "Pre-Shared Key, and set the key you will paste in the PSK field. Turn Remote start on "
            "so the hub can turn the TV on. Write the MAC (Network status) into the MAC field."
        ),
        "campo_psk": "Pre shared key set on the TV (IP control)",
        "campo_mac": (
            "Physical address of the TV, optional, as it is written in Settings, Network, "
            "Network status. Without it the hub only switches on a TV it has already seen "
            "awake, because that is where it reads the address from."
        ),
        "auth_ajuda": (
            "On the TV, open Settings, Network, Home Network Setup, IP Control (on a Google "
            "TV, Settings, Network and Internet, Local network setup, IP control), turn the "
            "authentication on with a pre shared key and type one. Paste the same text here "
            "and pair: pairing also turns wake on lan on, which is what lets the hub switch "
            "the TV on later. The key goes in a header of every request, so up to 64 "
            "characters with no accent are taken here and anything else is refused before a "
            "single request, with the reason in the log."
        ),
        "cap_volume": (
            "The TV has a scale of its own and the level travels from 0 to 100, converted "
            "here. A model that does not publish its range takes the volume keys, which are "
            "a step up and a step down and need no scale."
        ),
        "cap_fonte": (
            "The value is the uri of the input, as the TV writes it: extInput:hdmi?port=1."
        ),
        "cap_tecla": (
            "The keys are the ones the TV lists for its own remote, so a model that does not "
            "have one refuses it instead of doing nothing."
        ),
        "cap_atalho": (
            "A shortcut is an application, written as its uri (com.sony.dtv....), or a "
            "channel, written as the uri of the channel (tv:...)."
        ),
        "cap_comando_extra": (
            "Two commands by name: reboot restarts the TV, terminate_apps closes the "
            "applications that are open."
        ),
        "lista_entradas": "The uri of the input, extInput:hdmi?port=1, and the name you give it.",
        "lista_atalhos": (
            "The uri of an application (com.sony.dtv....) or of a channel (tv:...). The name "
            "of the application is not its uri. A value fits 64 characters, which the uri of "
            "an application such as YouTube goes past: that one has no shortcut, and the way "
            "to it is the keys of the remote."
        ),
    },
    "pt": {
        "descricao": (
            "TV Sony Bravia pela Scalar Web API da própria TV, com as teclas do controle por "
            "IRCC. Ela precisa da chave pré compartilhada definida na TV, e nada responde "
            "sem ela."
        ),
        "preparo": (
            "Na TV: Configurações, Rede, Configuração de rede doméstica, Controle por IP: "
            "Autenticação = Chave pré compartilhada, e defina a chave que você vai colar no campo "
            "PSK. Ligue o Início remoto para o hub acender a TV. Anote o MAC (Status da rede) no "
            "campo MAC."
        ),
        "campo_psk": "Chave pré compartilhada definida na TV (controle por IP)",
        "campo_mac": (
            "Endereço físico da TV, opcional, como ele está escrito em Configurações, Rede, "
            "Status da rede. Sem ele o hub só acende uma TV que ele já viu acordada, porque é "
            "dela que ele lê o endereço."
        ),
        "auth_ajuda": (
            "Na TV, abra Configurações, Rede, Configuração de rede doméstica, Controle por IP "
            "(numa Google TV, Configurações, Rede e Internet, Configuração da rede local, "
            "Controle por IP), ligue a autenticação com chave pré compartilhada e digite uma. "
            "Cole o mesmo texto aqui e pareie: o pareamento também liga o wake on lan, que é o "
            "que permite ao hub acender a TV depois. A chave vai num cabeçalho de toda "
            "requisição, então aqui cabem até 64 caracteres sem acento e o resto é recusado "
            "antes de qualquer requisição, com a razão no log."
        ),
        "cap_volume": (
            "A TV tem uma escala própria e o nível viaja de 0 a 100, convertido aqui. Um "
            "modelo que não publica a faixa dele aceita as teclas de volume, que são um passo "
            "para cima e um para baixo e não precisam de escala."
        ),
        "cap_fonte": ("O valor é a uri da entrada, como a TV a escreve: extInput:hdmi?port=1."),
        "cap_tecla": (
            "As teclas são as que a TV lista para o controle dela, então um modelo que não tem "
            "uma a recusa em vez de não fazer nada."
        ),
        "cap_atalho": (
            "Um atalho é um aplicativo, escrito como a uri dele (com.sony.dtv....), ou um "
            "canal, escrito como a uri do canal (tv:...)."
        ),
        "cap_comando_extra": (
            "Dois comandos pelo nome: reboot reinicia a TV, terminate_apps fecha os "
            "aplicativos abertos."
        ),
        "lista_entradas": "A uri da entrada, extInput:hdmi?port=1, e o nome que você dá a ela.",
        "lista_atalhos": (
            "A uri de um aplicativo (com.sony.dtv....) ou de um canal (tv:...). O nome do "
            "aplicativo não é a uri dele. Um valor cabe em 64 caracteres, que a uri de um "
            "aplicativo como o YouTube passa: esse não tem atalho, e o caminho até ele são as "
            "teclas do controle."
        ),
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the TV, plus the two facts the
    caller decides with: the TV said it is off, and the API is restarting.
    """

    def __init__(self, codigo: str, *, desligada: bool = False, reiniciando: bool = False) -> None:
        self.codigo = codigo
        self.desligada = desligada
        self.reiniciando = reiniciando
        super().__init__(codigo)


class SonyBravia(Driver):
    """One Sony Bravia TV, read over the Scalar Web API and commanded over it and over IRCC."""

    # The port is not a field of the registration, because this protocol fixes it, and
    # the discovery claims only the manufacturer: the ScalarWebAPI signature of the SSDP is
    # announced by a Sony soundbar too, and refuses two manifests claiming one
    # signature, so the sweep would have to read the service list of the TV to tell them apart.
    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV Sony Bravia", "en": "Sony Bravia TV"},
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
            ACAO_PROXIMA,
            ACAO_ANTERIOR,
            ACAO_TECLA,
            ACAO_ATALHO,
            ACAO_EXTRA,
        ),
        teclas=TECLAS_DO_DRIVER,
        auth=Auth.CHAVE,
        descoberta=Descoberta(ssdp_fabricantes=("sony",)),
        config_campos=(
            Campo(nome=CAMPO_CHAVE, tipo=TipoCampo.SEGREDO, obrigatorio=True),
            Campo(nome=CAMPO_MAC, tipo=TipoCampo.TEXTO),
        ),
        textos=TEXTOS,
        marca="Sony",
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._falhas = 0
        self._reinicios = 0
        # The names of the keys change with the model, so the map comes from the TV and
        # is kept; asking for it on every key press would spend a request to learn what a
        # firmware does not change while it runs.
        self._teclas: dict[str, str] = {}
        self._mac: str | None = None
        self._avisou_da_chave = False
        self._sistema_lido = False
        self._minimo: int | None = None
        self._maximo: int | None = None
        self._caminho_ircc = CAMINHOS_IRCC[0]
        self._ultimo_ircc: float | None = None

    async def iniciar(self) -> None:
        await self._abrir()
        # The map of the keys is what makes a key press possible, and reading it here
        # keeps the first press of the day from paying for it; a TV that did not answer now
        # is asked again by the first key that needs it.
        with suppress(_Falha):
            await self._ler_teclas()

    async def parar(self) -> None:
        sessao = self._sessao
        self._sessao = None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    async def autenticar(self) -> str:
        """One question with the key answers whether the key is the key, and there
        is no pending state to report, because this API has no handshake to be in the middle of.
        """
        try:
            await self._ler_sistema()
        except _Falha as falha:
            log.warning("%s: the TV refused the key: %s", self._id(), falha.codigo)
            return FALHOU
        # Wake on lan is off on a TV out of the factory, and without it the magic packet
        # of ligar never works and nothing on the panel says why; turning it on belongs to the
        # one moment the integrator is looking at this TV. A TV that refused it is still
        # paired: it only means switching it on has to reach it awake.
        try:
            await self._chamar(SERVICO_SISTEMA, MANDA_WOL, {"enabled": True})
        except _Falha as falha:
            log.warning("%s: the TV refused wake on lan: %s", self._id(), falha.codigo)
        return PAREADO

    async def atualizar(self) -> None:
        """One poll: the power first, and the rest only while the screen is on."""
        try:
            ligada = await self._ler_energia()
        except _Falha as falha:
            self._falhar(falha)
            return
        self._falhas = 0
        self._reinicios = 0
        self._defina(online=True, ligado=ligada, detalhe="")
        if not self._sistema_lido:
            with suppress(_Falha):
                await self._ler_sistema()
        if not ligada:
            # Polling a TV in standby keeps its network awake and costs watts, and a TV
            # that is off has no volume, no input and nothing playing to read.
            self._defina(tocando=None)
            return
        # A reading that failed keeps the last one, which is the same rule as a lost
        # poll: the TV answered, so it is here, and one silent service is not a TV that went.
        with suppress(_Falha):
            await self._ler_audio()
        with suppress(_Falha):
            await self._ler_conteudo()

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            return await self._ligar()
        if acao == ACAO_DESLIGAR:
            return await self._desligar()
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._tocar_uri(valor)
        if acao == ACAO_ATALHO:
            return await self._atalho(valor)
        if acao == ACAO_TECLA:
            return await self._tecla(valor)
        if acao in IRCC_DO_TRANSPORTE:
            return await self._por_nome(IRCC_DO_TRANSPORTE[acao])
        if acao == ACAO_EXTRA:
            return await self._extra(valor)
        return await super().executar(acao, valor)

    async def _ligar(self) -> str | None:
        """Three shots in this order, each tolerating its own failure: the magic packet, the
        REST and the one IRCC code, because an old model obeys only one of the last two.
        """
        acordou = await self._acordar()
        codigos: list[str] = []
        try:
            if await self._ler_energia():
                self._defina(ligado=True)
                return None
        except _Falha as falha:
            if falha.codigo == AUTH_PENDENTE:
                # A key the TV refuses refuses the two shots below as well, and saying so
                # once is what sends the integrator to the key instead of to the network.
                return AUTH_PENDENTE
            codigos.append(falha.codigo)
        feito = False
        try:
            await self._chamar(SERVICO_SISTEMA, MANDA_ENERGIA, {CHAVE_ESTADO: True})
            feito = True
        except _Falha as falha:
            codigos.append(falha.codigo)
        try:
            await self._ircc(CODIGO_LIGAR)
            feito = True
        except _Falha as falha:
            codigos.append(falha.codigo)
        if feito:
            self._defina(ligado=True)
            return None
        # Eq_offline is the silence of a TV nobody reached, and any other code is the TV
        # talking, which is what deserves to travel out of a shot that failed.
        falou = next((codigo for codigo in codigos if codigo != EQ_OFFLINE), None)
        if falou is not None:
            return falou
        if acordou:
            # The magic packet is the only shot that reaches a TV that is really off, and
            # a TV that is really off is exactly the one that answers neither of the other
            # two, so their silence is what this command looks like when it works. Reporting
            # it would paint a failed step on every night scene that switches this TV on, two
            # seconds before the screen lights up, and the next poll is what says it did.
            return None
        return EQ_OFFLINE

    async def _desligar(self) -> str | None:
        try:
            await self._chamar(SERVICO_SISTEMA, MANDA_ENERGIA, {CHAVE_ESTADO: False})
        except _Falha as falha:
            # A TV that is already off answers "not power-on" to the off command, which
            # is the state that was asked for and not a failure.
            if not falha.desligada:
                raise
        self._defina(ligado=False)
        return None

    async def _trocar_volume(self, valor: object) -> str | None:
        if type(valor) is not int or not VOLUME_MINIMO <= valor <= VOLUME_MAXIMO:
            return INVALID_VALUE
        if self._maximo is None:
            # The scale comes from the TV, and a failure asking for it is the reason the
            # level cannot be written, so its code travels out; blaming the value for a lost
            # exchange would send the integrator to the panel instead of to the TV.
            await self._ler_audio()
        bruto = _para_o_aparelho(valor, self._minimo, self._maximo)
        if bruto is None:
            # The scale of this model is minVolume to maxVolume, and writing 0 to 100
            # into a TV that answers another range sets a level nobody asked for; the volume
            # keys are the way in on a TV that hides its range.
            log.warning("%s: the TV did not publish its volume range", self._id())
            return ERRO_APARELHO
        await self._audio(str(bruto))
        self._defina(volume=valor)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        # The channel toggles the mute and sends the value it computed, and
        # a scene writes a bool as well, so this driver never has to guess which way to go.
        if not isinstance(valor, bool):
            return INVALID_VALUE
        await self._chamar(SERVICO_AUDIO, MANDA_MUDO, {CHAVE_ESTADO: valor})
        self._defina(mudo=valor)
        return None

    async def _tocar_uri(self, valor: object) -> str | None:
        """One input of the list of the registration, which is a uri of the TV."""
        uri = _uri(valor)
        if uri is None:
            return INVALID_VALUE
        await self._chamar(SERVICO_CONTEUDO, MANDA_CONTEUDO, {CHAVE_URI: uri})
        self._defina(fonte=uri)
        return None

    async def _atalho(self, valor: object) -> str | None:
        """An application goes through the application service and everything else through
        the content one, decided by the prefix the TV gives its own applications.
        """
        uri = _uri(valor)
        if uri is None:
            return INVALID_VALUE
        if uri.startswith(PREFIXO_APLICATIVO):
            await self._chamar(SERVICO_APLICATIVOS, MANDA_APLICATIVO, {CHAVE_URI: uri})
            return None
        await self._chamar(SERVICO_CONTEUDO, MANDA_CONTEUDO, {CHAVE_URI: uri})
        return None

    async def _tecla(self, valor: object) -> str | None:
        if not isinstance(valor, str):
            return INVALID_VALUE
        passo = TECLAS_RELATIVAS.get(valor)
        if passo is not None:
            await self._audio(passo)
            return None
        nome = IRCC_DA_TECLA.get(valor)
        if nome is None:
            return INVALID_VALUE
        return await self._por_nome(nome)

    async def _extra(self, valor: object) -> str | None:
        par = EXTRAS.get(valor) if isinstance(valor, str) else None
        if par is None:
            return INVALID_VALUE
        servico, metodo = par
        await self._chamar(servico, metodo)
        return None

    async def _por_nome(self, nome: str) -> str | None:
        """One key of the remote, by the name the TV gave it."""
        if not self._teclas:
            # The map is the list of keys of THIS model, and a failure reading it is the
            # reason the key cannot be pressed, so its code travels out instead of being
            # swallowed into an answer that would blame the model for a lost exchange.
            await self._ler_teclas()
        if not self._teclas:
            # An answer with no key in it is an answer this driver cannot use, and pressing
            # nothing while reporting success is what the TV does on its own.
            raise _Falha(ERRO_APARELHO)
        codigo = self._teclas.get(nome)
        if codigo is None:
            log.info("%s: the TV does not list the key %s", self._id(), nome)
            return NAO_SUPORTADO
        await self._ircc(codigo)
        return None

    async def _audio(self, volume: str) -> None:
        """The audio service takes the level as a string, absolute or a relative step."""
        await self._chamar(
            SERVICO_AUDIO, MANDA_VOLUME, {CHAVE_ALVO: ALVO_DE_AUDIO, CHAVE_VOLUME: volume}
        )

    async def _ler_energia(self) -> bool:
        lido = _primeiro(await self._chamar(SERVICO_SISTEMA, PEDE_ENERGIA))
        estado = _texto(lido.get(CHAVE_ESTADO)) if isinstance(lido, dict) else ""
        return estado == LIGADA

    async def _ler_sistema(self) -> None:
        """The identity of the TV and the address the magic packet needs, read once."""
        lido = _primeiro(await self._chamar(SERVICO_SISTEMA, PEDE_SISTEMA))
        if not isinstance(lido, dict):
            raise _Falha(ERRO_APARELHO)
        mac = _texto(lido.get(CHAVE_MAC))
        self._mac = mac.upper() if _MAC.fullmatch(mac) else None
        self._sistema_lido = True
        # The cid is the identity and the address is only where the TV
        # answered today, so the log is what lets the integrator tell one TV from another.
        log.info(
            "%s: the TV answered cid %s and mac %s",
            self._id(),
            _texto(lido.get(CHAVE_CID)).lower() or "?",
            self._mac or "?",
        )

    async def _ler_audio(self) -> None:
        entrada = _alto_falante(await self._chamar(SERVICO_AUDIO, PEDE_VOLUME))
        if entrada is None:
            # The range goes with the level it converts. Keeping the one of the last
            # answer that had a speaker would let a command write the old scale into a TV
            # whose state already says the level here is unknown.
            self._minimo = None
            self._maximo = None
            self._defina(volume=None, mudo=None)
            return
        self._minimo = _inteiro(entrada.get(CHAVE_MINIMO))
        self._maximo = _inteiro(entrada.get(CHAVE_MAXIMO))
        self._defina(
            volume=_do_aparelho(_inteiro(entrada.get(CHAVE_VOLUME)), self._minimo, self._maximo),
            mudo=_booleano(entrada.get(CHAVE_MUDO)),
        )

    async def _ler_conteudo(self) -> None:
        lido = _primeiro(await self._chamar(SERVICO_CONTEUDO, PEDE_CONTEUDO))
        if not isinstance(lido, dict):
            # An application running with no metadata answers an empty result, and
            # inventing a title for it would be the driver reporting what it does not know.
            self._defina(fonte=None, tocando=None)
            return
        uri = _texto(lido.get(CHAVE_URI))
        self._defina(fonte=uri or None, tocando=_titulo_de(uri, lido))

    async def _ler_teclas(self) -> None:
        """The names and the codes of the remote of THIS model, which no file can carry."""
        resultado = await self._chamar(SERVICO_SISTEMA, PEDE_TECLAS)
        lidas = resultado[1] if len(resultado) > 1 else None
        if not isinstance(lidas, list):
            return
        mapa: dict[str, str] = {}
        for item in lidas[:TECLAS_MAXIMAS]:
            if not isinstance(item, dict):
                continue
            nome = _texto(item.get(CHAVE_NOME))
            codigo = _texto(item.get(CHAVE_VALOR))
            if nome and _BASE64.fullmatch(codigo):
                mapa[nome] = codigo
        if mapa:
            self._teclas = mapa

    async def _acordar(self) -> bool:
        """The magic packet, which is the only thing that reaches a TV that is really off;
        True when it left, which is what says the silence of the other two shots is expected.
        """
        endereco = ip_literal(self.cadastro.ip)
        pacote = _magico(self._endereco_fisico())
        if endereco is None or pacote is None:
            return False
        laco = asyncio.get_running_loop()
        try:
            transporte, _protocolo = await laco.create_datagram_endpoint(
                asyncio.DatagramProtocol, remote_addr=(endereco, WOL_PORTA)
            )
        except OSError as erro:
            # A network that refused the datagram is one of the three shots of ligar
            # failing, and the other two still have to be fired.
            log.debug("%s: the magic packet did not leave: %s", self._id(), erro)
            return False
        try:
            transporte.sendto(pacote)
        finally:
            transporte.close()
        return True

    def _endereco_fisico(self) -> str | None:
        """The address of the TV: the one it answered, or the one the registration carries,
        which is the only one a TV that has been off since the hub booted ever has.
        """
        if self._mac is not None:
            return self._mac
        escrito = self.cadastro.campos.get(CAMPO_MAC, "").strip()
        return escrito if _MAC_ESCRITO.fullmatch(escrito) else None

    async def _ircc(self, codigo: str) -> None:
        if not _BASE64.fullmatch(codigo):
            raise _Falha(INVALID_VALUE)
        if self._precisa_despertar():
            # The first key after some ten minutes of silence is swallowed with no error,
            # so an empty frame wakes the API up and the real key lands; that same empty frame
            # is what finds out which spelling of the path this model answers.
            await self._enviar_ircc("")
        await self._enviar_ircc(codigo)

    def _precisa_despertar(self) -> bool:
        ultimo = self._ultimo_ircc
        return ultimo is None or monotonic() - ultimo >= DESPERTAR_S

    async def _enviar_ircc(self, codigo: str) -> None:
        chave = self._chave()
        if chave is None:
            raise _Falha(AUTH_PENDENTE)
        envelope = ENVELOPE.format(codigo=codigo)
        cabecalhos = {
            **self._cabecalhos(chave),
            "Content-Type": TIPO_SOAP,
            CABECALHO_ACAO: ACAO_SOAP,
        }
        for caminho in self._caminhos_ircc():
            estado = await self._postar(self._url(caminho), envelope, cabecalhos)
            if estado == 404:
                continue
            if estado in (401, 403):
                raise _Falha(AUTH_PENDENTE)
            if estado >= 400:
                log.warning("the TV answered HTTP %d to a key", estado)
                raise _Falha(ERRO_APARELHO)
            self._caminho_ircc = caminho
            self._ultimo_ircc = monotonic()
            return
        raise _Falha(EQ_OFFLINE, reiniciando=True)

    def _caminhos_ircc(self) -> tuple[str, ...]:
        """The spelling that worked first, and the other one only after a 404."""
        return (self._caminho_ircc, *(c for c in CAMINHOS_IRCC if c != self._caminho_ircc))

    async def _chamar(self, servico: str, metodo: str, params: dict | None = None) -> list:
        """One JSON-RPC exchange, answered as the list inside "result"."""
        chave = self._chave()
        if chave is None:
            # A registration saved without the key would otherwise ask the TV to say
            # what this line already knows, and every command would look like a network fault.
            raise _Falha(AUTH_PENDENTE)
        ordem = {
            "method": metodo,
            "id": PEDIDO_ID,
            # Params is always a list for this API, and a method with no argument takes
            # the empty one; an object here is refused by the TV.
            "params": [params] if params is not None else [],
            "version": VERSAO,
        }
        url = self._url(CAMINHO_SERVICO.format(servico=servico))
        estado, bruto = await self._trocar(url, self._cabecalhos(chave), ordem, None)
        return _resultado_de(estado, bruto, metodo)

    async def _postar(self, url: str, texto: str, cabecalhos: dict[str, str]) -> int:
        estado, _bruto = await self._trocar(url, cabecalhos, None, texto)
        return estado

    async def _trocar(
        self,
        url: str,
        cabecalhos: dict[str, str],
        ordem: dict | None,
        texto: str | None,
    ) -> tuple[int, bytes]:
        # The transcript writes the method and its params and never the headers, where
        # the pre shared key rides; a poll asks the same methods every ten seconds and is
        # written once.
        metodo = str((ordem or {}).get("method", "IRCC"))
        rotina = metodo in PERGUNTAS_DE_ROTINA
        transcricao = self._transcricao()
        transcricao.enviado(
            f"{metodo} {fio.redigir(ordem)}" if ordem else f"{metodo} {fio.aparar(texto)}",
            rotina=rotina,
        )
        sessao = await self._abrir()
        try:
            async with sessao.post(
                url,
                json=ordem,
                data=texto,
                headers=cabecalhos,
                # A TV answering a redirect would send the hub, and the key
                # in its header, to whatever host it names.
                allow_redirects=False,
            ) as resposta:
                bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            transcricao.falhou(metodo, erro)
            raise _Falha(EQ_OFFLINE) from erro
        transcricao.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        return estado, bruto

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self.cadastro.identidade or self.cadastro.ip)
            self._fio = transcricao
        return transcricao

    def _falhar(self, falha: _Falha) -> None:
        """One lost poll keeps the last state and two in a row is offline, except while the
        API of the TV restarts, which answers 404 to everything for about half a minute.
        """
        if falha.reiniciando:
            self._reinicios += 1
            log.warning("%s: the TV answered 404 on poll %d", self._id(), self._reinicios)
            if self._reinicios < REINICIOS_ATE_OFFLINE:
                return
        else:
            self._falhas += 1
            log.warning("%s: poll %d failed with %s", self._id(), self._falhas, falha.codigo)
            if self._falhas < FALHAS_ATE_OFFLINE:
                return
        self._sistema_lido = False
        self._defina(online=False, detalhe=falha.codigo)

    def _url(self, caminho: str) -> str:
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        anfitriao = f"[{endereco}]" if ":" in endereco else endereco
        return f"http://{anfitriao}:{PORTA}{caminho}"

    def _cabecalhos(self, chave: str) -> dict[str, str]:
        return {CABECALHO_CHAVE: chave, "Cache-Control": "no-cache"}

    def _chave(self) -> str | None:
        """The key of the registration, or None with the reason in the log.

        A key with an accent or one longer than the header takes is refused here, before
        any request, and the panel only ever sees auth_pendente; without this line the
        integrator reads the same key on the TV, finds it identical and goes hunting the
        network. It is said once per driver and not once per poll.
        """
        chave = self.cadastro.segredos.get(CAMPO_CHAVE, "").strip()
        if _CHAVE.fullmatch(chave):
            return chave
        if chave and not self._avisou_da_chave:
            self._avisou_da_chave = True
            # The length and never the key itself, a credential of a device does
            # not go to the log any more than it goes to the panel.
            log.warning(
                "%s: the key of the registration is %d characters and only up to %d printable "
                "ascii ones travel in the header, so no request is made",
                self._id(),
                len(chave),
                CHAVE_MAXIMA,
            )
        return None

    def _id(self) -> str:
        return self.cadastro.identidade

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao


def _magico(mac: str | None) -> bytes | None:
    """The magic packet of an address: six bytes of ones and the address sixteen times."""
    if mac is None or not _MAC_ESCRITO.fullmatch(mac):
        return None
    return bytes.fromhex(WOL_PREFIXO + mac.replace(":", "").replace("-", "") * WOL_REPETICOES)


def _resultado_de(estado: int, bruto: bytes, metodo: str) -> list:
    """The list inside "result", or the stable code the answer deserves."""
    if estado in (401, 403):
        raise _Falha(AUTH_PENDENTE)
    if estado == 404:
        # The WebApiCore service of the TV answers 404 to everything while it restarts,
        # so this is the silence that is tolerated for ten polls and not the usual two.
        raise _Falha(EQ_OFFLINE, reiniciando=True)
    if estado >= 400:
        log.warning("the TV answered HTTP %d to %s", estado, metodo)
        raise _Falha(ERRO_APARELHO)
    try:
        documento = json.loads(bruto.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError) as erro:
        raise _Falha(ERRO_APARELHO) from erro
    if not isinstance(documento, dict):
        raise _Falha(ERRO_APARELHO)
    _do_erro(documento.get(CHAVE_ERRO), metodo)
    resultado = documento.get(CHAVE_RESULTADO)
    if not isinstance(resultado, list):
        raise _Falha(ERRO_APARELHO)
    return resultado


def _do_erro(erro: object, metodo: str) -> None:
    """The failure of this API travels inside a 200, so the body is the only place it shows."""
    if not isinstance(erro, list) or not erro:
        return
    codigo = erro[0] if isinstance(erro[0], int) else 0
    mensagem = erro[1] if len(erro) > 1 and isinstance(erro[1], str) else ""
    if DESLIGADA in mensagem.lower():
        raise _Falha(EQ_OFFLINE, desligada=True)
    if codigo in (401, 403):
        raise _Falha(AUTH_PENDENTE)
    log.warning("the TV refused %s with %r", metodo, mensagem[:TEXTO_MAXIMO])
    raise _Falha(ERRO_APARELHO)


def _alto_falante(resultado: list) -> dict | None:
    """The entry of the speaker, never the last one seen: a TV with a headphone connected
    answers that one too, and publishing it would put the level of the headphone on the panel.
    """
    primeira = _primeiro(resultado)
    for entrada in primeira if isinstance(primeira, list) else ():
        if isinstance(entrada, dict) and _texto(entrada.get(CHAVE_ALVO)) == ALVO_DE_AUDIO:
            return entrada
    return None


def _titulo_de(uri: str, lido: dict) -> str | None:
    """What is playing, which an input does not have: the title of an input is its name."""
    if uri.startswith(PREFIXO_ENTRADA):
        return None
    if uri.startswith(PREFIXO_CANAL):
        return _texto(lido.get(CHAVE_PROGRAMA)) or _texto(lido.get(CHAVE_NUMERO)) or None
    return _texto(lido.get(CHAVE_TITULO)) or None


def _uri(valor: object) -> str | None:
    """A value of a list of the registration, which is a uri of this TV and nothing else."""
    if not isinstance(valor, str):
        return None
    limpo = valor.strip()
    return limpo if _NO_FIO.fullmatch(limpo) else None


def _primeiro(resultado: list) -> object:
    return resultado[0] if resultado else None


def _texto(valor: object) -> str:
    return valor.strip()[:TEXTO_MAXIMO] if isinstance(valor, str) else ""


def _inteiro(valor: object) -> int | None:
    return valor if type(valor) is int else None


def _booleano(valor: object) -> bool | None:
    return valor if isinstance(valor, bool) else None


def _do_aparelho(valor: int | None, minimo: int | None, maximo: int | None) -> int | None:
    """The level of the TV as the 0 to 100, or None for a TV that did not say
    which scale it speaks.
    """
    if valor is None or minimo is None or maximo is None or maximo <= minimo:
        return None
    convertido = round((valor - minimo) * VOLUME_MAXIMO / (maximo - minimo))
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, convertido))


def _para_o_aparelho(valor: int, minimo: int | None, maximo: int | None) -> int | None:
    """The 0 to 100 as the level of this TV, or None while its scale is unknown."""
    if minimo is None or maximo is None or maximo <= minimo:
        return None
    return round(minimo + valor * (maximo - minimo) / VOLUME_MAXIMO)

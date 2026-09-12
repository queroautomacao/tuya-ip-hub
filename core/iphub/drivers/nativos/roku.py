# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Roku TV and Roku streaming player over the ECP, the external control protocol,.

One transport and nothing else: plain HTTP on port 8060, with no TLS, no session, no
handshake and no pairing. A read is a GET that answers XML, a command is a POST with an empty
body, and the port is the same on every model, so the registration asks for no field at all.

What the device does, and what reading its protocol cost:

- the identity is the serial-number of /query/device-info, which answers in standby too. The
udn is a valid uuid that nothing else in this ecosystem keys on, and the device-id is equal
to the serial on the old boxes and different on the newer Roku TVs, so the serial is the
only stable one across families. The answer to an M-SEARCH names it inside the USN, after
"uuid:roku:ecp", which is not the shape of a UPnP uuid;
- there is NO volume in the ECP, in either direction: the only keys are VolumeUp, VolumeDown
and VolumeMute, and no answer of the device carries a level. So this driver does not declare
the capability, the level DP stays quiet, the profile is born with no N and the
volume is the mais and menos keys. Counting steps to fake a 0 to 100 was refused: the real
scale is unknown, the remote in the hand of the customer desynchronises it on the first
press, and the reread would confirm the invented number as truth;
- mute is one key that toggles and the device reports the result nowhere, so Estado.mudo stays
None by the same rule that governs reproduzindo;
- Play is ONE key and it toggles, so tocar and pausar are guarded against the transport read
from /query/media-player and put nothing on the wire when the device is already where the
caller asked. Where there is no transport to read (a TV input, a stopped player) they are a
blind toggle that writes no state, and there is no Stop key at all, so parar is not declared.
Fwd and Rev are fast forward and rewind and not track skip;
- PowerOff kills the answer: the device is gone before the POST returns, so a silence on the
way out of that one command is success and never an offline device;
- the setting "Control by mobile apps" (Roku OS 14.1 and later) governs keypress and
query/tv-active-channel, and does NOT govern device-info, apps, active-app, media-player or
launch. With it off the hub reads state, shows the device online and still changes apps
while every key fails, so the refusal is logged apart and textos names the menu path;
- an input is /launch/<id of app>, and the same field carries a number for a streaming app and
tvinput.hdmi1 or tvinput.dtv for a physical input of a Roku TV; launch does not depend on
that setting and the InputHDMI keys do, which is why launch is the path;
- a 200 with an empty body confirms nothing, so a key is checked against the closed list of
the protocol before it goes on the wire and free text is never joined into the path; a
shortcut is only a key no capacity of this driver already covers, and only a 2xx is an
answer, because a redirect is the proof that the request was not served;
- the XML is read by pattern and not by a parser: six scalars and one attribute per app do not
justify handing a document from a device on the LAN to a parser with entities.
"""

import logging
import re

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Descoberta, Manifesto, Sugestao

log = logging.getLogger("iphub.drivers.nativos.roku")

TIPO = "tv_roku"

# The ECP answers on 8060 on every model and there is no variant, so the registration
# asks for no port and a finding of the sweep is already enough to command the device.
PORTA = 8060

CAMINHO_APARELHO = "/query/device-info"
CAMINHO_APP_ATIVO = "/query/active-app"
CAMINHO_APPS = "/query/apps"
CAMINHO_TOCADOR = "/query/media-player"
CAMINHO_CANAL = "/query/tv-active-channel"
CAMINHO_TECLA = "/keypress/{tecla}"
CAMINHO_ABRIR = "/launch/{valor}"
# The questions of the poll, written once in the transcript and not every ten seconds.
PERGUNTAS_DE_ROTINA = (CAMINHO_APARELHO, CAMINHO_APP_ATIVO, CAMINHO_TOCADOR, CAMINHO_APPS)

# A read is a GET and a command is a POST with an empty body, which is the whole method table.
METODO_LEITURA = "GET"
METODO_COMANDO = "POST"

# The closed list of keys of the protocol. Search was retired in Roku OS 12.0 and the digits
# (Lit_0 to Lit_9) only reach the device while an on screen keyboard is open, so neither is
# here: a key this list does not carry never reaches the wire.
TECLAS_DO_ECP = (
    "Home",
    "Rev",
    "Fwd",
    "Play",
    "Select",
    "Left",
    "Right",
    "Down",
    "Up",
    "Back",
    "InstantReplay",
    "Info",
    "Backspace",
    "Enter",
    "FindRemote",
    "VolumeUp",
    "VolumeDown",
    "VolumeMute",
    "PowerOn",
    "PowerOff",
    "ChannelUp",
    "ChannelDown",
    "InputTuner",
    "InputHDMI1",
    "InputHDMI2",
    "InputHDMI3",
    "InputHDMI4",
    "InputAV1",
)

# The name of the key lands in the path of the URL of the device, so only a name of the
# closed list above ever gets there; nothing typed by anybody is joined into a path.
_NO_FIO = frozenset(TECLAS_DO_ECP)

TECLA_LIGAR = "PowerOn"
TECLA_DESLIGAR = "PowerOff"
TECLA_MUDO = "VolumeMute"
TECLA_TOCAR = "Play"
TECLA_AVANCAR = "Fwd"
TECLA_VOLTAR = "Rev"

# The word this driver sends, and the literal of the protocol it becomes.
DA_TECLA = {
    "mais": "VolumeUp",
    "menos": "VolumeDown",
    "canal_mais": "ChannelUp",
    "canal_menos": "ChannelDown",
    "cima": "Up",
    "baixo": "Down",
    "esquerda": "Left",
    "direita": "Right",
    "ok": "Select",
    "voltar": "Back",
    "inicio": "Home",
    "info": "Info",
    "play_pause": "Play",
    "proxima": "Fwd",
    "anterior": "Rev",
}
TECLAS = tuple(DA_TECLA)

# A shortcut is the key the vocabulary has no word for, and nothing else.
# Letting the whole closed list through here put Play, PowerOn, PowerOff and the three volume
# keys on the wire BEHIND the two guards this driver has: an atalho Play pauses the device
# while the state keeps saying it plays, so the tocar that follows is held by the guard of
# _transportar and the customer presses play with no effect; an atalho PowerOff turns the
# device off while ligado stays true and, on real hardware, that POST never returns and the
# command that worked answers eq_offline. Each of these keys already has a capacity of its
# own, and going through it is what keeps the state and the wire saying the same thing.
_JA_TEM_CAPACIDADE = frozenset((*DA_TECLA.values(), TECLA_LIGAR, TECLA_DESLIGAR, TECLA_MUDO))
ATALHOS = tuple(tecla for tecla in TECLAS_DO_ECP if tecla not in _JA_TEM_CAPACIDADE)

# The same name written in any case, because a shortcut is typed by hand in the registration
# and the device does not care about the case either.
_CANONICA = {tecla.lower(): tecla for tecla in ATALHOS}


# A device on the LAN answers these documents, and six scalars are read by pattern
# instead of by a parser that would also read entities somebody wrote into the document.
#
# Every pattern below stops at "<" and carries a ceiling on the repetition, and that is not
# style: the body is read up to CORPO_MAXIMO, re.search does not release the GIL, and a
# pattern free to walk the whole body again from each start position hands anything answering
# on 8060 a way to freeze the daemon, the DP bus, the panel and every other driver for
# seconds at each poll. Measured on this file before the ceiling: 128 KB with no ">" spent
# 8.9 s of CPU in one search. The log reader contains the same danger with regex_seguro and a
# deadline; a driver has no such fence and pays for the pattern it writes. Stopping at "<"
# keeps the scan inside one tag, which is what
# makes the cost of the whole document linear, and the ceilings are far above the scalars this
# protocol answers: a serial, a power mode, a program title.
CAMPO_MAXIMO = 512
ATRIBUTOS_MAXIMO = 512


def _campo(nome: str) -> re.Pattern[str]:
    return re.compile(rf"<{nome}>([^<]{{0,{CAMPO_MAXIMO}}})</{nome}>", re.IGNORECASE)


CAMPO_ENERGIA = _campo("power-mode")
CAMPO_SERIAL = _campo("serial-number")
CAMPO_PROGRAMA = _campo("program-title")

# The id of an <app>, which is the value /launch takes and the value the list of inputs of the
# registration carries. The home screen and the screen saver answer an <app> with no id, and
# reading the name out of one of those would point the index at nothing.
_ATRIBUTOS = rf"[^<>]{{0,{ATRIBUTOS_MAXIMO}}}"
APP_COM_ID = re.compile(rf"<app\b{_ATRIBUTOS}\bid=\"([^\"]{{1,64}})\"", re.IGNORECASE)
TOCADOR_ESTADO = re.compile(rf"<player\b{_ATRIBUTOS}\bstate=\"([^\"]{{1,16}})\"", re.IGNORECASE)
TOCADOR_ERRO = re.compile(rf"<player\b{_ATRIBUTOS}\berror=\"([^\"]{{1,16}})\"", re.IGNORECASE)

# The value of an input lands in the path of the URL, so a byte outside this alphabet
# could climb out of the path and write a request that nobody wrote in this file.
_APP = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_SERIAL = re.compile(r"[A-Za-z0-9]{6,32}")

TEMPO_LIMITE_S = 4.0
CORPO_MAXIMO = 128 * 1024
ESTADO_ATENDIDO = 200
FALHAS_ATE_OFFLINE = 2
TEXTO_MAXIMO = 120
FONTES_MAXIMO = 60

# The base polls every 10 s and the list of apps only changes when somebody installs one,
# so it is read every quarter of an hour and never on every poll.
POLLS_ENTRE_LISTAS = 90

# The only power mode that means on; Ready, DisplayOff and PowerOff are all standby, and a
# device in standby keeps answering the protocol.
LIGADO = "poweron"

# A physical input of a Roku TV is an app whose id starts with this, and it has no transport.
PREFIXO_DE_ENTRADA = "tvinput"
ENTRADA_DE_ANTENA = "tvinput.dtv"

TOCANDO = "play"
PAUSADO = "pause"
ERRO_VERDADEIRO = "true"

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PROXIMA = "proxima"
ACAO_ANTERIOR = "anterior"
ACAO_TECLA = "tecla"
ACAO_ATALHO = "atalho"
ACAO_EXTRA = "comando_extra"

# The physical inputs of a Roku TV are always named the same way and nobody guesses the
# shape of that value, while the id of a streaming app changes with what is installed and the
# device already answers the real list on every poll, so it is not suggested here.
SUGESTOES = (
    Sugestao("entradas", "HDMI 1", "tvinput.hdmi1"),
    Sugestao("entradas", "HDMI 2", "tvinput.hdmi2"),
    Sugestao("entradas", "HDMI 3", "tvinput.hdmi3"),
    Sugestao("entradas", "Antena", "tvinput.dtv"),
    Sugestao("atalhos", "Repetir", "InstantReplay"),
    Sugestao("atalhos", "Achar controle", "FindRemote"),
    Sugestao("atalhos", "Apagar", "Backspace"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "Roku TV and Roku streaming player over the ECP on port 8060: power, mute, app or "
            "physical input, the keys of the remote and the transport of what plays."
        ),
        "preparo": (
            "Turn Control by mobile apps on: Settings, System, Advanced system settings, Control "
            "by mobile apps, Network access: Enabled. Without it every key answers an error while "
            "the state keeps working. To turn the set on from here keep Fast TV Start on."
        ),
        "ajuste_controle_local": (
            "Roku OS 14.1 and later: turn on Settings > System > Advanced system settings > "
            "Control by mobile apps. With it off the device still reads as online and still "
            "changes app, and every key of the remote fails."
        ),
        "sem_volume": (
            "The protocol has no volume, in either direction: the device answers no level and "
            "takes no level, so there is no volume bar for a Roku. The volume is the mais and "
            "menos keys, which is the only honest control the device offers."
        ),
        "cap_mudo": (
            "Mute is one key that toggles, and the device reports the result nowhere, so the "
            "value is ignored and the hub never claims to know whether it is muted."
        ),
        "cap_transporte": (
            "Play and pause are the same key of the remote, so the hub only presses it when "
            "what it read says the device is not already where you asked. On a TV input, or "
            "with the player stopped, there is nothing to read and the key is a blind toggle. "
            "There is no stop, and next and previous are fast forward and rewind, not track "
            "skip."
        ),
        "cap_fonte": (
            "The value is the id of an app of the device: a number for a streaming app, or "
            "tvinput.hdmi1, tvinput.hdmi2, tvinput.dtv for a physical input of a Roku TV. The "
            "device answers the list it really has, and the panel shows it."
        ),
        "cap_tecla": (
            "The keys of the Roku remote. The protocol has no menu, guide or exit key, and "
            "the digits only reach the device while a keyboard is on the screen."
        ),
        "cap_atalho": (
            "A shortcut is one key of the closed list of the protocol that the vocabulary of "
            "the hub has no word for: InstantReplay, Enter, Backspace, FindRemote, "
            "InputTuner, InputHDMI1 to InputHDMI4, InputAV1."
        ),
        "cap_comando_extra": (
            "The same as a shortcut, typed on the spot instead of registered on the list."
        ),
    },
    "pt": {
        "descricao": (
            "TV Roku e player de streaming Roku pelo ECP na porta 8060: energia, mudo, app ou "
            "entrada física, as teclas do controle e o transporte do que toca."
        ),
        "preparo": (
            "Ligue o Controle por aplicativos móveis: Configurações, Sistema, Configurações "
            "avançadas do sistema, Controle por aplicativos móveis, Acesso à rede: Ativado. Sem "
            "isso toda tecla responde erro enquanto o estado segue funcionando. Para ligar a TV "
            "daqui, deixe o Início rápido (Fast TV Start) ligado."
        ),
        "ajuste_controle_local": (
            "Roku OS 14.1 em diante: ligue Ajustes > Sistema > Ajustes avançados do sistema > "
            "Controle por aplicativos móveis (Control by mobile apps). Com ele desligado o "
            "aparelho continua lendo como online e continua trocando de app, e toda tecla do "
            "controle falha."
        ),
        "sem_volume": (
            "O protocolo não tem volume, em sentido nenhum: o aparelho não responde nível e "
            "não recebe nível, então não há barra de volume para um Roku. O volume são as "
            "teclas mais e menos, que é o único controle honesto que o aparelho oferece."
        ),
        "cap_mudo": (
            "O mudo é uma tecla que alterna, e o aparelho não reporta o resultado em lugar "
            "nenhum, então o valor é ignorado e o hub nunca diz saber se ele está mudo."
        ),
        "cap_transporte": (
            "Tocar e pausar são a mesma tecla do controle, então o hub só a aperta quando o "
            "que ele leu diz que o aparelho ainda não está onde você pediu. Numa entrada de "
            "TV, ou com o tocador parado, não há o que ler e a tecla é um alterna às cegas. "
            "Não existe parar, e próxima e anterior são avanço e retrocesso rápido, não pulo "
            "de faixa."
        ),
        "cap_fonte": (
            "O valor é o id de um app do aparelho: um número para um app de streaming, ou "
            "tvinput.hdmi1, tvinput.hdmi2, tvinput.dtv para uma entrada física de uma Roku "
            "TV. O aparelho responde a lista que ele tem de verdade, e o painel a mostra."
        ),
        "cap_tecla": (
            "As teclas do controle Roku. O protocolo não tem tecla de menu, de guia nem de "
            "sair, e os dígitos só alcançam o aparelho enquanto um teclado está na tela."
        ),
        "cap_atalho": (
            "Um atalho é uma tecla da lista fechada do protocolo para a qual o vocabulário do "
            "hub não tem palavra: InstantReplay, Enter, Backspace, FindRemote, InputTuner, "
            "InputHDMI1 a InputHDMI4, InputAV1."
        ),
        "cap_comando_extra": (
            "O mesmo que um atalho, digitado na hora em vez de cadastrado na lista."
        ),
    },
}


class _Falha(Exception):
    """A stable code on its way out of an exchange with the device."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Roku(Driver):
    """One Roku TV or streaming player, read and commanded over the ECP."""

    # The protocol has no volume and no stop, and says the right move is to
    # omit the capability, never to implement a method that only refuses.
    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "TV e player Roku", "en": "Roku TV and player"},
        categoria="tv",
        capacidades=(
            ACAO_LIGAR,
            ACAO_DESLIGAR,
            ACAO_MUDO,
            ACAO_FONTE,
            ACAO_TOCAR,
            ACAO_PAUSAR,
            ACAO_PROXIMA,
            ACAO_ANTERIOR,
            ACAO_TECLA,
            ACAO_ATALHO,
            ACAO_EXTRA,
        ),
        teclas=TECLAS,
        marca="Roku",
        descoberta=Descoberta(ssdp_st=("roku:ecp",)),
        textos=TEXTOS,
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._sessao: ClientSession | None = None
        self._falhas = 0
        self._fontes: tuple[str, ...] = ()
        self._ate_as_listas = 0

    async def parar(self) -> None:
        sessao = self._sessao
        self._sessao = None
        if sessao is not None and not sessao.closed:
            await sessao.close()

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The serial the device answers, which is its identity on this hub."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        lido = await _trocar(METODO_LEITURA, _url(endereco, CAMINHO_APARELHO))
        if lido is None or not _atendido(lido[0]):
            return None
        serial = _texto(CAMPO_SERIAL, lido[1])
        return serial.upper() if _SERIAL.fullmatch(serial) else None

    async def atualizar(self) -> None:
        """One poll: the device always, and each of the rest only where it has an answer."""
        try:
            informacao = await self._pedir(METODO_LEITURA, CAMINHO_APARELHO)
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        energia = _texto(CAMPO_ENERGIA, informacao).lower()
        ligado = None if not energia else energia == LIGADO
        await self._listar_apps()
        # A device in standby keeps answering the active app of the last time it was on,
        # so asking it there would publish an input the customer is not looking at.
        try:
            fonte = await self._app_ativo() if ligado else None
        except _Falha as falha:
            # A lost read is not an empty screen. Publishing None here would erase the
            # input of a device that is still answering, and the DP 146 would
            # report "no input" and go back to the real one on the next poll, spending two
            # reports of a licence that counts 250 a day on a timeout. The list of apps just
            # above keeps what it had for the same reason, and these two are the same read.
            log.warning(
                "%s: the active app was not read (%s); keeping the input of the last poll",
                self.cadastro.identidade,
                falha.codigo,
            )
            self._defina(online=True, ligado=ligado, fontes=self._fontes, detalhe="")
            return
        reproduzindo = None
        tocando = None
        if fonte is not None and not fonte.startswith(PREFIXO_DE_ENTRADA):
            reproduzindo = await self._transporte()
        elif fonte == ENTRADA_DE_ANTENA:
            tocando = await self._programa()
        self._defina(
            online=True,
            ligado=ligado,
            fonte=fonte,
            fontes=self._fontes,
            reproduzindo=reproduzindo,
            tocando=tocando,
            detalhe="",
        )

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_LIGAR:
            await self._tecla(TECLA_LIGAR)
            self._defina(ligado=True)
            return None
        if acao == ACAO_DESLIGAR:
            # The device is gone before this POST returns, and reading that silence as a
            # fault would answer eq_offline for the one command that worked.
            await self._tecla(TECLA_DESLIGAR, tolera_silencio=True)
            self._defina(ligado=False, reproduzindo=None, tocando=None)
            return None
        if acao == ACAO_MUDO:
            return await self._alternar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._abrir_app(valor)
        if acao in (ACAO_TOCAR, ACAO_PAUSAR):
            return await self._transportar(acao == ACAO_TOCAR)
        if acao == ACAO_PROXIMA:
            await self._tecla(TECLA_AVANCAR)
            return None
        if acao == ACAO_ANTERIOR:
            await self._tecla(TECLA_VOLTAR)
            return None
        if acao == ACAO_TECLA:
            return await self._do_vocabulario(valor)
        if acao in (ACAO_ATALHO, ACAO_EXTRA):
            return await self._da_lista_fechada(valor)
        return await super().executar(acao, valor)

    async def _alternar_mudo(self, valor: object) -> str | None:
        """The key toggles and nothing reports the result, so the state keeps no opinion."""
        if not isinstance(valor, bool):
            return INVALID_VALUE
        # Writing an optimistic mudo here would publish, on the DP, a fact
        # the device never confirms and the remote in the room desynchronises on the first
        # press. says a driver that cannot tell leaves the field None.
        await self._tecla(TECLA_MUDO)
        return None

    async def _transportar(self, tocar: bool) -> str | None:
        """Play and pause are one key, so it is only pressed when it changes something."""
        lido = self.estado().reproduzindo
        if lido is tocar:
            return None
        await self._tecla(TECLA_TOCAR)
        # Where there was no transport to read (a TV input, a stopped player) the key is
        # a blind toggle, and writing the asked for value here would publish a fact the device
        # never answered: the panel would show a HDMI input as playing, and the second tocar
        # of the customer would be swallowed by the guard above until the next poll.
        # says a driver that cannot tell leaves reproduzindo None, which is the same decision
        # _alternar_mudo takes for the mute of this same protocol.
        if lido is not None:
            self._defina(reproduzindo=tocar)
        return None

    async def _abrir_app(self, valor: object) -> str | None:
        """An input is an app of the device, opened by its id: a number or a tvinput."""
        if not isinstance(valor, str) or not _APP.fullmatch(valor.strip()):
            return INVALID_VALUE
        app = valor.strip()
        await self._pedir(METODO_COMANDO, CAMINHO_ABRIR.format(valor=app))
        # The transport and the title belong to the source that was playing, and the
        # source has just changed; keeping them would publish the title of the previous app.
        self._defina(fonte=app, reproduzindo=None, tocando=None)
        return None

    async def _do_vocabulario(self, valor: object) -> str | None:
        tecla = DA_TECLA.get(valor) if isinstance(valor, str) else None
        if tecla is None:
            return INVALID_VALUE
        await self._tecla(tecla)
        return None

    async def _da_lista_fechada(self, valor: object) -> str | None:
        """A shortcut is one key of the protocol, and never text somebody typed."""
        tecla = _CANONICA.get(valor.strip().lower()) if isinstance(valor, str) else None
        if tecla is None:
            return INVALID_VALUE
        await self._tecla(tecla)
        return None

    async def _listar_apps(self) -> None:
        """The apps and the physical inputs the device really has, read now and then."""
        if self._ate_as_listas > 0:
            self._ate_as_listas -= 1
            return
        try:
            lido = await self._pedir(METODO_LEITURA, CAMINHO_APPS)
        except _Falha:
            return
        self._fontes = _apps(lido)
        self._ate_as_listas = POLLS_ENTRE_LISTAS

    async def _app_ativo(self) -> str | None:
        """The id of the app on the screen, or None for the home screen and the screen saver.

        A read that did not happen raises instead of answering None: "there is no app" and "I
        could not ask" are different facts, and only the first one is worth publishing.
        """
        lido = await self._pedir(METODO_LEITURA, CAMINHO_APP_ATIVO)
        achado = APP_COM_ID.search(lido)
        if achado is None:
            return None
        app = achado.group(1).strip()
        return app if _APP.fullmatch(app) else None

    async def _transporte(self) -> bool | None:
        """The transport : playing, paused, or a device that cannot tell."""
        try:
            lido = await self._pedir(METODO_LEITURA, CAMINHO_TOCADOR)
        except _Falha:
            return None
        if _texto(TOCADOR_ERRO, lido).lower() == ERRO_VERDADEIRO:
            return None
        estado = _texto(TOCADOR_ESTADO, lido).lower()
        if estado == TOCANDO:
            return True
        return False if estado == PAUSADO else None

    async def _programa(self) -> str | None:
        """The title of what the antenna is showing, which is the only title the ECP has."""
        try:
            lido = await self._pedir(METODO_LEITURA, CAMINHO_CANAL)
        except _Falha:
            # This is the one read the "Control by mobile apps" setting governs, so a
            # refusal here is a device with the setting off and not a poll worth losing.
            return None
        return _texto(CAMPO_PROGRAMA, lido) or None

    async def _tecla(self, tecla: str, *, tolera_silencio: bool = False) -> None:
        """One key of the closed list, pressed by a POST with an empty body."""
        if tecla not in _NO_FIO:
            raise _Falha(INVALID_VALUE)
        await self._pedir(
            METODO_COMANDO, CAMINHO_TECLA.format(tecla=tecla), tolera_silencio=tolera_silencio
        )

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is a device that went away."""
        self._falhas += 1
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, reproduzindo=None, tocando=None, detalhe=codigo)

    async def _pedir(self, metodo: str, caminho: str, *, tolera_silencio: bool = False) -> str:
        """One exchange with the device, on the one port the protocol has."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        url = _url(endereco, caminho)
        rotina = metodo == "GET" and caminho in PERGUNTAS_DE_ROTINA
        transcricao = self._transcricao()
        transcricao.enviado(f"{metodo} {caminho}", rotina=rotina)
        lido = await _trocar(metodo, url, await self._abrir())
        if lido is None:
            if tolera_silencio:
                return ""
            transcricao.falhou(f"{metodo} {caminho}", "no answer")
            raise _Falha(EQ_OFFLINE)
        estado, texto = lido
        transcricao.recebido(f"{estado} {texto}", rotina=rotina)
        if not _atendido(estado):
            # The device answers an error to a key, and to the channel of the antenna,
            # when "Control by mobile apps" is off, while it keeps answering state and keeps
            # changing app. Saying so here is what stops the integrator from hunting the
            # network for a device that is answering perfectly.
            log.warning(
                "%s: the device answered HTTP %d to %s, and only a 2xx is an answer; a refusal "
                "here is what a device with Control by mobile apps off answers to a key, in "
                "the advanced system settings of the device",
                self.cadastro.identidade,
                estado,
                caminho,
            )
            raise _Falha(ERRO_APARELHO)
        return texto

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


async def _trocar(
    metodo: str, url: str, sessao: ClientSession | None = None
) -> tuple[int, str] | None:
    """The status and the body of one exchange, or None when the device did not answer."""
    fechar = sessao is None
    aberta = sessao or ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
    try:
        async with aberta.request(
            metodo,
            url,
            # A device answering a redirect would send the hub to whatever host it
            # names, which is the LAN proxy refuses.
            allow_redirects=False,
        ) as resposta:
            bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
            estado = resposta.status
    except (TimeoutError, ClientError, OSError, ValueError) as erro:
        log.debug("the device did not answer %s: %s", url, erro or type(erro).__name__)
        return None
    finally:
        if fechar and not aberta.closed:
            await aberta.close()
    return estado, bruto.decode("utf-8", errors="replace")


def _atendido(estado: int) -> bool:
    """Only a 2xx is an answer; a redirect is the proof that the request was NOT served.

    The exchange goes out with allow_redirects off, so a 301, a 302 or a 307 comes back
    here as a body of its own. Reading anything under 400 as success made a poll of a device
    that answers a redirect publish online with no power mode, forever, and made a keypress
    that never reached the device answer None, which is the word for done.
    """
    return ESTADO_ATENDIDO <= estado < ESTADO_ATENDIDO + 100


def _url(endereco: str, caminho: str) -> str:
    anfitriao = f"[{endereco}]" if ":" in endereco else endereco
    return f"http://{anfitriao}:{PORTA}{caminho}"


def _texto(padrao: re.Pattern[str], documento: str) -> str:
    achado = padrao.search(documento)
    return "" if achado is None else achado.group(1).strip()[:TEXTO_MAXIMO]


def _apps(documento: str) -> tuple[str, ...]:
    """The ids of the apps and of the physical inputs, in the order the device listed them."""
    achados = APP_COM_ID.findall(documento)
    return tuple(app for app in achados if _APP.fullmatch(app))[:FONTES_MAXIMO]

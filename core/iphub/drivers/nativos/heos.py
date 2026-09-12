# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""HEOS multiroom speaker (Denon, Marantz) over the HEOS CLI, the multiroom driver of the
sections 6 and 14.

One transport, the command line interface of the speaker on TCP 1255: a line of text out, a
line of JSON back. It is native and not declarative because the answer of a command is
correlated by name and not by the order it arrives in, because the state is read from five
questions, and because a group is written as the whole list of its members.

What this file had to learn before it worked, and what nobody should have to learn twice:

- the port is fixed by the protocol, so it is not a field of the registration; there is
nothing to discover about a HEOS speaker except its address;
- there is no request identifier on this wire. The correlation is the command name coming
back in the answer AND the player id it was aimed at, so exactly one command is in flight
per connection, under a lock, and an answer that names another command, or the same command
about another speaker, is dropped instead of read as ours;
- a deadline of this file only ever fires when it is SHORTER than the one the gestor puts
around a call into a driver, and the cancellation of the gestor is not a timeout: it is
caught apart, and it drops the socket instead of leaving the answer of a dead exchange
inside a connection the next poll would reuse;
- a line whose command starts with "event/" was not asked for, and a line that carries
"command under process" is not the final answer of anything: both are discarded;
- the interface runs dormant and wakes up when the first socket connects, so a connection
asks for the events to stay off before it asks anything else, and a player list that comes
back empty right after that is a re-read and not a speaker that went away;
- the player id is NOT identity: a firmware update renumbers it. It is resolved again from
player/get_players at every poll, matched by the address of the speaker and checked
against the serial, so a renumbering repairs itself with nobody looking;
- the serial is only published by some models, so identificar answers it when the speaker
has one and nothing when it has not; the uuid of the SSDP answer covers the rest;
- the value encoding is not the one of a URL: only &, = and % are escaped and a space
travels as it is, so anything else on the wire is written by this file;
- a receiver with HEOS built in answers this driver AND the HTTP driver of the receiver, and
that is the park and not a defect: power and input belong to one registration, multiroom
and transport to the other, and the texts of this manifest say so;
- the volume is already the 0 to 100 in both directions, but it is read back
with a decimal point, so it is converted by value and never by scale;
- a speaker with a fixed line out and no network volume control does not change volume over
the network, and it says so in the player list, so this driver refuses instead of writing
a command that silently does nothing;
- there is no power command in this interface at all, which is what already
recorded: omitting the capability is right, implementing one to refuse is not.

Unlike the other multiroom driver of this hub, a member of a HEOS group answers and obeys
for the whole group, so nothing is refused here for being in one; the only thing the leader
lends to a member is the title of what plays, when the member names none.
"""

import asyncio
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass
from urllib.parse import parse_qsl

from iphub.config import ip_literal
from iphub.drivers import fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Descoberta, Manifesto, Sugestao

log = logging.getLogger("iphub.drivers.nativos.heos")

TIPO = "multiroom_heos"

# The protocol fixes this port, so a field of the registration would only be a field
# somebody breaks; nothing about a HEOS speaker is configurable except where it answers.
PORTA_CLI = 1255

# The gestor already cuts a call into a driver at half the poll interval, so a deadline
# of this file that is not SHORTER than that one never fires: what would kill a slow exchange
# is the cancellation of the gestor, which no except of _falar catches and which would leave
# the socket open with the answer of the dead poll still inside it.
TEMPO_LIMITE_S = 4.0
# One poll asks five questions on the same connection, so a deadline that is only per
# exchange lets a speaker that answers slowly hold the poll for five times the limit; the
# budget of the whole poll is what has to fit under the deadline of the gestor.
ORCAMENTO_DO_POLL_S = 4.0
PRAZO_DE_FECHAMENTO_S = 1.0

# A speaker on the LAN must never be able to make this daemon buffer without bound, and
# a line of this protocol is one JSON object, never a document.
LINHA_MAXIMA = 64 * 1024
# An event and an intermediate answer are dropped while the real one is waited for, so
# a speaker that only ever sends those must run out instead of holding the exchange open.
LINHAS_ATE_DESISTIR = 32
MENSAGEM_MAXIMA = 1024
TEXTO_MAXIMO = 120
JOGADORES_MAXIMOS = 64
MEMBROS_MAXIMOS = 12

# One lost poll is not a speaker that went away; two in a row is.
FALHAS_ATE_OFFLINE = 2
# A member out of the group for two polls in a row lost it to a reboot or to the
# application of the manufacturer, and the logical state has to be reconciled.
POLLS_ATE_RECONCILIAR = 2

BASE = "heos://"
TERMINADOR = b"\r\n"
TERMINADOR_TEXTO = "\r\n"

PEDE_JOGADORES = "player/get_players"
PEDE_TRANSPORTE = "player/get_play_state"
PEDE_VOLUME = "player/get_volume"
PEDE_MUDO = "player/get_mute"
PEDE_TOCANDO = "player/get_now_playing_media"
PEDE_GRUPOS = "group/get_groups"
REGISTRA_EVENTOS = "system/register_for_change_events"

MANDA_VOLUME = "player/set_volume"
MANDA_MUDO = "player/set_mute"
MANDA_TRANSPORTE = "player/set_play_state"
MANDA_PROXIMA = "player/play_next"
MANDA_ANTERIOR = "player/play_previous"
MANDA_SOBE = "player/volume_up"
MANDA_DESCE = "player/volume_down"
MANDA_ENTRADA = "browse/play_input"
MANDA_FAVORITO = "browse/play_preset"
MANDA_FLUXO = "browse/play_stream"
MANDA_GRUPO = "group/set_group"

# The questions of the poll, which answer the same thing every ten seconds.
PERGUNTAS = (PEDE_JOGADORES, PEDE_TRANSPORTE, PEDE_VOLUME, PEDE_MUDO, PEDE_TOCANDO)

ATRIBUTO_PID = "pid"
ATRIBUTO_NIVEL = "level"
ATRIBUTO_ESTADO = "state"
ATRIBUTO_ENTRADA = "input"
ATRIBUTO_FAVORITO = "preset"
ATRIBUTO_PASSO = "step"
ATRIBUTO_EVENTOS = "enable"
ATRIBUTO_URL = "url"

DESLIGADO = "off"
LIGADO = "on"

TOCAR = "play"
PAUSAR = "pause"
PARAR = "stop"

PREFIXO_EVENTO = "event/"
SOB_PROCESSO = "command under process"
SUCESSO = "success"
GRUPO_DE_GRUPO = "group"
GRUPO_DE_JOGADOR = "player"

CHAVE_HEOS = "heos"
CHAVE_COMANDO = "command"
CHAVE_RESULTADO = "result"
CHAVE_MENSAGEM = "message"
CHAVE_PAYLOAD = "payload"
CHAVE_ERRO = "eid"
CHAVE_ERRO_DE_SISTEMA = "syserrno"

CHAVE_PID = "pid"
CHAVE_GID = "gid"
CHAVE_IP = "ip"
CHAVE_SERIAL = "serial"
CHAVE_NOME = "name"
CHAVE_VERSAO = "version"
CHAVE_SAIDA = "lineout"
CHAVE_CONTROLE = "control"
CHAVE_JOGADORES = "players"
CHAVE_PAPEL = "role"
CHAVE_TITULO = "song"
CHAVE_ESTACAO = "station"
CHAVE_FONTE = "sid"
CHAVE_MIDIA = "mid"

PAPEL_LIDER = "leader"

# The auxiliary input is one source id of this protocol, and the media id under it is
# literally the name of the input, which is the value the list of the registration carries.
FONTE_DE_ENTRADA = 1027

# The player list says whether the speaker changes volume over the network at all; a
# fixed line out driven by anything other than the network ignores the volume command.
SAIDA_FIXA = 2
CONTROLE_DE_REDE = 4

# The firmware the published protocol targets; older than this and a command may not exist.
VERSAO_ALVO = (3, 34, 0)

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100
# The wire of this protocol is the same 0 to 100 the contract fixes, in
# both directions; saying it with a constant keeps a firmware of another range one line away.
VOLUME_MINIMO_DO_APARELHO = 0
VOLUME_MAXIMO_DO_APARELHO = 100

# The step of a relative volume key, which is the default the published protocol names.
PASSO_PADRAO = 5

FAVORITO_MINIMO = 1
FAVORITO_MAXIMO = 99

URL_MAXIMA = 200

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
AUTH_PENDENTE = "auth_pendente"
ERRO_APARELHO = "erro_aparelho"
NAO_SUPORTADO = "nao_suportado"

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
ACAO_TECLA = "tecla"

TECLA_MAIS = "mais"
TECLA_MENOS = "menos"
TECLAS = (TECLA_MAIS, TECLA_MENOS)

# What the speaker answers when it refuses, mapped onto the five stable codes.
# A code of its own would be a phrase the panel cannot translate.
CODIGO_POR_ERRO = {
    1: NAO_SUPORTADO,
    2: EQ_OFFLINE,
    3: NAO_SUPORTADO,
    6: AUTH_PENDENTE,
    8: AUTH_PENDENTE,
    9: INVALID_VALUE,
    10: AUTH_PENDENTE,
    15: NAO_SUPORTADO,
}
# The generic internal error of this protocol means "no account" only with this system
# number beside it, and reading every internal error as a pairing problem would send the
# integrator to a login screen for a speaker that is simply busy.
ERRO_INTERNO = 12
ERRO_SEM_CONTA = -1063

# Only these three bytes are escaped by this protocol, and a space travels as it is; a
# URL encoder here would rewrite the name of a station and the speaker would not find it.
# The percent comes FIRST, or the escape of the ampersand would be escaped again.
TROCAS = (("%", "%25"), ("&", "%26"), ("=", "%3D"))

# The line ends the command, so any byte outside the printable range could write a
# second command of its own onto the wire; the check is on the whole line, once, before it
# reaches the socket.
_NO_FIO = re.compile(r"[\x20-\x7e]{1,512}")
# An input of this protocol is a word of a published vocabulary under inputs/, so a
# value of another shape is a value that would only ever fail at the speaker.
_ENTRADA = re.compile(r"inputs/[a-z0-9_]{1,32}")
# The address of a stream is the last attribute of its command and travels unescaped, so
# a separator inside it would write attributes nobody wrote in this file.
_URL = re.compile(r"https?://[A-Za-z0-9:._~/\-\[\]]+")
_NUMERO = re.compile(r"[0-9]{1,4}")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

# The vocabulary of inputs is published and fixed, and nobody types inputs/optical_in_1
# from memory; the shortcuts are not suggested because a favourite is a position in the list
# of an account of the customer, which this driver never reads.
SUGESTOES = (
    Sugestao("entradas", "AUX 1", "inputs/aux_in_1"),
    Sugestao("entradas", "AUX 2", "inputs/aux_in_2"),
    Sugestao("entradas", "Line In 1", "inputs/line_in_1"),
    Sugestao("entradas", "Optico 1", "inputs/optical_in_1"),
    Sugestao("entradas", "Coaxial 1", "inputs/coax_in_1"),
    Sugestao("entradas", "HDMI 1", "inputs/hdmi_in_1"),
    Sugestao("entradas", "HDMI ARC", "inputs/hdmi_arc_1"),
    Sugestao("entradas", "TV", "inputs/tvaudio"),
    Sugestao("entradas", "CD", "inputs/cd"),
    Sugestao("entradas", "Blu-ray", "inputs/bluray"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "HEOS multiroom speaker (Denon, Marantz) over the command line interface of the "
            "speaker. Always on, so it declares no power: volume, mute, input, play, pause, "
            "stop, track, favourites and native grouping. A receiver with HEOS built in is "
            "two registrations: power and input on the receiver driver, multiroom and "
            "transport here."
        ),
        "preparo": (
            "Set the speaker up in the HEOS app first, on the same network as the hub, and reserve "
            "its address on the router. Favourites need the HEOS account signed in on the app. "
            "Nothing to switch on in the speaker: its interface answers by default."
        ),
        "cap_fonte": (
            "The value is the name of the input in the vocabulary of the protocol, written "
            "whole: inputs/aux_in_1, inputs/optical_in_1, inputs/hdmi_arc_1."
        ),
        "cap_tocar": (
            "Play with no value resumes what is loaded, and with the address of a stream it "
            "plays that stream. The address travels as it is, so it carries no '?' and no '&'."
        ),
        "cap_atalho": (
            "A shortcut is the position of a favourite of the HEOS account, written as a "
            "number from 1, or the address of a stream. A favourite needs the account of the "
            "customer signed in on the speaker, and its position changes when the owner "
            "reorders the list in the application."
        ),
        "cap_agrupar": (
            "Grouping takes the address of the leader to follow, and with no value it leaves "
            "the group. Only HEOS speakers, and the whole list of members is rewritten at "
            "every change, so the ones already in it stay in it."
        ),
        "cap_tecla": "Volume up and volume down, in the step of five the protocol defaults to.",
        "lista_entradas": (
            "The name of the input under inputs/, as the protocol publishes it. A speaker "
            "only has the ones its model carries."
        ),
        "lista_atalhos": (
            "The position of a favourite of the HEOS account (1, 2, 3) or the address of a "
            "stream (plain http, no '?'). The page of a station is not its stream."
        ),
    },
    "pt": {
        "descricao": (
            "Caixa multiroom HEOS (Denon, Marantz) pela interface de linha de comando da "
            "caixa. Sempre ligada, então não declara energia: volume, mudo, entrada, tocar, "
            "pausar, parar, faixa, favoritos e agrupamento nativo. Um receiver com HEOS "
            "embutido são dois cadastros: energia e entrada no driver do receiver, multiroom "
            "e transporte aqui."
        ),
        "preparo": (
            "Configure a caixa no aplicativo HEOS antes, na mesma rede do hub, e reserve o "
            "endereço dela no roteador. Favoritos exigem a conta HEOS conectada no aplicativo. "
            "Nada a ligar na caixa: a interface dela responde por padrão."
        ),
        "cap_fonte": (
            "O valor é o nome da entrada no vocabulário do protocolo, escrito inteiro: "
            "inputs/aux_in_1, inputs/optical_in_1, inputs/hdmi_arc_1."
        ),
        "cap_tocar": (
            "Tocar sem valor retoma o que está carregado, e com o endereço de um fluxo toca "
            "esse fluxo. O endereço viaja como está, então ele não leva '?' nem '&'."
        ),
        "cap_atalho": (
            "Um atalho é a posição de um favorito da conta HEOS, escrita como um número a "
            "partir de 1, ou o endereço de um fluxo. Um favorito exige a conta do cliente "
            "conectada na caixa, e a posição dele muda quando o dono reordena a lista no "
            "aplicativo."
        ),
        "cap_agrupar": (
            "Agrupar recebe o endereço do líder a seguir, e sem valor sai do grupo. Só caixas "
            "HEOS, e a lista inteira de membros é reescrita a cada mudança, então os que já "
            "estão nele continuam nele."
        ),
        "cap_tecla": "Volume para cima e para baixo, no passo de cinco que o protocolo usa.",
        "lista_entradas": (
            "O nome da entrada sob inputs/, como o protocolo o publica. Uma caixa só tem as "
            "que o modelo dela carrega."
        ),
        "lista_atalhos": (
            "A posição de um favorito da conta HEOS (1, 2, 3) ou o endereço de um fluxo "
            "(http simples, sem '?'). A página de uma estação não é o fluxo dela."
        ),
    },
}


@dataclass(frozen=True)
class Resposta:
    """One line the speaker wrote, read as the protocol defines it."""

    comando: str
    ok: bool
    mensagem: dict[str, str]
    payload: object = None


@dataclass(frozen=True)
class Jogador:
    """One speaker of the system as the player list describes it."""

    pid: int
    ip: str = ""
    serial: str = ""
    nome: str = ""
    gid: int | None = None
    versao: str = ""
    volume_pela_rede: bool = True


@dataclass(frozen=True)
class GrupoLido:
    """One group of the system: who leads it and who follows, by player id."""

    gid: int | None
    lider: int
    membros: tuple[int, ...] = ()


@dataclass(frozen=True)
class Escravo:
    """One member of a group as the hub keys it: the serial is the identity, the ip is today's."""

    identidade: str
    ip: str
    nome: str = ""


@dataclass(frozen=True)
class Grupo:
    """The group this speaker really leads, read from the system and not from our own books."""

    escravos: tuple[Escravo, ...] = ()


class _Falha(Exception):
    """A stable code on the way out of an exchange, so no exception escapes executar."""

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class _PidMorto(_Falha):
    """The speaker refused the player id the command carried: that number is not valid here
    any more, which is what a firmware update leaves behind when it renumbers the players.

    It is the same offline code on the way out, and a class of its own only so the
    exchange that carried OUR player id can read the list again and go once more, instead of
    handing the integrator an offline speaker that is alive and answering.
    """

    def __init__(self) -> None:
        super().__init__(EQ_OFFLINE)


class Heos(Driver):
    """Volume, mute, input, transport, favourites and native grouping of a HEOS speaker."""

    # This interface has no power command at all, and says omitting the
    # capability is right, never implementing one to refuse it. The port is not a field
    # either: the protocol fixes it, and a field nobody needs is a field somebody breaks.
    MANIFESTO = Manifesto(
        tipo=TIPO,
        rotulo={"pt": "Multiroom HEOS (Denon)", "en": "HEOS multiroom (Denon)"},
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
            ACAO_TECLA,
        ),
        teclas=TECLAS,
        # The manufacturer signature of the SSDP answer belongs to the driver of the
        # receiver, and a receiver with HEOS built in answers both; claiming the name here
        # would make one box appear twice under the same finding of the sweep.
        descoberta=Descoberta(
            ssdp_st=("urn:schemas-denon-com:device:ACT-Denon:1",),
            mdns_servicos=("_heos-audio._tcp",),
        ),
        textos=TEXTOS,
        marca="HEOS",
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._leitor: asyncio.StreamReader | None = None
        self._escritor: asyncio.StreamWriter | None = None
        # There is no request identifier on this wire, so two exchanges at once read the
        # answer of each other; one command in flight per connection is the protocol itself.
        self._trava = asyncio.Lock()
        self._pid: int | None = None
        self._serial = ""
        self._jogadores: tuple[Jogador, ...] = ()
        self._volume_pela_rede = True
        self._falhas = 0
        self._escravo = False
        self._no_grupo = False
        self._polls_fora = 0
        self._saiu_do_grupo = False
        self._espelho: str | None = None
        self._espelho_reproduzindo: bool | None = None
        self._avisou_da_versao = False
        # A favourite and a stream are asked for by a shortcut the integrator named, and
        # this speaker answers no title for a raw stream until the station sends metadata,
        # which many never do; an empty "now playing" over a speaker that plays is the panel
        # calling itself broken.
        self._pedido: str | None = None

    async def iniciar(self) -> None:
        # The interface runs dormant until a socket connects, and the player list right
        # after that may be incomplete, so the first resolution happens here and not inside
        # the first command of the integrator.
        try:
            await self._resolver_pid()
        except _Falha as falha:
            log.warning("%s: the speaker did not name itself: %s", self.cadastro.identidade, falha)

    async def parar(self) -> None:
        await self._desligar()

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The serial the speaker at that address publishes, or nothing.

        This interface only publishes a serial on some models, and inventing an identity
        out of the player id would key the registration by a number a firmware update
        renumbers. The uuid of the SSDP answer covers the models that have no serial.
        """
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        escritor = None
        try:
            async with asyncio.timeout(TEMPO_LIMITE_S):
                leitor, escritor = await asyncio.open_connection(
                    endereco, PORTA_CLI, limit=LINHA_MAXIMA
                )
                await _escrever(escritor, REGISTRA_EVENTOS, ((ATRIBUTO_EVENTOS, DESLIGADO),))
                await _ouvir(leitor, REGISTRA_EVENTOS)
                await _escrever(escritor, PEDE_JOGADORES)
                resposta = await _ouvir(leitor, PEDE_JOGADORES)
        except (TimeoutError, OSError, _Falha, asyncio.IncompleteReadError):
            return None
        except asyncio.LimitOverrunError:
            return None
        finally:
            if escritor is not None:
                await _fechar(escritor)
        jogador = _jogador_no(_jogadores_de(resposta.payload), endereco)
        return jogador.serial or None if jogador is not None else None

    async def atualizar(self) -> None:
        prazo_final = asyncio.get_running_loop().time() + ORCAMENTO_DO_POLL_S
        try:
            pid = await self._resolver_pid(prazo_final)
            transporte = await self._perguntar(PEDE_TRANSPORTE, pid, prazo_final)
            volume = await self._perguntar(PEDE_VOLUME, pid, prazo_final)
            mudo = await self._perguntar(PEDE_MUDO, pid, prazo_final)
            tocando = await self._perguntar(PEDE_TOCANDO, pid, prazo_final)
        except _Falha as falha:
            self._falhar(falha.codigo)
            return
        self._falhas = 0
        self._defina(
            online=True,
            volume=_do_aparelho(volume.mensagem.get(ATRIBUTO_NIVEL)),
            mudo=_verdade(mudo.mensagem.get(ATRIBUTO_ESTADO)),
            fonte=_entrada_ativa(tocando.payload),
            reproduzindo=self._reproduzindo(transporte),
            tocando=self._tocando(tocando.payload),
            detalhe="",
        )

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    def identidade_do_aparelho(self) -> str | None:
        """The serial the speaker published, which is what says the box here is still ours."""
        return self._serial or None

    def e_escravo(self) -> bool:
        """The last poll saw the speaker following the leader of a group."""
        return self._escravo

    def saiu_do_grupo(self) -> bool:
        """It followed a leader and stopped following one for two polls: the group dissolved."""
        return self._saiu_do_grupo

    def no_grupo(self) -> bool:
        return self._escravo or self._no_grupo

    def marcar_grupo(self, dentro: bool) -> None:
        """Where the owner of the group logic says this speaker stands right now."""
        self._no_grupo = dentro
        # Whoever owns the group logic has just declared where this speaker stands, and
        # that settles it in BOTH directions; a verdict left over from an earlier group would
        # make the reconcile tear down the group the owner formed one moment ago.
        self._saiu_do_grupo = False
        if not dentro:
            self._espelho = None
            self._espelho_reproduzindo = None

    def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
        """What the leader of the group plays, pinned here by whoever owns the group.

        A member of a HEOS group plays the audio of its leader, and it answers a title of
        its own only when the leader named one; the mirror is what fills that in, and it only
        ever stands in while this speaker is a member.
        """
        self._espelho = None if tocando is None else _texto(tocando)
        self._espelho_reproduzindo = reproduzindo

    async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
        """Run on the MEMBER: it joins the group led by the speaker at that address."""
        endereco = ip_literal(ip_do_mestre)
        if endereco is None:
            return INVALID_VALUE
        try:
            await self._entrar(endereco)
        except _Falha as falha:
            return falha.codigo
        return None

    async def desfazer_grupo(self) -> str | None:
        """Run on either side: the leader dismantles what it leads, a member walks out of it."""
        try:
            await self._sair()
        except _Falha as falha:
            return falha.codigo
        return None

    async def tirar_do_grupo(self, ip_do_escravo: object) -> str | None:
        """Run on the LEADER: it removes one member and keeps the rest of the group playing."""
        endereco = ip_literal(ip_do_escravo)
        if endereco is None:
            return INVALID_VALUE
        try:
            await self._tirar(endereco)
        except _Falha as falha:
            return falha.codigo
        return None

    async def volume_de_escravo(self, ip_do_escravo: object, valor: object) -> str | None:
        """Run on the LEADER: the volume of one member of the group it leads.

        Unlike the other multiroom protocol of this hub, each speaker keeps its own
        volume while it is in a group, so this is the volume command aimed at the member.
        """
        endereco = ip_literal(ip_do_escravo)
        if endereco is None or not _volume_valido(valor):
            return INVALID_VALUE
        try:
            alvo = await self._jogador_alvo(endereco)
            # The fact is the same fact whether the volume goes to this speaker or to a
            # member of its group, and the player list answers it for every speaker of the
            # system. Deciding it one way here and another way in the command
            # leaves the panel with a bar that moves in silence for the number of a member.
            if not alvo.volume_pela_rede:
                return NAO_SUPORTADO
            await self._mandar(
                MANDA_VOLUME,
                ((ATRIBUTO_PID, alvo.pid), (ATRIBUTO_NIVEL, _para_o_aparelho(int(valor)))),
            )
        except _Falha as falha:
            return falha.codigo
        return None

    async def ler_grupo(self) -> Grupo | None:
        """Run on the LEADER: the members the system lists under this speaker, or None when
        it did not answer. the key of a member is its serial, never its address.
        """
        try:
            meu = await self._pid_atual()
            grupo = await self._meu_grupo(meu)
        except _Falha as falha:
            log.warning("speaker %s did not list its group: %s", self.cadastro.identidade, falha)
            return None
        if grupo is None or grupo.lider != meu:
            return Grupo()
        membros = []
        for pid in grupo.membros:
            if pid == meu or len(membros) >= MEMBROS_MAXIMOS:
                continue
            jogador = _jogador_com(self._jogadores, pid)
            if jogador is not None:
                membros.append(Escravo(jogador.serial, jogador.ip, jogador.nome))
        return Grupo(tuple(membros))

    async def _agir(self, acao: str, valor: object) -> str | None:
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._trocar_fonte(valor)
        if acao == ACAO_TOCAR:
            return await self._tocar(valor)
        if acao in (ACAO_PAUSAR, ACAO_PARAR):
            # A pause on a stream keeps the speaker connected to the station
            # and a stop lets go of it, so the two are different commands and not one.
            await self._transporte(PAUSAR if acao == ACAO_PAUSAR else PARAR)
            self._pedido = None
            self._defina(reproduzindo=False, tocando=None)
            return None
        if acao == ACAO_PROXIMA:
            await self._mandar(MANDA_PROXIMA, ((ATRIBUTO_PID, await self._pid_atual()),))
            return None
        if acao == ACAO_ANTERIOR:
            await self._mandar(MANDA_ANTERIOR, ((ATRIBUTO_PID, await self._pid_atual()),))
            return None
        if acao == ACAO_TECLA:
            return await self._tecla(valor)
        if acao == ACAO_ATALHO:
            return await self._atalho(valor)
        if acao == ACAO_AGRUPAR:
            return await self._agrupar(valor)
        return await super().executar(acao, valor)

    async def _trocar_volume(self, valor: object) -> str | None:
        if not _volume_valido(valor):
            return INVALID_VALUE
        # A speaker with a fixed line out driven by anything other than the network
        # accepts this command and does nothing, and a panel showing a bar that moves nothing
        # is worse than a panel that says the speaker cannot do it.
        if not self._volume_pela_rede:
            return NAO_SUPORTADO
        pedido = int(valor)
        await self._mandar(
            MANDA_VOLUME,
            ((ATRIBUTO_PID, await self._pid_atual()), (ATRIBUTO_NIVEL, _para_o_aparelho(pedido))),
        )
        self._defina(volume=pedido)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        if not isinstance(valor, bool):
            return INVALID_VALUE
        await self._mandar(
            MANDA_MUDO,
            (
                (ATRIBUTO_PID, await self._pid_atual()),
                (ATRIBUTO_ESTADO, LIGADO if valor else DESLIGADO),
            ),
        )
        self._defina(mudo=valor)
        return None

    async def _trocar_fonte(self, valor: object) -> str | None:
        if not isinstance(valor, str) or not _ENTRADA.fullmatch(valor.strip()):
            return INVALID_VALUE
        entrada = valor.strip()
        await self._mandar(
            MANDA_ENTRADA,
            ((ATRIBUTO_PID, await self._pid_atual()), (ATRIBUTO_ENTRADA, entrada)),
        )
        # The input is what plays now, so the title of the network source that played
        # before it would be a line of the panel describing something nobody can hear.
        self._pedido = None
        self._defina(fonte=entrada, reproduzindo=True, tocando=None)
        return None

    async def _tocar(self, valor: object) -> str | None:
        if valor is None or valor == "":
            await self._transporte(TOCAR)
            self._defina(reproduzindo=True)
            return None
        if not _url_valida(valor):
            return INVALID_VALUE
        return await self._fluxo(str(valor))

    async def _fluxo(self, endereco: str) -> str | None:
        """A stream the speaker fetches by itself, with its address last and unescaped."""
        await self._mandar(MANDA_FLUXO, ((ATRIBUTO_PID, await self._pid_atual()),), url=endereco)
        self._pedido = self._rotulo_de(endereco)
        self._defina(fonte=None, reproduzindo=True, tocando=self._pedido)
        return None

    async def _tecla(self, valor: object) -> str | None:
        if valor not in TECLAS:
            return INVALID_VALUE
        comando = MANDA_SOBE if valor == TECLA_MAIS else MANDA_DESCE
        await self._mandar(
            comando, ((ATRIBUTO_PID, await self._pid_atual()), (ATRIBUTO_PASSO, PASSO_PADRAO))
        )
        return None

    async def _atalho(self, valor: object) -> str | None:
        """A favourite of the account by its position, or a stream by its address."""
        if _url_valida(valor):
            return await self._fluxo(str(valor))
        numero = _favorito_de(valor)
        if numero is None:
            return INVALID_VALUE
        await self._mandar(
            MANDA_FAVORITO,
            ((ATRIBUTO_PID, await self._pid_atual()), (ATRIBUTO_FAVORITO, numero)),
        )
        self._pedido = self._rotulo_de(valor)
        self._defina(fonte=None, reproduzindo=True, tocando=self._pedido)
        return None

    async def _agrupar(self, valor: object) -> str | None:
        if valor is None or valor == "":
            return await self.desfazer_grupo()
        return await self.entrar_no_grupo(valor)

    async def _transporte(self, estado: str) -> None:
        await self._mandar(
            MANDA_TRANSPORTE,
            ((ATRIBUTO_PID, await self._pid_atual()), (ATRIBUTO_ESTADO, estado)),
        )

    def _rotulo_de(self, valor: object) -> str | None:
        """The label the integrator gave this value in the shortcuts of the registration."""
        for item in self.cadastro.listas.get("atalhos", ()):
            if item.valor == valor:
                return item.rotulo
        return None

    async def _entrar(self, endereco: str) -> None:
        """Of this protocol: the command carries the WHOLE list of the group, so
        joining one that already has members means reading it first and writing it back with
        one more name; a list of two would throw everybody else out.
        """
        meu = await self._pid_atual()
        lider = await self._pid_no(endereco)
        if lider == meu:
            return
        grupo = await self._grupo_do_lider(lider)
        membros = () if grupo is None else grupo.membros
        pids = [lider, *(pid for pid in membros if pid not in (lider, meu)), meu]
        await self._mandar(MANDA_GRUPO, ((ATRIBUTO_PID, _pids(pids)),))

    async def _sair(self) -> None:
        """A leader alone is a group of nobody, which is how this protocol dismantles one; a
        member walks out by writing the group back without itself.
        """
        meu = await self._pid_atual()
        grupo = await self._meu_grupo(meu)
        if grupo is None or grupo.lider == meu:
            await self._mandar(MANDA_GRUPO, ((ATRIBUTO_PID, _pids((meu,))),))
            return
        restantes = [grupo.lider, *(pid for pid in grupo.membros if pid not in (grupo.lider, meu))]
        await self._mandar(MANDA_GRUPO, ((ATRIBUTO_PID, _pids(restantes)),))

    async def _tirar(self, endereco: str) -> None:
        meu = await self._pid_atual()
        alvo = await self._pid_no(endereco)
        grupo = await self._meu_grupo(meu)
        if grupo is None or grupo.lider != meu:
            raise _Falha(NAO_SUPORTADO)
        restantes = [meu, *(pid for pid in grupo.membros if pid not in (meu, alvo))]
        await self._mandar(MANDA_GRUPO, ((ATRIBUTO_PID, _pids(restantes)),))

    async def _meu_grupo(self, meu: int) -> GrupoLido | None:
        grupos = await self._ler_grupos()
        for grupo in grupos:
            if meu == grupo.lider or meu in grupo.membros:
                return grupo
        return None

    async def _grupo_do_lider(self, lider: int) -> GrupoLido | None:
        for grupo in await self._ler_grupos():
            if grupo.lider == lider:
                return grupo
        return None

    async def _ler_grupos(self) -> tuple[GrupoLido, ...]:
        resposta = await self._falar(PEDE_GRUPOS)
        return _grupos_de(resposta.payload)

    async def _pid_no(self, endereco: str) -> int:
        return (await self._jogador_alvo(endereco)).pid

    async def _jogador_alvo(self, endereco: str) -> Jogador:
        """The line of the player list of the speaker at that address, refused when nothing
        there is one.

        A group only ever exists between speakers of the same kind, so an address that
        is not a speaker of this system is a value this driver refuses instead of a command
        it writes to nobody.
        """
        await self._pid_atual()
        jogador = _jogador_no(self._jogadores, endereco)
        if jogador is None:
            # A second read, because the list of a freshly woken interface can be incomplete.
            await self._resolver_pid()
            jogador = _jogador_no(self._jogadores, endereco)
        if jogador is None:
            raise _Falha(INVALID_VALUE)
        return jogador

    async def _pid_atual(self, prazo_final: float | None = None) -> int:
        if self._pid is not None:
            return self._pid
        await self._resolver_pid(prazo_final)
        if self._pid is None:
            raise _Falha(EQ_OFFLINE)
        return self._pid

    async def _resolver_pid(self, prazo_final: float | None = None) -> int:
        """The player id is not identity, so it is asked for again and never stored."""
        endereco = self._endereco()
        jogador = self._minha_linha(await self._jogadores_agora(prazo_final), endereco)
        if jogador is None:
            # The interface wakes up dormant and discovers the speakers of the system by
            # itself, so an empty or short list right after a connection is a re-read and not
            # a speaker that went away.
            jogador = self._minha_linha(await self._jogadores_agora(prazo_final), endereco)
        if jogador is None:
            raise _Falha(EQ_OFFLINE)
        self._pid = jogador.pid
        if jogador.serial:
            self._serial = jogador.serial
        self._volume_pela_rede = jogador.volume_pela_rede
        self._marcar_escravo(jogador.gid is not None and jogador.gid != jogador.pid)
        self._avisar_da_versao(jogador.versao)
        return jogador.pid

    async def _jogadores_agora(self, prazo_final: float | None = None) -> Resposta:
        return await self._falar(PEDE_JOGADORES, prazo_final=prazo_final)

    def _minha_linha(self, resposta: Resposta, endereco: str) -> Jogador | None:
        """The line of the player list that is this registration, matched by address and
        checked against the serial we learned from the speaker itself.

        The identity of a registration may be the uuid of the SSDP answer, which this
        interface never publishes, so the driver keeps its own record instead of keying by
        what the registration holds. A different serial at the same address is another box:
        commanding it would be turning up the volume of a neighbour under our own name.
        """
        self._jogadores = _jogadores_de(resposta.payload)
        jogador = _jogador_no(self._jogadores, endereco)
        if jogador is None:
            return _jogador_serial(self._jogadores, self._serial)
        if self._serial and jogador.serial and jogador.serial != self._serial:
            raise _Falha(EQ_OFFLINE)
        return jogador

    def _reproduzindo(self, transporte: Resposta) -> bool | None:
        """Whether the transport is playing, which is a different fact from the title."""
        estado = _texto(transporte.mensagem.get(ATRIBUTO_ESTADO)).lower()
        if estado:
            return estado == TOCAR
        # A driver that cannot tell leaves it None; the leader of the group
        # only ever stands in while this speaker is a member of one.
        return self._espelho_reproduzindo if self._escravo else None

    def _tocando(self, payload: object) -> str | None:
        """The title of what plays right now, and nothing else."""
        dados = payload if isinstance(payload, dict) else {}
        titulo = _texto(dados.get(CHAVE_TITULO)) or _texto(dados.get(CHAVE_ESTACAO))
        if titulo:
            return titulo
        # A member of a group plays the audio of its leader and names no title of its
        # own for it, and the name of the shortcut only stands in while the speaker names
        # nothing; a station that starts sending metadata takes the line on the next poll.
        if self._escravo and self._espelho:
            return self._espelho
        return self._pedido

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

    def _avisar_da_versao(self, versao: str) -> None:
        """A firmware older than the published protocol may not carry a command of this file."""
        if self._avisou_da_versao or not _antiga(versao):
            return
        self._avisou_da_versao = True
        log.warning(
            "%s: firmware %s is older than the published protocol, a command may not exist",
            self.cadastro.identidade,
            versao,
        )

    def _falhar(self, codigo: str) -> None:
        """One lost poll keeps the last state, two in a row is offline."""
        self._falhas += 1
        # A poll that failed is the first line of every diagnosis of a speaker that
        # "does nothing", and it is the one thing the diary must never be quiet about.
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        # A firmware update renumbers the player id, and a poll that failed is exactly
        # where that shows up, so the next one resolves it again from the player list.
        self._pid = None
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        self._defina(online=False, tocando=None, detalhe=codigo)

    async def _perguntar(self, comando: str, pid: int, prazo_final: float | None) -> Resposta:
        return await self._falar(comando, ((ATRIBUTO_PID, pid),), prazo_final=prazo_final)

    async def _mandar(
        self,
        comando: str,
        atributos: tuple[tuple[str, object], ...] = (),
        *,
        url: str = "",
    ) -> Resposta:
        """One command, sent again with the new number when the speaker says the old one died.

        A firmware update renumbers the players, and the refusal that says so is the one
        place the driver learns it between two polls. Answering the panel offline for a
        speaker that is alive and answering costs the integrator a second press, and costs a
        scene the step altogether, when the whole repair is one list away.
        """
        meu = self._pid
        try:
            return await self._falar(comando, atributos, url=url)
        except _PidMorto:
            novo = await self._renumerado(atributos, meu)
            if novo is None:
                raise
        return await self._falar(comando, _com_pid(atributos, novo), url=url)

    async def _renumerado(
        self, atributos: tuple[tuple[str, object], ...], meu: int | None
    ) -> int | None:
        """The new player id of THIS speaker, when the command that was refused carried the
        old one; None when there is nothing to send again.

        The command of a member of the group carries the player id of the member, and
        rewriting that one with ours would turn up the volume of the leader under the name of
        the member. Only the number this driver believed was its own is ever replaced.
        """
        velho = _pid_de(atributos)
        if velho is None or velho != meu:
            return None
        novo = await self._pid_atual()
        return novo if novo != velho else None

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self.cadastro.identidade or self.cadastro.ip)
            self._fio = transcricao
        return transcricao

    async def _falar(
        self,
        comando: str,
        atributos: tuple[tuple[str, object], ...] = (),
        *,
        url: str = "",
        prazo_final: float | None = None,
    ) -> Resposta:
        """One exchange, alone on the connection, answered by the command that comes back."""
        # The bytes are built and judged before a socket is touched, so a value this
        # file would refuse never costs the connection of a healthy speaker.
        bruto = _linha(comando, atributos, url=url)
        limite = _limite(prazo_final)
        # A spent budget drops the questions the poll has left, before a socket is used.
        if limite <= 0:
            raise _Falha(EQ_OFFLINE)
        rotina = comando in PERGUNTAS
        transcricao = self._transcricao()
        transcricao.enviado(bruto, rotina=rotina)
        async with self._trava:
            try:
                async with asyncio.timeout(limite):
                    leitor, escritor = await self._conexao()
                    escritor.write(bruto)
                    await escritor.drain()
                    resposta = await _ouvir(leitor, comando, _pid_de(atributos))
            except asyncio.CancelledError:
                # The deadline that kills a slow exchange is the one of the gestor around
                # the whole poll, and it arrives here as a cancellation and not as a timeout.
                # Leaving the socket behind would leave the answer of the dead exchange inside
                # it, and the next poll would open no connection and read that one as its own.
                self._soltar()
                raise
            except (
                TimeoutError,
                OSError,
                asyncio.IncompleteReadError,
                asyncio.LimitOverrunError,
            ) as erro:
                transcricao.falhou(bruto, erro)
                await self._desligar()
                raise _Falha(EQ_OFFLINE) from erro
            except _Falha as falha:
                transcricao.falhou(bruto, falha.codigo)
                await self._desligar()
                raise
        transcricao.recebido(resposta.mensagem, rotina=rotina)
        if not resposta.ok:
            codigo = _codigo_do_erro(resposta.mensagem)
            log.warning("the speaker refused %s with %s", comando, resposta.mensagem)
            if codigo == EQ_OFFLINE:
                # The player id this command carried is not valid any more, so the next
                # exchange resolves it again instead of writing to a number that died.
                self._pid = None
                raise _PidMorto
            raise _Falha(codigo)
        return resposta

    async def _conexao(self) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        """The connection this driver keeps open, opened and woken up when there is none.

        The interface goes back to a dormant mode on an idle socket and it discovers the
        speakers of the system when a controller connects, so the first thing a connection
        writes is the request for events to stay off, which is the wake up the protocol asks
        for; the events themselves are not consumed here and a speaker that sent them would
        be interleaving lines into every answer.
        """
        leitor, escritor = self._leitor, self._escritor
        if leitor is not None and escritor is not None and not escritor.is_closing():
            return leitor, escritor
        # A socket that is on its way out is still a socket, and leaving it behind is a
        # descriptor of this daemon held by a speaker that already hung up.
        await self._desligar()
        leitor, escritor = await asyncio.open_connection(
            self._endereco(), PORTA_CLI, limit=LINHA_MAXIMA
        )
        # A new connection is a new session of the interface, so nothing learned about
        # the numbering of the old one carries over.
        self._leitor, self._escritor = leitor, escritor
        await _escrever(escritor, REGISTRA_EVENTOS, ((ATRIBUTO_EVENTOS, DESLIGADO),))
        await _ouvir(leitor, REGISTRA_EVENTOS)
        return leitor, escritor

    async def _desligar(self) -> None:
        escritor = self._soltar()
        if escritor is None:
            return
        await _fechar(escritor)

    def _soltar(self) -> asyncio.StreamWriter | None:
        """The connection taken off this driver and closed, with nobody waited for.

        A cancellation has no time left to spend waiting for a peer to acknowledge the
        close, and the descriptor is what must not be left behind; the polite wait is only
        for the paths that still own their deadline.
        """
        escritor, self._escritor = self._escritor, None
        self._leitor = None
        self._pid = None
        if escritor is not None:
            with suppress(OSError):
                escritor.close()
        return escritor

    def _endereco(self) -> str:
        """Only an IP literal reaches a speaker, so the hub is never a resolver."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco


async def _escrever(
    escritor: asyncio.StreamWriter,
    comando: str,
    atributos: tuple[tuple[str, object], ...] = (),
    *,
    url: str = "",
) -> None:
    escritor.write(_linha(comando, atributos, url=url))
    await escritor.drain()


def _linha(comando: str, atributos: tuple[tuple[str, object], ...] = (), *, url: str = "") -> bytes:
    """One command of the protocol as the bytes the speaker reads.

    The address of a stream is the only attribute that travels unescaped and it has to
    be the last one, which the published protocol states and this file obeys in one place.
    """
    pares = [f"{nome}={_codificado(str(valor))}" for nome, valor in atributos]
    if url:
        pares.append(f"{ATRIBUTO_URL}={url}")
    consulta = f"?{'&'.join(pares)}" if pares else ""
    texto = f"{BASE}{comando}{consulta}"
    # Everything that reaches the socket passes here, so a value that carried the
    # terminator of the protocol would write a second command, and it never gets that far.
    if not _NO_FIO.fullmatch(texto):
        raise _Falha(INVALID_VALUE)
    return f"{texto}{TERMINADOR_TEXTO}".encode("ascii")


def _codificado(valor: str) -> str:
    """The escaping of this protocol, which is three bytes and not the one of a URL."""
    for bruto, trocado in TROCAS:
        valor = valor.replace(bruto, trocado)
    return valor


async def _ouvir(leitor: asyncio.StreamReader, comando: str, pid: int | None = None) -> Resposta:
    """The answer of one command, with what is not it dropped on the way.

    There is no request identifier on this wire, so the answer is the line whose command
    is ours. An event was never asked for, an answer carrying "command under process" is not
    the final one, and a late answer of another command belongs to nobody here: reading any
    of the three as ours is how a poll starts reading the answer of the poll before it.

    The player id is the second half of that correlation and it costs nothing: every answer
    of a command aimed at one speaker carries the id it was aimed at, the id is resolved again
    at every poll, and a firmware update renumbers it. Without this check the late answer of
    the same question asked about ANOTHER number reads as ours, and the panel publishes the
    volume of a speaker in another room.
    """
    aceitos = _aceitos(comando)
    for _ in range(LINHAS_ATE_DESISTIR):
        bruto = await leitor.readuntil(TERMINADOR)
        resposta = _resposta_de(bruto[: -len(TERMINADOR)])
        if resposta is None:
            continue
        if resposta.comando.startswith(PREFIXO_EVENTO) or SOB_PROCESSO in resposta.mensagem:
            continue
        if resposta.comando in aceitos and _do_pid(resposta, pid):
            return resposta
        log.debug("the speaker answered %s while %s was asked", resposta.comando, comando)
    raise _Falha(ERRO_APARELHO)


def _do_pid(resposta: Resposta, pid: int | None) -> bool:
    """Whether this line answers about the speaker the command was aimed at.

    A firmware that omits the id says nothing about whose answer this is, and dropping
    the line then would be this driver refusing to read a healthy speaker; only an id that
    is there and is somebody else's is the answer of another exchange.
    """
    if pid is None:
        return True
    respondido = _inteiro(resposta.mensagem.get(CHAVE_PID))
    return respondido is None or respondido == pid


def _aceitos(comando: str) -> frozenset[str]:
    """The command names that answer this command.

    The published protocol prints the answers of the group commands under the player
    group and the speaker writes them under the group one, so a driver that matched only what
    it sent would wait for a line that never comes on one of the two.
    """
    grupo, _separador, resto = comando.partition("/")
    if grupo == GRUPO_DE_GRUPO:
        return frozenset({comando, f"{GRUPO_DE_JOGADOR}/{resto}"})
    return frozenset({comando})


def _resposta_de(bruto: bytes) -> Resposta | None:
    """One line read as the envelope of the protocol, or None when it is not one."""
    try:
        documento = json.loads(bruto.decode("utf-8", errors="replace"))
    except (ValueError, RecursionError):
        return None
    if not isinstance(documento, dict):
        return None
    cabecalho = documento.get(CHAVE_HEOS)
    if not isinstance(cabecalho, dict):
        return None
    # A line with no result at all is a success in this protocol, and reading the absence
    # as a refusal would turn every answer of a firmware that omits it into an error.
    resultado = _texto(cabecalho.get(CHAVE_RESULTADO)) or SUCESSO
    return Resposta(
        comando=_texto(cabecalho.get(CHAVE_COMANDO)),
        ok=resultado == SUCESSO,
        mensagem=_mensagem(cabecalho.get(CHAVE_MENSAGEM)),
        payload=documento.get(CHAVE_PAYLOAD),
    )


def _mensagem(bruto: object) -> dict[str, str]:
    """The message of an answer, which is a query string with keys that carry no value."""
    if not isinstance(bruto, str):
        return {}
    return dict(parse_qsl(bruto[:MENSAGEM_MAXIMA], keep_blank_values=True))


def _codigo_do_erro(mensagem: dict[str, str]) -> str:
    """The refusal of the speaker as one of the five stable codes."""
    erro = _inteiro(mensagem.get(CHAVE_ERRO))
    if erro == ERRO_INTERNO and _inteiro(mensagem.get(CHAVE_ERRO_DE_SISTEMA)) == ERRO_SEM_CONTA:
        return AUTH_PENDENTE
    return CODIGO_POR_ERRO.get(erro, ERRO_APARELHO)


def _jogadores_de(payload: object) -> tuple[Jogador, ...]:
    """The speakers of the system as the player list describes them, capped."""
    if not isinstance(payload, list):
        return ()
    jogadores = []
    for item in payload[:JOGADORES_MAXIMOS]:
        if not isinstance(item, dict):
            continue
        pid = _inteiro(item.get(CHAVE_PID))
        if pid is None:
            continue
        endereco = ip_literal(item.get(CHAVE_IP))
        jogadores.append(
            Jogador(
                pid=pid,
                ip="" if endereco is None else endereco,
                serial=_texto(item.get(CHAVE_SERIAL)),
                nome=_texto(item.get(CHAVE_NOME)),
                # The group field is only there while the speaker is in a group, so its
                # absence is a solo speaker and never a line that failed to be read.
                gid=_inteiro(item.get(CHAVE_GID)),
                versao=_texto(item.get(CHAVE_VERSAO)),
                volume_pela_rede=_volume_pela_rede(item),
            )
        )
    return tuple(jogadores)


def _volume_pela_rede(item: dict) -> bool:
    """Whether this speaker changes volume over the network at all."""
    saida = _inteiro(item.get(CHAVE_SAIDA))
    # The control field only means anything on a fixed line out, and a variable one is
    # always driven over the network.
    if saida != SAIDA_FIXA:
        return True
    controle = _inteiro(item.get(CHAVE_CONTROLE))
    # A speaker that does not publish the field said nothing about what drives its
    # output, and reading silence as a refusal takes the volume away from a healthy speaker
    # for good, with the profile still publishing the level and nothing anywhere
    # saying why the bar refuses. The refusal is for a speaker that names another controller.
    return controle is None or controle == CONTROLE_DE_REDE


def _jogador_no(jogadores: tuple[Jogador, ...], endereco: str) -> Jogador | None:
    return next((jogador for jogador in jogadores if jogador.ip == endereco), None)


def _jogador_com(jogadores: tuple[Jogador, ...], pid: int) -> Jogador | None:
    return next((jogador for jogador in jogadores if jogador.pid == pid), None)


def _jogador_serial(jogadores: tuple[Jogador, ...], serial: str) -> Jogador | None:
    if not serial:
        return None
    return next((jogador for jogador in jogadores if jogador.serial == serial), None)


def _grupos_de(payload: object) -> tuple[GrupoLido, ...]:
    """The groups of the system, without the one nobody leads.

    A group in the middle of being formed can arrive with no leader in it, and a driver
    that took that for granted would drop its poll on the answer of a healthy system.
    """
    if not isinstance(payload, list):
        return ()
    grupos = []
    for item in payload[:JOGADORES_MAXIMOS]:
        if not isinstance(item, dict):
            continue
        lido = _grupo_lido_de(item)
        if lido is not None:
            grupos.append(lido)
    return tuple(grupos)


def _grupo_lido_de(item: dict) -> GrupoLido | None:
    jogadores = item.get(CHAVE_JOGADORES)
    if not isinstance(jogadores, list):
        return None
    lider: int | None = None
    membros: list[int] = []
    for membro in jogadores[:JOGADORES_MAXIMOS]:
        if not isinstance(membro, dict):
            continue
        pid = _inteiro(membro.get(CHAVE_PID))
        if pid is None:
            continue
        if _texto(membro.get(CHAVE_PAPEL)).lower() == PAPEL_LIDER:
            lider = pid
        else:
            membros.append(pid)
    if lider is None:
        return None
    return GrupoLido(gid=_inteiro(item.get(CHAVE_GID)), lider=lider, membros=tuple(membros))


def _entrada_ativa(payload: object) -> str | None:
    """The input that plays right now, which this protocol only names inside its own source.

    There is no input field in what plays; the auxiliary source is one source id and the
    media id under it is literally the name of the input, which is the value the list of the
    registration carries. Anything else playing is not an input, and saying so is honest.
    """
    if not isinstance(payload, dict):
        return None
    if _inteiro(payload.get(CHAVE_FONTE)) != FONTE_DE_ENTRADA:
        return None
    midia = _texto(payload.get(CHAVE_MIDIA))
    return midia if _ENTRADA.fullmatch(midia) else None


def _limite(prazo_final: float | None) -> float:
    if prazo_final is None:
        return TEMPO_LIMITE_S
    return min(TEMPO_LIMITE_S, prazo_final - asyncio.get_running_loop().time())


def _pid_de(atributos: tuple[tuple[str, object], ...]) -> int | None:
    """The player id this command was aimed at, when it was aimed at exactly one.

    The group commands of this protocol carry the WHOLE list of a group under the same
    attribute name, and a list is not an id to correlate an answer by nor a number to write
    again; only a plain one is either of those.
    """
    return next(
        (valor for nome, valor in atributos if nome == ATRIBUTO_PID and type(valor) is int), None
    )


def _com_pid(atributos: tuple[tuple[str, object], ...], pid: int) -> tuple[tuple[str, object], ...]:
    return tuple((nome, pid if nome == ATRIBUTO_PID else valor) for nome, valor in atributos)


def _pids(valores) -> str:
    """The list of a group as this protocol writes it: the leader first, by player id."""
    return ",".join(str(int(valor)) for valor in list(valores)[:JOGADORES_MAXIMOS])


def _volume_valido(valor: object) -> bool:
    # True is an int in Python, and a mute arriving where a volume belongs would be
    # written as the volume 1, which is a speaker that went silent for no reason.
    return type(valor) is int and VOLUME_MINIMO <= valor <= VOLUME_MAXIMO


def _para_o_aparelho(valor: int) -> int:
    """The 0 to 100 in the range the speaker speaks."""
    largura = VOLUME_MAXIMO_DO_APARELHO - VOLUME_MINIMO_DO_APARELHO
    return VOLUME_MINIMO_DO_APARELHO + round(valor * largura / VOLUME_MAXIMO)


def _do_aparelho(bruto: object) -> int | None:
    """What the speaker answered in the 0 to 100, or None when it is not one.

    This level comes back with a decimal point, so it is read as a number and then
    rounded; reading it as an integer raises on the answer of a healthy speaker.
    """
    numero = _numero(bruto)
    if numero is None:
        # A speaker that answers a word where a number belongs is not a volume of zero,
        # and writing zero would tell the panel a speaker is silent while it plays.
        return None
    largura = VOLUME_MAXIMO_DO_APARELHO - VOLUME_MINIMO_DO_APARELHO
    preso = max(VOLUME_MINIMO_DO_APARELHO, min(VOLUME_MAXIMO_DO_APARELHO, numero))
    convertido = (preso - VOLUME_MINIMO_DO_APARELHO) * VOLUME_MAXIMO / largura
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, round(convertido)))


def _favorito_de(valor: object) -> int | None:
    """The position of a favourite of the account, which is a number from one."""
    if not isinstance(valor, str) or not _NUMERO.fullmatch(valor.strip()):
        return None
    escolhido = int(valor.strip())
    return escolhido if FAVORITO_MINIMO <= escolhido <= FAVORITO_MAXIMO else None


def _url_valida(valor: object) -> bool:
    """The address of a stream the speaker fetches by itself, and never anything else."""
    return isinstance(valor, str) and len(valor) <= URL_MAXIMA and bool(_URL.fullmatch(valor))


def _verdade(bruto: object) -> bool | None:
    lido = _texto(bruto).lower()
    if lido == LIGADO:
        return True
    if lido == DESLIGADO:
        return False
    return None


def _antiga(versao: str) -> bool:
    numeros = []
    for parte in versao.split(".")[: len(VERSAO_ALVO)]:
        numero = _inteiro(parte)
        if numero is None:
            return False
        numeros.append(numero)
    return bool(numeros) and tuple(numeros) < VERSAO_ALVO[: len(numeros)]


def _texto(bruto: object) -> str:
    """What the speaker wrote, cleaned and capped, exactly as it wrote it."""
    if isinstance(bruto, bool) or bruto is None:
        return ""
    return _CONTROLE.sub("", str(bruto).strip())[:TEXTO_MAXIMO]


def _inteiro(bruto: object) -> int | None:
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, int):
        return bruto
    if not isinstance(bruto, str):
        return None
    try:
        return int(bruto.strip())
    except ValueError:
        return None


def _numero(bruto: object) -> int | None:
    """A number the speaker wrote, which it writes with a decimal point where it likes."""
    if isinstance(bruto, bool) or bruto is None:
        return None
    try:
        return round(float(bruto))
    except (TypeError, ValueError):
        return None


async def _fechar(escritor: asyncio.StreamWriter) -> None:
    escritor.close()
    # A cancellation of the poll must reach the caller, so only the noise of a peer that
    # already went away is swallowed here, and the wait has a deadline of its own because it
    # runs after the deadline of the exchange has already expired.
    with suppress(OSError, TimeoutError):
        async with asyncio.timeout(PRAZO_DE_FECHAMENTO_S):
            await escritor.wait_closed()

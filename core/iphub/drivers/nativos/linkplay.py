# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""LinkPlay multiroom speaker (AudioCast, iEAST), the multiroom driver of sections 6 and 14.

One transport, the HTTP API of the module: status, volume, mute, input, transport, the
hardware presets, a stream URL and the group. It is native and not declarative because the
state is read from two questions, the metadata is hexadecimal and the group logic needs the
moves below. The iEAST control port on TCP 8899 was retired on 5/set/2026: the office
speakers are standard LinkPlay modules (A28, A31, project uyesee-i50) and the public HTTP API
carries everything the port carried, with no pace to keep between two commands.

What the module that owns the blocks has to know, because paid days for it:

- the identity is the uuid of getStatusEx and never the address, so identidade_do_aparelho
is what tells whether the box answering at this ip is still the registered one;
- a speaker is a slave when getStatusEx says group 1 (or names a master_uuid); the mode 99
of getPlayerStatus alone is not proof, because a box idle after leaving a group keeps
answering it (measured on 5/set/2026), and it only stands in when the group field is absent;
- a play on a slave dismantles the group, so the transport of a group goes to the master and
this driver refuses transport, volume and preset while it is a slave;
- a slave answers stop while it plays, so the transport of the master is pinned onto it with
espelhar and read back in Estado.tocando;
- a slave that leaves the multiroom mode for two polls in a row lost its group to a reboot
or to the application of the manufacturer, and saiu_do_grupo says so;
- the firmware does not clear the title of the previous source, so tocando is the title of a
network source that is playing right now, and nothing else;
- Estado.tocando carries the title while the transport plays and None while it does not, so
whoever writes the play DP reads it from there.

The group logic itself (who is the master, which speakers may share a group, what to mirror)
lives in the module that owns the blocks. This driver offers only the four moves a group is
made of: entrar_no_grupo, desfazer_grupo, volume_de_escravo and ler_grupo.
"""

import asyncio
import json
import logging
import re
from contextlib import suppress
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout
from yarl import URL

from iphub.config import ip_literal
from iphub.drivers import corpo, fio
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Descoberta, Manifesto, Sugestao

log = logging.getLogger("iphub.drivers.nativos.linkplay")

PORTA_HTTP = 80
TEMPO_LIMITE_S = 4.0

# One lost poll is not a speaker that went away; two in a row is.
FALHAS_ATE_OFFLINE = 2

# A slave out of the multiroom mode for two polls in a row means the
# physical group dissolved by itself, and the logical state has to be reconciled.
POLLS_ATE_RECONCILIAR = 2

# A speaker on the LAN must never be able to make the daemon buffer without bound.
CORPO_MAXIMO = 64 * 1024
TEXTO_MAXIMO = 120
ESCRAVOS_MAXIMO = 12
URL_MAXIMA = 200

CAMINHO = "/httpapi.asp?command="

PEDE_IDENTIDADE = "getStatusEx"
PEDE_ESTADO = "getPlayerStatus"
PEDE_ESCRAVOS = "multiroom:getSlaveList"

# The two questions of the poll, which answer the same thing every five seconds.
PERGUNTAS = (PEDE_IDENTIDADE, PEDE_ESTADO)
MANDA_VOLUME = "setPlayerCmd:vol:{valor}"
MANDA_TOCAR = "setPlayerCmd:play:{valor}"
MANDA_RETOMAR = "setPlayerCmd:resume"
MANDA_PAUSAR = "setPlayerCmd:pause"
MANDA_PARAR = "setPlayerCmd:stop"
MANDA_REDE = "setPlayerCmd:switchmode:wifi"
MANDA_ENTRADA = "setPlayerCmd:switchmode:{valor}"
MANDA_MUDO = "setPlayerCmd:mute:{valor}"
MANDA_PROXIMA = "setPlayerCmd:next"
MANDA_ANTERIOR = "setPlayerCmd:prev"
# A preset is one of the keys of the speaker, and the API presses it by number.
MANDA_PRESET = "MCUKeyShortClick:{valor}"
ENTRA_NO_GRUPO = "ConnectMasterAp:JoinGroupMaster:eth{ip}:wifi0.0.0.0"
DESFAZ_GRUPO = "multiroom:Ungroup"
# A group is a master and the members the customer chose, so taking one
# member out cannot mean taking the group down; this is the move that removes exactly one.
TIRA_DO_GRUPO = "multiroom:SlaveKickout:{ip}"
MANDA_VOLUME_DE_ESCRAVO = "multiroom:SlaveVolume:{ip}:{valor}"

RESPOSTA_OK = "ok"

CHAVE_UUID = "uuid"
CHAVE_ENTRADAS = "plm_support"
CHAVE_GRUPO = "group"
CHAVE_MESTRE = "master_uuid"
CHAVE_PRESETS = "preset_key"
CHAVE_MODO = "mode"
CHAVE_ESTADO = "status"
CHAVE_VOLUME = "vol"
CHAVE_MUDO = "mute"
CHAVE_TITULO = "Title"
CHAVE_ARTISTA = "Artist"
CHAVE_ESCRAVOS = "slave_list"
CHAVE_IP = "ip"
CHAVE_NOME = "name"

TOCANDO = "play"
LIGADO = "1"

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

# What a slave must never do on its own, because on the bench each one of these took the
# group down or desynchronized it; the module that owns the group sends them to the master.
ACOES_DO_MESTRE = (
    ACAO_VOLUME,
    ACAO_TOCAR,
    ACAO_PAUSAR,
    ACAO_PARAR,
    ACAO_PROXIMA,
    ACAO_ANTERIOR,
    ACAO_ATALHO,
)

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

# A code of its own would be a phrase the panel cannot translate, and of
# the five stable codes this is the one that says the speaker cannot do it as it stands: in
# a group its input is the group, and its transport belongs to the master.
RECUSA_DE_GRUPO = "nao_suportado"

MODO_ESCRAVO = 99
ENTRADA_DE_REDE = "wifi"

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100
# The speaker speaks the same 0 to 100 the contract fixes, and saying it
# with a constant is what keeps a firmware with another range one line away.
VOLUME_MINIMO_DO_APARELHO = 0
VOLUME_MAXIMO_DO_APARELHO = 100

# The speaker says how many preset keys it has (preset_key of getStatusEx, 6 and 9 on the
# office boxes); the ceiling below holds for a box that did not say.
PRESET_MINIMO = 1
PRESET_MAXIMO = 12
PREFIXO_PRESET = "preset:"

# The value of a command lands inside the query string of the speaker, so anything that
# is not one of these bytes could close the command and write a second parameter of its own.
_NO_FIO = re.compile(r"[A-Za-z0-9:%._~/\-\[\]]+")
_URL = re.compile(r"https?://[A-Za-z0-9:%._~/\-\[\]]+")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2})+")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
_NUMERO = re.compile(r"-?[0-9]{1,10}")

# A title the firmware writes when it has none of its own.
SEM_TITULO = frozenset({"unknown", "un-known", "none", "null"})


@dataclass(frozen=True)
class Entrada:
    """One physical input: the bit the hardware declares, the mode it answers and the name
    the switchmode command takes.
    """

    nome: str
    bit: int
    modo: int
    codigo: str


# Plm_support is the bitmask saying which inputs the hardware really has, so offering
# one outside it puts a button on the panel that only ever fails. The network input is not
# in the mask because every speaker has it, and it is the one that comes back over HTTP.
ENTRADAS = (
    Entrada("line-in", bit=1, modo=40, codigo="line-in"),
    Entrada("bluetooth", bit=2, modo=41, codigo="bluetooth"),
    Entrada("usb", bit=3, modo=51, codigo="udisk"),
    Entrada("optical", bit=4, modo=43, codigo="optical"),
)

# A speaker that was just added carries an empty list of shortcuts, and the value of one
# is a URL the speaker fetches by itself, which nobody guesses. These three are public radios
# that answer plain HTTP with no redirect and no query string, which is what the speaker and
# the guard of the address both need, plus the preset shape written once so it can be copied.
# Only the shortcuts are suggested: the inputs of a box are the ones its plm_support declares,
# read at each poll, and a suggested list would replace that true list with a guess.
SUGESTOES = (
    Sugestao("atalhos", "Groove Salad", "http://ice1.somafm.com/groovesalad-128-mp3"),
    Sugestao("atalhos", "Secret Agent", "http://ice1.somafm.com/secretagent-128-mp3"),
    Sugestao("atalhos", "Radio Paradise", "http://stream.radioparadise.com/mp3-192"),
    Sugestao("atalhos", "Tecla 1 da caixa", "preset:1"),
    Sugestao("atalhos", "Tecla 2 da caixa", "preset:2"),
    Sugestao("atalhos", "Tecla 3 da caixa", "preset:3"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "LinkPlay multiroom speaker (AudioCast, iEAST). Always on, so it declares no "
            "power: volume, mute, input, play, pause, stop, track, radios and presets, and "
            "native grouping."
        ),
        "preparo": (
            "Put the speaker on the Wi-Fi with the app of the maker first, and reserve its address "
            "on the router. The hub talks to it on port 80, which is on by default; nothing to "
            "switch on in the speaker."
        ),
        "cap_fonte": (
            "Only the inputs the speaker declares are offered, and the input is refused "
            "while the speaker is in a group, because changing it breaks the group."
        ),
        "cap_tocar": (
            "Play takes the address of an audio stream, and with no value it resumes what "
            "was paused. In a group it belongs to the master."
        ),
        "cap_agrupar": (
            "Grouping takes the address of the master to join, and with no value it "
            "dismantles the group this speaker leads. Only speakers of the same kind, one "
            "master and up to seven members, chosen one by one."
        ),
        "cap_atalho": (
            "A shortcut is a radio, written as the address of its stream "
            "(http://ice1.somafm.com/groovesalad-128-mp3), or a preset key of the speaker "
            "itself, written as preset:1 up to the number of keys it has. In a group it "
            "belongs to the master."
        ),
        "lista_entradas": "wifi is the network; the physical ones are the ones below.",
        "lista_atalhos": (
            "The address of a stream (plain http, no '?'), or preset:1 for a key of the "
            "speaker. The page of a station is not its stream."
        ),
    },
    "pt": {
        "descricao": (
            "Caixa multiroom LinkPlay (AudioCast, iEAST). Sempre ligada, então não declara "
            "energia: volume, mudo, entrada, tocar, pausar, parar, faixa, rádios e presets, e "
            "agrupamento nativo."
        ),
        "preparo": (
            "Ponha a caixa no Wi-Fi com o aplicativo do fabricante antes, e reserve o endereço "
            "dela no roteador. O hub fala com ela na porta 80, que vem ligada; nada a ligar na "
            "caixa."
        ),
        "cap_fonte": (
            "Só as entradas que a caixa declara são oferecidas, e a entrada é recusada "
            "enquanto a caixa está num grupo, porque trocá-la quebra o grupo."
        ),
        "cap_tocar": (
            "Tocar recebe o endereço de um fluxo de áudio, e sem valor retoma o que estava "
            "pausado. Num grupo ele é do mestre."
        ),
        "cap_agrupar": (
            "Agrupar recebe o endereço do mestre em que entrar, e sem valor desfaz o grupo "
            "que esta caixa lidera. Só caixas do mesmo tipo, um mestre e até sete membros, "
            "escolhidos um a um."
        ),
        "cap_atalho": (
            "Um atalho é uma rádio, escrita como o endereço do fluxo dela "
            "(http://ice1.somafm.com/groovesalad-128-mp3), ou uma tecla de preset da própria "
            "caixa, escrita como preset:1 até o número de teclas que ela tem. Num grupo ele é "
            "do mestre."
        ),
        "lista_entradas": "wifi é a rede; as físicas são as de baixo.",
        "lista_atalhos": (
            "O endereço de um fluxo (http simples, sem '?'), ou preset:1 para uma tecla da "
            "caixa. A página de uma estação não é o fluxo dela."
        ),
    },
}


@dataclass(frozen=True)
class Escravo:
    """One member of a group as the master lists it: the uuid is the key, the ip is today's."""

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


class LinkPlay(Driver):
    """Volume, mute, input, transport, radios and presets and native grouping of a LinkPlay
    speaker.
    """

    # The speaker is always on, and says omitting the capability is right,
    # never implementing one to refuse it. The port is not a config field either: this
    # protocol fixes both of them, and a field nobody needs is a field somebody breaks.
    MANIFESTO = Manifesto(
        tipo="multiroom_linkplay",
        rotulo={"pt": "Multiroom LinkPlay", "en": "LinkPlay multiroom"},
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
        descoberta=Descoberta(mdns_servicos=("_linkplay._tcp",)),
        textos=TEXTOS,
        marca="LinkPlay",
        sugestoes=SUGESTOES,
    )

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._porta_http = PORTA_HTTP
        self._sessao: ClientSession | None = None
        # The poll and a command of the integrator land together, and two exchanges at
        # once on the same speaker read each other's answers.
        self._trava_http = asyncio.Lock()
        self._identidade: str | None = None
        self._entradas: tuple[str, ...] = ()
        self._grupo_fisico: bool | None = None
        self._presets: int | None = None
        self._falhas = 0
        self._escravo = False
        self._polls_fora = 0
        self._saiu_do_grupo = False
        self._no_grupo = False
        self._espelho: str | None = None
        self._espelho_reproduzindo: bool | None = None
        # A radio is a raw stream, and this firmware answers an empty Title for one until
        # the station sends metadata, which many never do. The hub asked for this stream by a
        # shortcut the integrator named, so it publishes that name meanwhile: an empty "now
        # playing" over a speaker that is audibly playing is the panel calling itself broken.
        self._pedido: str | None = None

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """The uuid the speaker at that address answers, for a finding of the sweep."""
        endereco = ip_literal(ip)
        if endereco is None:
            return None
        url = f"http://{_hospedeiro(endereco)}:{PORTA_HTTP}{CAMINHO}{PEDE_IDENTIDADE}"
        try:
            async with ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S)) as sessao:
                async with sessao.get(url, allow_redirects=False) as resposta:
                    if resposta.status >= 400:
                        return None
                    bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
        except (TimeoutError, ClientError, OSError, ValueError):
            return None
        try:
            dados = json.loads(bruto.decode("utf-8", errors="replace"))
        except (ValueError, RecursionError):
            return None
        if not isinstance(dados, dict):
            return None
        return _texto(dados.get(CHAVE_UUID)) or None

    async def iniciar(self) -> None:
        await self._abrir()

    async def parar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None:
            await sessao.close()

    async def atualizar(self) -> None:
        # The identity is the uuid and the address is only where it answered
        # today. Asking once and never again means a lease that moved to another box leaves the
        # hub commanding whatever now holds the address, under the name of this block, for as
        # long as the daemon runs. The question is one small GET on the LAN, which is a cheap
        # price for never commanding the wrong speaker.
        try:
            self._ler_identidade(await self._perguntar(PEDE_IDENTIDADE))
            self._aplicar(await self._perguntar(PEDE_ESTADO))
        except _Falha as falha:
            self._falhar(falha.codigo)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        try:
            return await self._agir(acao, valor)
        except _Falha as falha:
            return falha.codigo

    def identidade_do_aparelho(self) -> str | None:
        """The uuid the speaker answered, which is what says the box here is still ours."""
        return self._identidade

    def e_escravo(self) -> bool:
        """The last poll saw the speaker in the multiroom slave mode."""
        return self._escravo

    def saiu_do_grupo(self) -> bool:
        """It was a slave and left the multiroom mode for two polls: the group dissolved."""
        return self._saiu_do_grupo

    def no_grupo(self) -> bool:
        return self._escravo or self._no_grupo

    def marcar_grupo(self, dentro: bool) -> None:
        """Where the owner of the group logic says this speaker stands right now."""
        self._no_grupo = dentro
        # Whoever owns the group logic has just declared where this speaker stands, and
        # that settles it in BOTH directions. A verdict left over from an earlier group, that
        # the speaker had left the multiroom mode, would otherwise make the reconcile tear
        # down the group the owner formed one moment ago.
        self._saiu_do_grupo = False
        if not dentro:
            self._espelho = None
            self._espelho_reproduzindo = None

    def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
        """A slave answers stop even while it plays, so the transport of the
        master is pinned here by whoever owns the group.
        """
        self._espelho = None if tocando is None else _texto(tocando)
        self._espelho_reproduzindo = reproduzindo

    async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
        """Run on the SLAVE: it joins the master at that address."""
        endereco = ip_literal(ip_do_mestre)
        if endereco is None:
            return INVALID_VALUE
        try:
            await self._mandar(ENTRA_NO_GRUPO.format(ip=endereco))
        except _Falha as falha:
            return falha.codigo
        return None

    async def desfazer_grupo(self) -> str | None:
        """Run on the MASTER: it dismantles the group it leads."""
        try:
            await self._mandar(DESFAZ_GRUPO)
        except _Falha as falha:
            return falha.codigo
        return None

    async def tirar_do_grupo(self, ip_do_escravo: object) -> str | None:
        """Run on the MASTER: it removes one member and keeps the rest of the group playing."""
        endereco = ip_literal(ip_do_escravo)
        if endereco is None:
            return INVALID_VALUE
        try:
            await self._mandar(TIRA_DO_GRUPO.format(ip=endereco))
        except _Falha as falha:
            return falha.codigo
        return None

    async def volume_de_escravo(self, ip_do_escravo: object, valor: object) -> str | None:
        """Run on the MASTER: the volume of a slave goes through the master."""
        endereco = ip_literal(ip_do_escravo)
        if endereco is None or not _volume_valido(valor):
            return INVALID_VALUE
        bruto = _para_o_aparelho(int(valor))
        try:
            await self._mandar(MANDA_VOLUME_DE_ESCRAVO.format(ip=endereco, valor=bruto))
        except _Falha as falha:
            return falha.codigo
        return None

    async def ler_grupo(self) -> Grupo | None:
        """Run on the MASTER: the group the speaker itself lists, or None when it did not
        answer one. the key of a member is its uuid, never its address.
        """
        try:
            return _grupo_de(await self._perguntar(PEDE_ESCRAVOS))
        except _Falha as falha:
            log.warning("speaker %s did not list its group: %s", self.cadastro.identidade, falha)
            return None

    async def _agir(self, acao: str, valor: object) -> str | None:
        # Each one of these on a slave takes the group down or leaves the
        # panel showing a volume the speaker never heard about.
        if acao in ACOES_DO_MESTRE and self._escravo:
            return RECUSA_DE_GRUPO
        if acao == ACAO_VOLUME:
            return await self._trocar_volume(valor)
        if acao == ACAO_MUDO:
            return await self._trocar_mudo(valor)
        if acao == ACAO_FONTE:
            return await self._trocar_fonte(valor)
        if acao == ACAO_TOCAR:
            return await self._tocar(valor)
        if acao == ACAO_PAUSAR:
            await self._mandar(MANDA_PAUSAR)
            self._pedido = None
            self._defina(reproduzindo=False, tocando=None)
            return None
        if acao == ACAO_PARAR:
            # A pause on a stream keeps the speaker connected to the radio, and a station
            # that dropped the connection meanwhile never resumes; stop is what lets go of it.
            await self._mandar(MANDA_PARAR)
            self._pedido = None
            self._defina(reproduzindo=False, tocando=None)
            return None
        if acao == ACAO_PROXIMA:
            await self._mandar(MANDA_PROXIMA)
            return None
        if acao == ACAO_ANTERIOR:
            await self._mandar(MANDA_ANTERIOR)
            return None
        if acao == ACAO_AGRUPAR:
            return await self._agrupar(valor)
        if acao == ACAO_ATALHO:
            return await self._atalho(valor)
        return await super().executar(acao, valor)

    async def _trocar_volume(self, valor: object) -> str | None:
        if not _volume_valido(valor):
            return INVALID_VALUE
        pedido = int(valor)
        await self._mandar(MANDA_VOLUME.format(valor=_para_o_aparelho(pedido)))
        self._defina(volume=pedido)
        return None

    async def _trocar_mudo(self, valor: object) -> str | None:
        if not isinstance(valor, bool):
            return INVALID_VALUE
        await self._mandar(MANDA_MUDO.format(valor=int(valor)))
        self._defina(mudo=valor)
        return None

    async def _trocar_fonte(self, valor: object) -> str | None:
        # Changing the input of a speaker that is in a group breaks the
        # group, so it is refused here instead of being discovered on the bench.
        if self.no_grupo():
            return RECUSA_DE_GRUPO
        entradas = self._entradas or (ENTRADA_DE_REDE,)
        if not isinstance(valor, str) or valor not in entradas:
            return INVALID_VALUE
        if valor == ENTRADA_DE_REDE:
            await self._mandar(MANDA_REDE)
        else:
            await self._mandar(MANDA_ENTRADA.format(valor=_entrada_por_nome(valor).codigo))
        # The firmware keeps the title of the last network source, so the
        # cache would show the last track of the radio on a line input until the next poll.
        self._pedido = None
        self._defina(fonte=valor, tocando=None)
        return None

    def _rotulo_de(self, valor: object) -> str | None:
        """The label the integrator gave this value in the shortcuts of the registration."""
        for item in self.cadastro.listas.get("atalhos", ()):
            if item.valor == valor:
                return item.rotulo
        return None

    async def _tocar(self, valor: object) -> str | None:
        # Every other handler pins its own field in the cache, and the bus publishes from
        # that cache once a second, which lands BEFORE the reread at 1.5 s. A play
        # that pinned nothing let the tick republish the old transport, so reproduzindo fell back to
        # false a second after the command the speaker had accepted.
        if valor is None or valor == "":
            await self._mandar(MANDA_RETOMAR)
            self._defina(reproduzindo=True)
            return None
        if not _url_valida(valor):
            return INVALID_VALUE
        await self._mandar(MANDA_TOCAR.format(valor=valor))
        self._pedido = self._rotulo_de(valor)
        self._defina(fonte=ENTRADA_DE_REDE, reproduzindo=True, tocando=self._pedido)
        return None

    async def _agrupar(self, valor: object) -> str | None:
        if valor is None or valor == "":
            return await self.desfazer_grupo()
        return await self.entrar_no_grupo(valor)

    async def _atalho(self, valor: object) -> str | None:
        """A radio or a stream by its address, or a preset key of the speaker by its number."""
        if _url_valida(valor):
            return await self._tocar(valor)
        numero = _preset_de(valor, PRESET_MAXIMO if self._presets is None else self._presets)
        if numero is None:
            return INVALID_VALUE
        await self._mandar(MANDA_PRESET.format(valor=numero))
        self._pedido = self._rotulo_de(valor)
        self._defina(fonte=ENTRADA_DE_REDE, reproduzindo=True, tocando=self._pedido)
        return None

    def _ler_identidade(self, dados: dict) -> None:
        # The identity of a device is its uuid; the address is only where it
        # answered today, and a hub that keyed by it would lose the box on the next lease.
        identidade = _texto(dados.get(CHAVE_UUID))
        # Says the identity is the uuid and the address is only where it
        # answered today. Storing the uuid without ever comparing it means a lease that moved
        # to another box has the hub commanding whatever now holds the address: the volume of
        # a neighbour's speaker, under the name of this block.
        if identidade and self._identidade and identidade != self._identidade:
            raise _Falha(EQ_OFFLINE)
        if identidade:
            self._identidade = identidade
        self._entradas = _entradas_de(dados.get(CHAVE_ENTRADAS))
        self._grupo_fisico = _grupo_fisico_de(dados)
        self._presets = _inteiro(dados.get(CHAVE_PRESETS))

    def _aplicar(self, dados: dict) -> None:
        self._falhas = 0
        modo = _inteiro(dados.get(CHAVE_MODO))
        self._marcar_escravo(self._escravo_de(modo))
        fonte = _fonte_do_modo(modo)
        self._defina(
            online=True,
            volume=_do_aparelho(dados.get(CHAVE_VOLUME)),
            mudo=_verdade(dados.get(CHAVE_MUDO)),
            fonte=fonte if fonte is not None else self.estado().fonte,
            fontes=self._entradas,
            reproduzindo=self._reproduzindo(dados),
            tocando=self._tocando(dados, fonte),
            detalhe="",
        )

    def _escravo_de(self, modo: int | None) -> bool:
        """Whether the speaker follows a master right now.

        Measured on 5/set/2026, a speaker idle after leaving a group keeps answering
        mode 99 with group 0 and no master, and taking the mode for the fact refused its
        volume and transport on the panel as if it followed a master; getStatusEx says it.
        """
        if self._grupo_fisico is not None:
            return self._grupo_fisico
        return modo == MODO_ESCRAVO

    def _reproduzindo(self, dados: dict) -> bool | None:
        """Whether the transport is playing, on whatever input, the reproduzindo fact.

        The title is a different fact, and reading this one from it reported a speaker
        playing over bluetooth, over a line input, or a radio with no metadata, as paused.
        """
        # A slave answers stop even while the group plays.
        if self._escravo:
            return self._espelho_reproduzindo
        estado = _texto(dados.get(CHAVE_ESTADO)).lower()
        if not estado:
            return None
        return estado == TOCANDO

    def _tocando(self, dados: dict, fonte: str | None) -> str | None:
        """The title of a network source that is playing right now, and nothing else."""
        # A slave answers stop even while the group plays, so what the
        # master is playing wins over what the slave says about itself.
        if self._escravo:
            return self._espelho
        # The firmware does not clear Title and Artist when the source
        # changes, so a line-in that is playing would show the last track of the radio.
        if fonte != ENTRADA_DE_REDE or _texto(dados.get(CHAVE_ESTADO)).lower() != TOCANDO:
            return None
        titulo = _titulo(dados.get(CHAVE_TITULO))
        artista = _titulo(dados.get(CHAVE_ARTISTA))
        if titulo and artista:
            return f"{titulo} - {artista}"[:TEXTO_MAXIMO]
        # The name of the shortcut only stands in while the speaker names nothing itself,
        # and a station that starts sending metadata takes the line over on the next poll.
        return titulo or artista or self._pedido

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
        # A poll that failed is the first line of every diagnosis of a speaker that
        # "does nothing", and it is the one thing the diary must never be quiet about.
        log.warning("%s: poll %d failed with %s", self.cadastro.identidade, self._falhas, codigo)
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        # A speaker that went away comes back by its identity in about 50 s,
        # and in the meantime the lease may have handed its address to another box; asking
        # the identity again is what keeps the hub from commanding a stranger.
        self._identidade = None
        self._defina(online=False, tocando=None, detalhe=codigo)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    async def _perguntar(self, comando: str) -> dict:
        """A question of the protocol, answered as an object; anything else is a fault."""
        corpo = await self._pedir(comando)
        # A body nested deep enough makes the parser recurse to its limit, which is an
        # answer the speaker chose and not a fault of this daemon.
        try:
            documento = json.loads(corpo)
        except (ValueError, RecursionError) as erro:
            raise _Falha(ERRO_APARELHO) from erro
        if not isinstance(documento, dict):
            raise _Falha(ERRO_APARELHO)
        return documento

    async def _mandar(self, comando: str) -> None:
        """A command of the protocol; the speaker answers OK and nothing else means done."""
        corpo = await self._pedir(comando)
        # This firmware answers OK to any command, including one that does
        # not exist, so this check only catches a firmware that does report an error. What
        # really verifies a command on these speakers is the reread against the
        # state the device answers, never this line.
        if corpo.strip().lower() != RESPOSTA_OK:
            log.warning("speaker answered %r to %s", corpo[:TEXTO_MAXIMO], comando)
            raise _Falha(ERRO_APARELHO)

    def _transcricao(self) -> fio.Fio:
        transcricao = getattr(self, "_fio", None)
        if transcricao is None:
            transcricao = fio.Fio(log, self.cadastro.identidade or self.cadastro.ip)
            self._fio = transcricao
        return transcricao

    async def _pedir(self, comando: str) -> str:
        # The command lands in the query string of the speaker, so a value that carried
        # a separator would write a second parameter that nobody wrote in this file.
        if not _NO_FIO.fullmatch(comando):
            raise _Falha(INVALID_VALUE)
        url = f"http://{_hospedeiro(self._endereco())}:{self._porta_http}{CAMINHO}{comando}"
        rotina = comando in PERGUNTAS
        transcricao = self._transcricao()
        transcricao.enviado(f"GET {CAMINHO}{comando}", rotina=rotina)
        async with self._trava_http:
            sessao = await self._abrir()
            try:
                async with sessao.get(
                    # The bytes checked above are the bytes the speaker has to read; the
                    # client would rewrite the brackets of an IPv6 stream on its own.
                    URL(url, encoded=True),
                    # A speaker answering a redirect would send the hub to whatever host
                    # it names, which is the LAN proxy refuses.
                    allow_redirects=False,
                ) as resposta:
                    bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                    estado = resposta.status
            except (TimeoutError, ClientError, OSError, ValueError) as erro:
                transcricao.falhou(f"GET {CAMINHO}{comando}", erro)
                raise _Falha(EQ_OFFLINE) from erro
        # The two questions of the poll are written once per connection and every
        # command always, so the transcript shows what the speaker answers without a poll
        # every five seconds on twelve speakers evicting the commands from the thousand
        # lines of the panel.
        transcricao.recebido(f"{estado} {bruto.decode('utf-8', errors='replace')}", rotina=rotina)
        if estado >= 400:
            log.warning("the speaker answered HTTP %d to %s", estado, comando)
            raise _Falha(ERRO_APARELHO)
        return bruto.decode("utf-8", errors="replace")

    def _endereco(self) -> str:
        """Only an IP literal reaches a speaker, so the hub is never a resolver."""
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco


def _entradas_de(bruto: object) -> tuple[str, ...]:
    """The inputs the hardware declares in its mask, with the network one it always has."""
    mascara = _mascara(bruto)
    if mascara is None or mascara < 0:
        return (ENTRADA_DE_REDE,)
    return (ENTRADA_DE_REDE, *(e.nome for e in ENTRADAS if mascara & (1 << e.bit)))


def _mascara(bruto: object) -> int | None:
    """The mask of inputs, which the firmware writes in decimal or in hexadecimal."""
    if isinstance(bruto, str) and bruto.strip().lower().startswith("0x"):
        return _inteiro(bruto.strip()[2:], base=16)
    return _inteiro(bruto)


def _entrada_por_nome(nome: str) -> Entrada:
    return next(entrada for entrada in ENTRADAS if entrada.nome == nome)


def _fonte_do_modo(modo: int | None) -> str | None:
    """The input the speaker says it is on; the multiroom mode is a group and not an input."""
    if modo is None or modo == MODO_ESCRAVO:
        return None
    for entrada in ENTRADAS:
        if entrada.modo == modo:
            return entrada.nome
    # Everything the mask does not name is the network side of the speaker, which is
    # where airplay, the streaming services and a played URL all live.
    return ENTRADA_DE_REDE


def _grupo_de(dados: dict) -> Grupo:
    """The members the master listed, keyed by uuid, capped, and without a member whose
    address is a name instead of an address.
    """
    bruto = dados.get(CHAVE_ESCRAVOS)
    if not isinstance(bruto, list):
        return Grupo()
    membros: dict[str, Escravo] = {}
    for item in bruto:
        if len(membros) >= ESCRAVOS_MAXIMO:
            break
        if not isinstance(item, dict):
            continue
        identidade = _texto(item.get(CHAVE_UUID))
        endereco = ip_literal(item.get(CHAVE_IP))
        if not identidade or endereco is None:
            continue
        membros.setdefault(identidade, Escravo(identidade, endereco, _texto(item.get(CHAVE_NOME))))
    return Grupo(tuple(membros.values()))


def _volume_valido(valor: object) -> bool:
    # True is an int in Python, and a mute arriving where a volume belongs would be
    # written as the volume 1, which is a speaker that went silent for no reason.
    return type(valor) is int and VOLUME_MINIMO <= valor <= VOLUME_MAXIMO


def _para_o_aparelho(valor: int) -> int:
    """The 0 to 100 in the range the speaker speaks."""
    largura = VOLUME_MAXIMO_DO_APARELHO - VOLUME_MINIMO_DO_APARELHO
    return VOLUME_MINIMO_DO_APARELHO + round(valor * largura / VOLUME_MAXIMO)


def _do_aparelho(bruto: object) -> int | None:
    """What the speaker answered in the 0 to 100, or None when it is not one."""
    numero = _inteiro(bruto)
    if numero is None:
        # A speaker that answers a word where a number belongs is not a volume of zero,
        # and writing zero would tell the panel a speaker is silent while it plays.
        return None
    largura = VOLUME_MAXIMO_DO_APARELHO - VOLUME_MINIMO_DO_APARELHO
    preso = max(VOLUME_MINIMO_DO_APARELHO, min(VOLUME_MAXIMO_DO_APARELHO, numero))
    convertido = (preso - VOLUME_MINIMO_DO_APARELHO) * VOLUME_MAXIMO / largura
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, round(convertido)))


def _preset_de(valor: object, maximo: int) -> int | None:
    """The preset key of a shortcut, written as preset:1 up to the keys the speaker has."""
    if not isinstance(valor, str):
        return None
    cabeca, separador, numero = valor.strip().lower().partition(":")
    if not separador or f"{cabeca}:" != PREFIXO_PRESET or not _NUMERO.fullmatch(numero):
        return None
    escolhido = int(numero)
    if not PRESET_MINIMO <= escolhido <= min(maximo, PRESET_MAXIMO):
        return None
    return escolhido


def _grupo_fisico_de(dados: dict) -> bool | None:
    """What getStatusEx says about the group: a slave answers group 1 and names its master;
    None when the firmware does not carry the field at all.
    """
    if _texto(dados.get(CHAVE_MESTRE)):
        return True
    return _verdade(dados.get(CHAVE_GRUPO))


def _url_valida(valor: object) -> bool:
    """The address of a stream the speaker fetches by itself, and never anything else."""
    return isinstance(valor, str) and len(valor) <= URL_MAXIMA and bool(_URL.fullmatch(valor))


def _verdade(bruto: object) -> bool | None:
    lido = _texto(bruto)
    if not lido:
        return None
    return lido == LIGADO


def _titulo(bruto: object) -> str:
    lido = _metadado(bruto)
    return "" if lido.lower() in SEM_TITULO else lido


def _texto(bruto: object) -> str:
    """What the speaker wrote, cleaned and capped, exactly as it wrote it."""
    if isinstance(bruto, bool) or bruto is None:
        return ""
    return _CONTROLE.sub("", str(bruto).strip())[:TEXTO_MAXIMO]


def _metadado(bruto: object) -> str:
    """A title or an artist, which the firmware answers in hexadecimal.

    A title that is not hexadecimal, and one whose bytes are not text, are both read as
    they arrived, because a strict decode is the only thing that tells a hex title from a
    title that happens to be spelled with the letters of the hexadecimal alphabet.
    """
    texto = _texto(bruto)
    if _HEX.fullmatch(texto):
        with suppress(ValueError, UnicodeDecodeError):
            texto = _CONTROLE.sub("", bytes.fromhex(texto).decode("utf-8"))
    return texto[:TEXTO_MAXIMO]


def _inteiro(bruto: object, *, base: int = 10) -> int | None:
    """The number the speaker answered, which it writes as text and sometimes as hexadecimal."""
    if isinstance(bruto, bool):
        return None
    if isinstance(bruto, int):
        return bruto
    if not isinstance(bruto, str):
        return None
    try:
        return int(bruto.strip(), base)
    except ValueError:
        return None


def _hospedeiro(endereco: str) -> str:
    """The address as the HOST of a URL: an IPv6 lives in brackets there, or the colons of
    the address read as a port. The address inside a command is a value and not a host, so
    it goes on the wire as the speaker itself wrote it.
    """
    return f"[{endereco}]" if ":" in endereco else endereco

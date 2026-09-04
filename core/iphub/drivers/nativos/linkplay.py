# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""LinkPlay multiroom speaker (AudioCast, iEAST), the multiroom driver of sections 6 and 14.

Two transports in one driver, which is exactly why this one is native and not declarative:
HTTP carries the status, the volume, the transport, the URL and the group, and the iEAST
control port carries what only it has, the mute, the hardware preset and the physical input,
honouring the 200 ms minimum between two frames.

What the module that owns the blocks has to know, because section 14 paid days for it:

- the identity is the uuid of getStatusEx and never the address, so identidade_do_aparelho
  is what tells whether the box answering at this ip is still the registered one;
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

Caixa multiroom LinkPlay (AudioCast, iEAST), o driver multiroom das seções 6 e 14.

Dois transportes num driver, que é exatamente por que este é nativo e não declarativo: o
HTTP leva o estado, o volume, o transporte, a URL e o grupo, e a porta de controle do iEAST
leva o que só ela tem, o mudo, o preset de hardware e a entrada física, respeitando o mínimo
de 200 ms entre dois quadros.

O que o módulo dono dos blocos precisa saber, porque a seção 14 pagou dias por isso:

- a identidade é o uuid do getStatusEx e nunca o endereço, então identidade_do_aparelho é
  quem diz se a caixa que responde neste ip ainda é a cadastrada;
- um play em escravo desmonta o grupo, então o transporte de um grupo vai para o mestre e
  este driver recusa transporte, volume e preset enquanto for escravo;
- um escravo responde stop mesmo tocando, então o transporte do mestre é fixado nele com o
  espelhar e lido de volta no Estado.tocando;
- um escravo que sai do modo multiroom por dois polls seguidos perdeu o grupo para um reboot
  ou para o aplicativo do fabricante, e o saiu_do_grupo diz isso;
- o firmware não limpa o título da fonte anterior, então tocando é o título de uma fonte de
  rede que está tocando agora, e nada mais;
- o Estado.tocando leva o título enquanto o transporte toca e None enquanto não toca, então
  quem escreve o DP de play o lê de lá.

A lógica de grupo em si (quem é o mestre, que caixas podem dividir um grupo, o que espelhar)
mora no módulo dono dos blocos. Este driver oferece só os quatro movimentos de que um grupo é
feito: entrar_no_grupo, desfazer_grupo, volume_de_escravo e ler_grupo.
"""

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import ip_literal
from iphub.drivers import corpo
from iphub.drivers.base import Cadastro, Driver
from iphub.drivers.manifesto import Descoberta, Manifesto

log = logging.getLogger("iphub.drivers.nativos.linkplay")

PORTA_HTTP = 80
PORTA_TCP = 8899
TEMPO_LIMITE_S = 4.0

# Why: section 14 measured the minimum the iEAST control port needs between two frames, and
# a driver that ignores it loses the second command in silence.
# Por que: a seção 14 mediu o mínimo que a porta de controle do iEAST precisa entre dois
# quadros, e um driver que o ignora perde o segundo comando em silêncio.
INTERVALO_TCP_MS = 200
MILISSEGUNDO_S = 0.001

# Why: section 14, one lost poll is not a speaker that went away; two in a row is.
# Por que: seção 14, um poll perdido não é uma caixa que sumiu; dois seguidos é.
FALHAS_ATE_OFFLINE = 2

# Why: section 14, a slave out of the multiroom mode for two polls in a row means the
# physical group dissolved by itself, and the logical state has to be reconciled.
# Por que: seção 14, um escravo fora do modo multiroom por dois polls seguidos significa que
# o grupo físico se desfez sozinho, e o estado lógico precisa ser reconciliado.
POLLS_ATE_RECONCILIAR = 2

# Why: a speaker on the LAN must never be able to make the daemon buffer without bound.
# Por que: uma caixa na LAN nunca pode fazer o daemon acumular sem limite.
CORPO_MAXIMO = 64 * 1024
LINHA_MAXIMA = 8 * 1024
TEXTO_MAXIMO = 120
ESCRAVOS_MAXIMO = 12
URL_MAXIMA = 200

CAMINHO = "/httpapi.asp?command="

PEDE_IDENTIDADE = "getStatusEx"
PEDE_ESTADO = "getPlayerStatus"
PEDE_ESCRAVOS = "multiroom:getSlaveList"
MANDA_VOLUME = "setPlayerCmd:vol:{valor}"
MANDA_TOCAR = "setPlayerCmd:play:{valor}"
MANDA_RETOMAR = "setPlayerCmd:resume"
MANDA_PAUSAR = "setPlayerCmd:pause"
MANDA_REDE = "setPlayerCmd:switchmode:wifi"
ENTRA_NO_GRUPO = "ConnectMasterAp:JoinGroupMaster:eth{ip}:wifi0.0.0.0"
DESFAZ_GRUPO = "multiroom:Ungroup"
MANDA_VOLUME_DE_ESCRAVO = "multiroom:SlaveVolume:{ip}:{valor}"

QUADRO_TCP = "MCU+PAS+RAKOIT:{corpo}&"
QUADRO_ENTRADA = "MCU+PLM+{codigo}&"
TCP_MUDO = "MUT:{valor}"
TCP_PRESET = "PRESET:{valor}"

RESPOSTA_OK = "ok"

CHAVE_UUID = "uuid"
CHAVE_ENTRADAS = "plm_support"
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
ACAO_AGRUPAR = "agrupar"
ACAO_COMANDO_EXTRA = "comando_extra"

# Why: what a slave must never do on its own, because on the bench each one of these took the
# group down or desynchronized it; the module that owns the group sends them to the master.
# Por que: o que um escravo nunca pode fazer sozinho, porque na bancada cada um destes
# derrubou o grupo ou o dessincronizou; o módulo dono do grupo os manda para o mestre.
ACOES_DO_MESTRE = (ACAO_VOLUME, ACAO_TOCAR, ACAO_PAUSAR, ACAO_COMANDO_EXTRA)

EQ_OFFLINE = "eq_offline"
INVALID_VALUE = "invalid_value"
ERRO_APARELHO = "erro_aparelho"

# Why: a code of its own would be a phrase the panel cannot translate (section 11), and of
# the five stable codes this is the one that says the speaker cannot do it as it stands: in
# a group its input is the group, and its transport belongs to the master.
# Por que: um código próprio seria frase que o painel não traduz (seção 11), e dos cinco
# códigos estáveis este é o que diz que a caixa não pode fazer isso como está: num grupo a
# entrada dela é o grupo, e o transporte dela é do mestre.
RECUSA_DE_GRUPO = "nao_suportado"

MODO_ESCRAVO = 99
ENTRADA_DE_REDE = "wifi"

VOLUME_MINIMO = 0
VOLUME_MAXIMO = 100
# Why: the speaker speaks the same 0 to 100 the contract of section 6 fixes, and saying it
# with a constant is what keeps a firmware with another range one line away.
# Por que: a caixa fala o mesmo 0 a 100 que o contrato da seção 6 fixa, e dizê-lo com uma
# constante é o que mantém um firmware de outra faixa a uma linha de distância.
VOLUME_MINIMO_DO_APARELHO = 0
VOLUME_MAXIMO_DO_APARELHO = 100

PRESET_MINIMO = 1
PRESET_MAXIMO = 8
PREFIXO_PRESET = "preset:"

# Why: the value of a command lands inside the query string of the speaker, so anything that
# is not one of these bytes could close the command and write a second parameter of its own.
# Por que: o valor de um comando cai dentro da query string da caixa, então o que não for um
# destes bytes poderia fechar o comando e escrever um segundo parâmetro próprio.
_NO_FIO = re.compile(r"[A-Za-z0-9:%._~/\-\[\]]+")
_URL = re.compile(r"https?://[A-Za-z0-9:%._~/\-\[\]]+")
_HEX = re.compile(r"(?:[0-9a-fA-F]{2})+")
_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")
_NUMERO = re.compile(r"-?[0-9]{1,10}")

# A title the firmware writes when it has none of its own.
# Um título que o firmware escreve quando não tem um próprio.
SEM_TITULO = frozenset({"unknown", "un-known", "none", "null"})

type Relogio = Callable[[], float]
type Dormir = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Entrada:
    """One physical input: the bit the hardware declares and the mode it answers and takes.

    Uma entrada física: o bit que o hardware declara e o modo que ele responde e aceita.
    """

    nome: str
    bit: int
    modo: int
    codigo: str


# Why: plm_support is the bitmask saying which inputs the hardware really has, so offering
# one outside it puts a button on the panel that only ever fails. The network input is not
# in the mask because every speaker has it, and it is the one that comes back over HTTP.
# Por que: o plm_support é a máscara que diz que entradas o hardware tem de verdade, então
# oferecer uma fora dela põe no painel um botão que só falha. A entrada de rede não está na
# máscara porque toda caixa a tem, e é a única que se volta por HTTP.
ENTRADAS = (
    Entrada("line-in", bit=1, modo=40, codigo="040"),
    Entrada("bluetooth", bit=2, modo=41, codigo="041"),
    Entrada("usb", bit=3, modo=51, codigo="051"),
    Entrada("optical", bit=4, modo=43, codigo="043"),
)

TEXTOS = {
    "en": {
        "descricao": (
            "LinkPlay multiroom speaker (AudioCast, iEAST). Always on, so it declares no "
            "power: volume, mute, input, transport and native grouping."
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
            "dismantles the group this speaker leads. Only speakers of the same kind."
        ),
        "cap_comando_extra": (
            "Plays a preset stored in the speaker itself, written as preset:1 up to preset:8."
        ),
    },
    "pt": {
        "descricao": (
            "Caixa multiroom LinkPlay (AudioCast, iEAST). Sempre ligada, então não declara "
            "energia: volume, mudo, entrada, transporte e agrupamento nativo."
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
            "que esta caixa lidera. Só caixas do mesmo tipo."
        ),
        "cap_comando_extra": (
            "Toca um preset guardado na própria caixa, escrito como preset:1 até preset:8."
        ),
    },
}


@dataclass(frozen=True)
class Escravo:
    """One member of a group as the master lists it: the uuid is the key, the ip is today's.

    Um membro do grupo como o mestre o lista: o uuid é a chave, o ip é o de hoje.
    """

    identidade: str
    ip: str
    nome: str = ""


@dataclass(frozen=True)
class Grupo:
    """The group the speaker really leads, read from the speaker and not from our own books.

    O grupo que a caixa de fato lidera, lido da caixa e não dos nossos próprios livros.
    """

    escravos: tuple[Escravo, ...] = ()


class _Falha(Exception):
    """A stable code on the way out of an exchange, so no exception escapes executar.

    Um código estável na saída de uma troca, para nenhuma exceção escapar do executar.
    """

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class LinkPlay(Driver):
    """Volume, mute, input, transport and native grouping of a LinkPlay speaker.

    Volume, mudo, entrada, transporte e agrupamento nativo de uma caixa LinkPlay.
    """

    # Why: the speaker is always on, and section 14 says omitting the capability is right,
    # never implementing one to refuse it. The port is not a config field either: this
    # protocol fixes both of them, and a field nobody needs is a field somebody breaks.
    # Por que: a caixa está sempre ligada, e a seção 14 diz que omitir a capacidade é o
    # certo, nunca implementar uma para recusar. A porta também não é campo de cadastro:
    # este protocolo fixa as duas, e um campo que ninguém precisa é um campo que alguém quebra.
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
            ACAO_AGRUPAR,
            ACAO_COMANDO_EXTRA,
        ),
        descoberta=Descoberta(mdns_servicos=("_linkplay._tcp",)),
        textos=TEXTOS,
        motor="nativo",
    )

    def __init__(
        self,
        cadastro: Cadastro,
        *,
        agora: Relogio | None = None,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        super().__init__(cadastro)
        self._porta_http = PORTA_HTTP
        self._porta_tcp = PORTA_TCP
        self._agora = agora
        self._dormir = dormir
        self._sessao: ClientSession | None = None
        # Why: the poll and a command of the integrator land together, and two exchanges at
        # once on the same speaker read each other's answers.
        # Por que: o poll e um comando do integrador caem juntos, e duas trocas ao mesmo
        # tempo na mesma caixa leem a resposta uma da outra.
        self._trava_http = asyncio.Lock()
        self._trava_tcp = asyncio.Lock()
        self._ultimo_quadro = 0.0
        self._identidade: str | None = None
        self._entradas: tuple[str, ...] = ()
        self._falhas = 0
        self._escravo = False
        self._polls_fora = 0
        self._saiu_do_grupo = False
        self._no_grupo = False
        self._espelho: str | None = None
        self._espelho_reproduzindo: bool | None = None

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        """Section 6: the uuid the speaker at that address answers, for a finding of the sweep.

        Seção 6: o uuid que a caixa naquele endereço responde, para um achado da varredura.
        """
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
        except ValueError:
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
        # Why: section 6, the identity is the uuid and the address is only where it answered
        # today. Asking once and never again means a lease that moved to another box leaves the
        # hub commanding whatever now holds the address, under the name of this block, for as
        # long as the daemon runs. The question is one small GET on the LAN, which is a cheap
        # price for never commanding the wrong speaker.
        # Por que: seção 6, a identidade é o uuid e o endereço é só onde ela respondeu hoje.
        # Perguntar uma vez e nunca mais faz uma concessão que passou para outra caixa deixar o
        # hub comandando quem estiver com o endereço, com o nome deste bloco, enquanto o daemon
        # viver. A pergunta é um GET pequeno na LAN, preço barato por nunca comandar a caixa
        # errada.
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
        """The uuid the speaker answered, which is what says the box here is still ours.

        O uuid que a caixa respondeu, que é o que diz se a caixa daqui ainda é a nossa.
        """
        return self._identidade

    def e_escravo(self) -> bool:
        """The last poll saw the speaker in the multiroom slave mode.

        O último poll viu a caixa no modo escravo de multiroom.
        """
        return self._escravo

    def saiu_do_grupo(self) -> bool:
        """It was a slave and left the multiroom mode for two polls: the group dissolved.

        Era escravo e saiu do modo multiroom por dois polls: o grupo se desfez.
        """
        return self._saiu_do_grupo

    def no_grupo(self) -> bool:
        return self._escravo or self._no_grupo

    def marcar_grupo(self, dentro: bool) -> None:
        """Where the owner of the group logic says this speaker stands right now.

        Onde o dono da lógica de grupo diz que esta caixa está agora.
        """
        self._no_grupo = dentro
        # Why: whoever owns the group logic has just declared where this speaker stands, and
        # that settles it in BOTH directions. A verdict left over from an earlier group, that
        # the speaker had left the multiroom mode, would otherwise make the reconcile tear
        # down the group the owner formed one moment ago.
        # Por que: quem é dono da lógica de grupo acabou de declarar onde esta caixa está, e
        # isso resolve nos DOIS sentidos. Um veredito que sobrou de um grupo anterior, de que a
        # caixa tinha saído do modo multiroom, faria a reconciliação derrubar o grupo que o
        # dono formou um instante atrás.
        self._saiu_do_grupo = False
        if not dentro:
            self._espelho = None
            self._espelho_reproduzindo = None

    def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
        """Section 14: a slave answers stop even while it plays, so the transport of the
        master is pinned here by whoever owns the group.

        Seção 14: um escravo responde stop mesmo tocando, então o transporte do mestre é
        fixado aqui por quem é dono do grupo.
        """
        self._espelho = None if tocando is None else _texto(tocando)
        self._espelho_reproduzindo = reproduzindo

    async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
        """Run on the SLAVE: it joins the master at that address.

        Roda no ESCRAVO: ele entra no mestre daquele endereço.
        """
        endereco = ip_literal(ip_do_mestre)
        if endereco is None:
            return INVALID_VALUE
        try:
            await self._mandar(ENTRA_NO_GRUPO.format(ip=endereco))
        except _Falha as falha:
            return falha.codigo
        return None

    async def desfazer_grupo(self) -> str | None:
        """Run on the MASTER: it dismantles the group it leads.

        Roda no MESTRE: ele desfaz o grupo que lidera.
        """
        try:
            await self._mandar(DESFAZ_GRUPO)
        except _Falha as falha:
            return falha.codigo
        return None

    async def volume_de_escravo(self, ip_do_escravo: object, valor: object) -> str | None:
        """Run on the MASTER: section 14, the volume of a slave goes through the master.

        Roda no MESTRE: seção 14, o volume de um escravo passa pelo mestre.
        """
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
        answer one. Section 6: the key of a member is its uuid, never its address.

        Roda no MESTRE: o grupo que a própria caixa lista, ou None quando ela não respondeu
        um. Seção 6: a chave de um membro é o uuid dele, nunca o endereço.
        """
        try:
            return _grupo_de(await self._perguntar(PEDE_ESCRAVOS))
        except _Falha as falha:
            log.warning("speaker %s did not list its group: %s", self.cadastro.identidade, falha)
            return None

    async def _agir(self, acao: str, valor: object) -> str | None:
        # Why: section 14, each one of these on a slave takes the group down or leaves the
        # panel showing a volume the speaker never heard about.
        # Por que: seção 14, cada um destes num escravo derruba o grupo ou deixa o painel
        # mostrando um volume de que a caixa nunca soube.
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
            self._defina(reproduzindo=False, tocando=None)
            return None
        if acao == ACAO_AGRUPAR:
            return await self._agrupar(valor)
        if acao == ACAO_COMANDO_EXTRA:
            return await self._preset(valor)
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
        await self._quadro(QUADRO_TCP.format(corpo=TCP_MUDO.format(valor=int(valor))))
        self._defina(mudo=valor)
        return None

    async def _trocar_fonte(self, valor: object) -> str | None:
        # Why: section 14, changing the input of a speaker that is in a group breaks the
        # group, so it is refused here instead of being discovered on the bench.
        # Por que: seção 14, trocar a entrada de uma caixa que está num grupo quebra o grupo,
        # então isso é recusado aqui em vez de ser descoberto na bancada.
        if self.no_grupo():
            return RECUSA_DE_GRUPO
        entradas = self._entradas or (ENTRADA_DE_REDE,)
        if not isinstance(valor, str) or valor not in entradas:
            return INVALID_VALUE
        if valor == ENTRADA_DE_REDE:
            await self._mandar(MANDA_REDE)
        else:
            await self._quadro(QUADRO_ENTRADA.format(codigo=_entrada_por_nome(valor).codigo))
        self._defina(fonte=valor)
        return None

    async def _tocar(self, valor: object) -> str | None:
        # Why: every other handler pins its own field in the cache, and the bus publishes from
        # that cache once a second, which lands BEFORE the reread of section 8 at 1.5 s. A play
        # that pinned nothing let the tick republish the old transport, so DP 102 fell back to
        # false a second after the command the speaker had accepted.
        # Por que: todo outro handler prende o campo dele no cache, e o barramento publica desse
        # cache uma vez por segundo, o que cai ANTES da releitura da seção 8 em 1,5 s. Um play
        # que não prendia nada deixava o tique republicar o transporte antigo, então o DP 102
        # voltava a falso um segundo depois do comando que a caixa tinha aceitado.
        if valor is None or valor == "":
            await self._mandar(MANDA_RETOMAR)
            self._defina(reproduzindo=True)
            return None
        if not _url_valida(valor):
            return INVALID_VALUE
        await self._mandar(MANDA_TOCAR.format(valor=valor))
        self._defina(reproduzindo=True)
        return None

    async def _agrupar(self, valor: object) -> str | None:
        if valor is None or valor == "":
            return await self.desfazer_grupo()
        return await self.entrar_no_grupo(valor)

    async def _preset(self, valor: object) -> str | None:
        numero = _preset_de(valor)
        if numero is None:
            return INVALID_VALUE
        await self._quadro(QUADRO_TCP.format(corpo=TCP_PRESET.format(valor=f"{numero:02d}")))
        return None

    def _ler_identidade(self, dados: dict) -> None:
        # Why: section 6, the identity of a device is its uuid; the address is only where it
        # answered today, and a hub that keyed by it would lose the box on the next lease.
        # Por que: seção 6, a identidade de um aparelho é o uuid dele; o endereço é só onde
        # ele respondeu hoje, e um hub chaveado por ele perderia a caixa na próxima concessão.
        identidade = _texto(dados.get(CHAVE_UUID))
        # Why: section 6 says the identity is the uuid and the address is only where it
        # answered today. Storing the uuid without ever comparing it means a lease that moved
        # to another box has the hub commanding whatever now holds the address: the volume of
        # a neighbour's speaker, under the name of this block.
        # Por que: a seção 6 diz que a identidade é o uuid e o endereço é só onde ela respondeu
        # hoje. Guardar o uuid sem nunca compará-lo faz uma concessão que passou para outra
        # caixa deixar o hub comandando quem estiver com o endereço agora: o volume da caixa do
        # vizinho, com o nome deste bloco.
        if identidade and self._identidade and identidade != self._identidade:
            raise _Falha(EQ_OFFLINE)
        if identidade:
            self._identidade = identidade
        self._entradas = _entradas_de(dados.get(CHAVE_ENTRADAS))

    def _aplicar(self, dados: dict) -> None:
        self._falhas = 0
        modo = _inteiro(dados.get(CHAVE_MODO))
        self._marcar_escravo(modo == MODO_ESCRAVO)
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

    def _reproduzindo(self, dados: dict) -> bool | None:
        """Whether the transport is playing, on whatever input, which is DP 102 of section 8.

        Why: the title is a different fact, and reading this one from it reported a speaker
        playing over bluetooth, over a line input, or a radio with no metadata, as paused.

        Se o transporte está tocando, em qualquer entrada, que é o DP 102 da seção 8.

        Por que: o título é outro fato, e ler este daquele reportava como pausada uma caixa
        tocando por bluetooth, por entrada de linha, ou um rádio sem metadado.
        """
        # Why: section 14, a slave answers stop even while the group plays.
        # Por que: seção 14, um escravo responde stop mesmo com o grupo tocando.
        if self._escravo:
            return self._espelho_reproduzindo
        estado = _texto(dados.get(CHAVE_ESTADO)).lower()
        if not estado:
            return None
        return estado == TOCANDO

    def _tocando(self, dados: dict, fonte: str | None) -> str | None:
        """The title of a network source that is playing right now, and nothing else.

        O título de uma fonte de rede que está tocando agora, e nada mais.
        """
        # Why: section 14, a slave answers stop even while the group plays, so what the
        # master is playing wins over what the slave says about itself.
        # Por que: seção 14, um escravo responde stop mesmo com o grupo tocando, então o que
        # o mestre toca vence o que o escravo diz de si.
        if self._escravo:
            return self._espelho
        # Why: section 14, the firmware does not clear Title and Artist when the source
        # changes, so a line-in that is playing would show the last track of the radio.
        # Por que: seção 14, o firmware não limpa Title e Artist quando a fonte muda, então
        # uma entrada de linha tocando mostraria a última faixa do rádio.
        if fonte != ENTRADA_DE_REDE or _texto(dados.get(CHAVE_ESTADO)).lower() != TOCANDO:
            return None
        titulo = _titulo(dados.get(CHAVE_TITULO))
        artista = _titulo(dados.get(CHAVE_ARTISTA))
        if titulo and artista:
            return f"{titulo} - {artista}"[:TEXTO_MAXIMO]
        return titulo or artista or None

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
        """Section 14: one lost poll keeps the last state, two in a row is offline.

        Seção 14: um poll perdido guarda o último estado, dois seguidos é offline.
        """
        self._falhas += 1
        if self._falhas < FALHAS_ATE_OFFLINE:
            return
        # Why: section 14, a speaker that went away comes back by its identity in about 50 s,
        # and in the meantime the lease may have handed its address to another box; asking
        # the identity again is what keeps the hub from commanding a stranger.
        # Por que: seção 14, uma caixa que sumiu volta pela identidade em uns 50 s, e nesse
        # meio tempo a concessão pode ter dado o endereço dela a outra caixa; perguntar a
        # identidade de novo é o que impede o hub de comandar uma desconhecida.
        self._identidade = None
        self._defina(online=False, tocando=None, detalhe=codigo)

    async def _abrir(self) -> ClientSession:
        sessao = self._sessao
        if sessao is None or sessao.closed:
            sessao = ClientSession(timeout=ClientTimeout(total=TEMPO_LIMITE_S))
            self._sessao = sessao
        return sessao

    async def _perguntar(self, comando: str) -> dict:
        """A question of the protocol, answered as an object; anything else is a fault.

        Uma pergunta do protocolo, respondida como objeto; qualquer outra coisa é falha.
        """
        corpo = await self._pedir(comando)
        try:
            documento = json.loads(corpo)
        except ValueError as erro:
            raise _Falha(ERRO_APARELHO) from erro
        if not isinstance(documento, dict):
            raise _Falha(ERRO_APARELHO)
        return documento

    async def _mandar(self, comando: str) -> None:
        """A command of the protocol; the speaker answers OK and nothing else means done.

        Um comando do protocolo; a caixa responde OK e nada mais significa feito.
        """
        corpo = await self._pedir(comando)
        # Why: section 14, this firmware answers OK to any command, including one that does
        # not exist, so this check only catches a firmware that does report an error. What
        # really verifies a command on these speakers is the reread of section 8 against the
        # state the device answers, never this line.
        # Por que: seção 14, este firmware responde OK a qualquer comando, inclusive a um que
        # não existe, então esta checagem só pega um firmware que reporta erro. O que
        # verifica de verdade um comando nestas caixas é a releitura da seção 8 contra o
        # estado que o aparelho responde, nunca esta linha.
        if corpo.strip().lower() != RESPOSTA_OK:
            log.warning("speaker answered %r to %s", corpo[:TEXTO_MAXIMO], comando)
            raise _Falha(ERRO_APARELHO)

    async def _pedir(self, comando: str) -> str:
        # Why: the command lands in the query string of the speaker, so a value that carried
        # a separator would write a second parameter that nobody wrote in this file.
        # Por que: o comando cai na query string da caixa, então um valor que levasse um
        # separador escreveria um segundo parâmetro que ninguém escreveu neste arquivo.
        if not _NO_FIO.fullmatch(comando):
            raise _Falha(INVALID_VALUE)
        url = f"http://{_hospedeiro(self._endereco())}:{self._porta_http}{CAMINHO}{comando}"
        async with self._trava_http:
            sessao = await self._abrir()
            try:
                async with sessao.get(
                    url,
                    # Why: a speaker answering a redirect would send the hub to whatever host
                    # it names, which is the LAN proxy section 9 refuses.
                    # Por que: uma caixa respondendo redirecionamento mandaria o hub para o
                    # host que ela nomear, que é o proxy de LAN que a seção 9 recusa.
                    allow_redirects=False,
                ) as resposta:
                    bruto = await corpo.inteiro(resposta.content, CORPO_MAXIMO)
                    estado = resposta.status
            except (TimeoutError, ClientError, OSError, ValueError) as erro:
                raise _Falha(EQ_OFFLINE) from erro
        if estado >= 400:
            log.warning("the speaker answered HTTP %d to %s", estado, comando)
            raise _Falha(ERRO_APARELHO)
        return bruto.decode("utf-8", errors="replace")

    async def _quadro(self, quadro: str) -> None:
        """One frame on the control port, written and never read back, at the declared pace.

        Um quadro na porta de controle, escrito e nunca lido de volta, no ritmo declarado.
        """
        endereco = self._endereco()
        async with self._trava_tcp:
            await self._ritmo()
            try:
                async with asyncio.timeout(TEMPO_LIMITE_S):
                    _leitor, escritor = await asyncio.open_connection(
                        endereco, self._porta_tcp, limit=LINHA_MAXIMA
                    )
                    try:
                        escritor.write(quadro.encode("ascii", errors="ignore"))
                        await escritor.drain()
                    finally:
                        await _fechar(escritor)
            except (TimeoutError, OSError) as erro:
                raise _Falha(EQ_OFFLINE) from erro
            finally:
                self._ultimo_quadro = self._relogio()

    async def _ritmo(self) -> None:
        """Holds the minimum section 14 measured between two frames of the control port.

        Segura o mínimo que a seção 14 mediu entre dois quadros da porta de controle.
        """
        atraso = self._ultimo_quadro + INTERVALO_TCP_MS * MILISSEGUNDO_S - self._relogio()
        if atraso > 0:
            await self._dormir(atraso)

    def _relogio(self) -> float:
        if self._agora is not None:
            return self._agora()
        return asyncio.get_running_loop().time()

    def _endereco(self) -> str:
        """Section 9: only an IP literal reaches a speaker, so the hub is never a resolver.

        Seção 9: só um IP literal alcança uma caixa, então o hub nunca é um resolvedor.
        """
        endereco = ip_literal(self.cadastro.ip)
        if endereco is None:
            raise _Falha(EQ_OFFLINE)
        return endereco


def _entradas_de(bruto: object) -> tuple[str, ...]:
    """The inputs the hardware declares in its mask, with the network one it always has.

    As entradas que o hardware declara na máscara dele, com a de rede que ele sempre tem.
    """
    mascara = _mascara(bruto)
    if mascara is None or mascara < 0:
        return (ENTRADA_DE_REDE,)
    return (ENTRADA_DE_REDE, *(e.nome for e in ENTRADAS if mascara & (1 << e.bit)))


def _mascara(bruto: object) -> int | None:
    """The mask of inputs, which the firmware writes in decimal or in hexadecimal.

    A máscara de entradas, que o firmware escreve em decimal ou em hexadecimal.
    """
    if isinstance(bruto, str) and bruto.strip().lower().startswith("0x"):
        return _inteiro(bruto.strip()[2:], base=16)
    return _inteiro(bruto)


def _entrada_por_nome(nome: str) -> Entrada:
    return next(entrada for entrada in ENTRADAS if entrada.nome == nome)


def _fonte_do_modo(modo: int | None) -> str | None:
    """The input the speaker says it is on; the multiroom mode is a group and not an input.

    A entrada em que a caixa diz estar; o modo multiroom é um grupo e não uma entrada.
    """
    if modo is None or modo == MODO_ESCRAVO:
        return None
    for entrada in ENTRADAS:
        if entrada.modo == modo:
            return entrada.nome
    # Why: everything the mask does not name is the network side of the speaker, which is
    # where airplay, the streaming services and a played URL all live.
    # Por que: tudo que a máscara não nomeia é o lado de rede da caixa, onde moram o airplay,
    # os serviços de streaming e uma URL tocada.
    return ENTRADA_DE_REDE


def _grupo_de(dados: dict) -> Grupo:
    """The members the master listed, keyed by uuid, capped, and without a member whose
    address is a name instead of an address.

    Os membros que o mestre listou, chaveados por uuid, com teto, e sem membro cujo endereço
    seja um nome em vez de um endereço.
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
    # Why: True is an int in Python, and a mute arriving where a volume belongs would be
    # written as the volume 1, which is a speaker that went silent for no reason.
    # Por que: True é int em Python, e um mudo chegando onde cabe volume seria gravado como
    # volume 1, que é uma caixa que emudeceu sem motivo.
    return type(valor) is int and VOLUME_MINIMO <= valor <= VOLUME_MAXIMO


def _para_o_aparelho(valor: int) -> int:
    """The 0 to 100 of section 6 in the range the speaker speaks.

    O 0 a 100 da seção 6 na faixa que a caixa fala.
    """
    largura = VOLUME_MAXIMO_DO_APARELHO - VOLUME_MINIMO_DO_APARELHO
    return VOLUME_MINIMO_DO_APARELHO + round(valor * largura / VOLUME_MAXIMO)


def _do_aparelho(bruto: object) -> int | None:
    """What the speaker answered in the 0 to 100 of section 6, or None when it is not one.

    O que a caixa respondeu no 0 a 100 da seção 6, ou None quando não é um.
    """
    numero = _inteiro(bruto)
    if numero is None:
        # Why: a speaker that answers a word where a number belongs is not a volume of zero,
        # and writing zero would tell the panel a speaker is silent while it plays.
        # Por que: uma caixa que responde palavra onde cabe número não é volume zero, e
        # gravar zero diria ao painel que uma caixa está calada enquanto ela toca.
        return None
    largura = VOLUME_MAXIMO_DO_APARELHO - VOLUME_MINIMO_DO_APARELHO
    preso = max(VOLUME_MINIMO_DO_APARELHO, min(VOLUME_MAXIMO_DO_APARELHO, numero))
    convertido = (preso - VOLUME_MINIMO_DO_APARELHO) * VOLUME_MAXIMO / largura
    return max(VOLUME_MINIMO, min(VOLUME_MAXIMO, round(convertido)))


def _preset_de(valor: object) -> int | None:
    """The hardware preset of comando_extra, written as preset:1 up to preset:8.

    O preset de hardware do comando_extra, escrito como preset:1 até preset:8.
    """
    if not isinstance(valor, str):
        return None
    cabeca, separador, numero = valor.strip().lower().partition(":")
    if not separador or f"{cabeca}:" != PREFIXO_PRESET or not _NUMERO.fullmatch(numero):
        return None
    escolhido = int(numero)
    if not PRESET_MINIMO <= escolhido <= PRESET_MAXIMO:
        return None
    return escolhido


def _url_valida(valor: object) -> bool:
    """The address of a stream the speaker fetches by itself, and never anything else.

    O endereço de um fluxo que a caixa busca sozinha, e nunca outra coisa.
    """
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
    """What the speaker wrote, cleaned and capped, exactly as it wrote it.

    O que a caixa escreveu, limpo e com teto, exatamente como ela escreveu.
    """
    if isinstance(bruto, bool) or bruto is None:
        return ""
    return _CONTROLE.sub("", str(bruto).strip())[:TEXTO_MAXIMO]


def _metadado(bruto: object) -> str:
    """A title or an artist, which the firmware answers in hexadecimal.

    Why: a title that is not hexadecimal, and one whose bytes are not text, are both read as
    they arrived, because a strict decode is the only thing that tells a hex title from a
    title that happens to be spelled with the letters of the hexadecimal alphabet.

    O que a caixa escreveu, como texto: o firmware responde metadado em hexadecimal.

    Por que: um título que não é hexadecimal, e um cujos bytes não são texto, são lidos como
    chegaram, porque uma decodificação estrita é a única coisa que distingue um título em hex
    de um título escrito por acaso com as letras do alfabeto hexadecimal.
    """
    texto = _texto(bruto)
    if _HEX.fullmatch(texto):
        with suppress(ValueError, UnicodeDecodeError):
            texto = _CONTROLE.sub("", bytes.fromhex(texto).decode("utf-8"))
    return texto[:TEXTO_MAXIMO]


def _inteiro(bruto: object, *, base: int = 10) -> int | None:
    """The number the speaker answered, which it writes as text and sometimes as hexadecimal.

    O número que a caixa respondeu, que ela escreve como texto e às vezes como hexadecimal.
    """
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

    O endereço como HOST de uma URL: um IPv6 mora entre colchetes lá, ou os dois pontos do
    endereço viram porta. O endereço dentro de um comando é valor e não host, então ele vai
    para o fio como a própria caixa o escreveu.
    """
    return f"[{endereco}]" if ":" in endereco else endereco


async def _fechar(escritor: asyncio.StreamWriter) -> None:
    escritor.close()
    # Why: a cancellation of the poll must reach the caller, so only the noise of a peer that
    # already went away is swallowed here.
    # Por que: um cancelamento do poll precisa chegar a quem chamou, então só o ruído de um
    # par que já foi embora é engolido aqui.
    with suppress(OSError, TimeoutError):
        await escritor.wait_closed()

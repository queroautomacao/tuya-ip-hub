# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 6, 8 and 14: the numbers of every licence, the data points they publish and the
group of a licence of audio and video.

A number is only what section 8 says it is, one of the equipment numbers of a licence, which
any registered equipment of the right product may occupy, so there is no second registry
here: the numbers are an ORDER over identities already registered as equipment, the position
IS the number, and an empty string is a number nobody occupies. Removing an equipment empties
its slot instead of shifting the rest, because a shift moves an equipment from number 2 to
number 1 in every automation the customer already built on the platform, and nothing on the
bus would say it happened.

A licence of the product ar carries air conditioners and nothing else; a licence of the
product av carries everything else. What the data points of a number mean follows the
manifest of its driver: the power switch of an equipment that declares both power
capabilities, the level of one that declares volume, and so on (section 8).

The group logic the LinkPlay driver deliberately did not take lives here, per licence of
audio and video, and every rule of it was paid for on the bench (section 14):

- a group is formed by naming a MASTER, and only speakers of the same tipo are invited: a
  mixed group is never offered, so a speaker of another kind is not even asked to join;
- a play on a slave dismantles the group, so the transport of a number that is a slave is
  routed to the master;
- the volume of a slave goes through the master, never to the slave itself;
- a slave answers stop even while the group plays, so what the master plays is mirrored onto
  every slave and read back from the state of the slave;
- a slave that left the multiroom mode for two polls in a row lost its group to a reboot or
  to the application of the manufacturer, and the logical state is reconciled;
- a zombie group of a previous run, or of an address that changed under us, is sanitized on
  boot before anything is published;
- forming, sanitizing and reconciling race each other, so ONE lock serializes all of it, and
  a command of a number takes the same lock because the routing decision it makes (is this
  number a slave, and who leads it) has to be the same one the group logic is holding.

The group data point carries the NUMBER of the master, which is what keeps it stable: a
group defined as an entry in a list would renumber itself the day a number is added, which is
the same silent move the empty slot exists to prevent. Multiroom is a capability of the
equipment (section 6), so only a number whose manifest declares it can lead or join.

Seções 6, 8 e 14: os números de cada licença, os data points que eles publicam e o grupo de
uma licença de áudio e vídeo.

Um número é só o que a seção 8 diz que ele é, um dos números de equipamento de uma licença,
que qualquer equipamento cadastrado do produto certo pode ocupar, então não existe segundo
cadastro aqui: os números são uma ORDEM sobre identidades já cadastradas como equipamento, a
posição É o número, e uma string vazia é um número que ninguém ocupa. Remover um equipamento
esvazia a vaga dele em vez de empurrar o resto, porque empurrar move um equipamento do número
2 para o número 1 em toda automação que o cliente já montou na plataforma, e nada no
barramento diria que isso aconteceu.

Uma licença do produto ar carrega ares condicionados e nada mais; uma licença do produto av
carrega todo o resto. O que os data points de um número significam segue o manifesto do
driver dele: a chave de ligar de um equipamento que declara as duas capacidades de energia,
o nível de um que declara volume, e assim por diante (seção 8).

A lógica de grupo que o driver LinkPlay de propósito não tomou mora aqui, por licença de
áudio e vídeo, e cada regra dela foi paga na bancada (seção 14):

- um grupo é formado nomeando um MESTRE, e só caixas do mesmo tipo são convidadas: um grupo
  misto nunca é oferecido, então uma caixa de outro tipo nem chega a ser chamada;
- um play num escravo desmonta o grupo, então o transporte de um número que é escravo vai
  para o mestre;
- o volume de um escravo passa pelo mestre, nunca pelo próprio escravo;
- um escravo responde stop mesmo com o grupo tocando, então o que o mestre toca é espelhado
  em todo escravo e lido de volta no estado do escravo;
- um escravo que saiu do modo multiroom por dois polls seguidos perdeu o grupo para um reboot
  ou para o aplicativo do fabricante, e o estado lógico é reconciliado;
- um grupo zumbi de uma execução anterior, ou de um endereço que mudou por baixo, é saneado
  no boot antes de qualquer publicação;
- formar, sanear e reconciliar correm uns sobre os outros, então UMA trava serializa tudo, e
  um comando de número toma a mesma trava porque a decisão de rota que ele faz (este número é
  escravo, e quem o lidera) precisa ser a mesma que a lógica de grupo está segurando.

O data point de grupo leva o NÚMERO do mestre, que é o que o mantém estável: um grupo
definido como entrada de uma lista se renumeraria no dia em que um número fosse acrescentado,
que é a mesma mudança silenciosa que a vaga vazia existe para impedir. Multiroom é capacidade
do equipamento (seção 6), então só um número cujo manifesto a declara pode liderar ou entrar
num grupo.
"""

import asyncio
import logging
import time
from collections.abc import Awaitable, Iterable, Mapping, Sequence
from dataclasses import dataclass

from iphub.config import Cadastro, Licenca
from iphub.dpbus import comando, mapa, perfil, protocolo
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.gestor import EQ_NAO_ENCONTRADO, EQ_OFFLINE, ERRO_APARELHO, Gestor
from iphub.drivers.manifesto import (
    CAPACIDADE_DE_GRUPO,
    CATEGORIA_DE_GRUPO,
    MODOS_AR,
    VENTOS,
    Estado,
    Manifesto,
    produto_de,
)

log = logging.getLogger("iphub.dpbus.numeros")

VAZIA = ""
SOLO = 0

# Why: the lock is held while a speaker answers, so the deadline of one call into a driver is
# what keeps a box that accepted the connection and went quiet from freezing the group of the
# whole licence.
# Por que: a trava fica presa enquanto uma caixa responde, então o prazo de uma chamada para
# dentro de um driver é o que impede uma caixa que aceitou a conexão e emudeceu de congelar o
# grupo da licença inteira.
LIMITE_S = 5.0

# Why: a speaker held in a group this hub does not lead is asked to leave, and section 14
# records Ungroup as a command of the master, so a slave that ignores it would be asked
# again on every tick; one request a minute is a reminder, one a second is a flood.
# Por que: uma caixa presa num grupo que este hub não lidera é convidada a sair, e a seção 14
# registra o Ungroup como comando do mestre, então um escravo que o ignora seria convidado de
# novo a cada tique; um pedido por minuto é lembrete, um por segundo é inundação.
ESPERA_DE_SAIDA_S = 60.0

# The actions of section 6 a data point of a number turns into.
# As ações da seção 6 em que um data point de número se transforma.
ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_MUDO = "mudo"
ACAO_FONTE = "fonte"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PARAR = "parar"
ACAO_TEMPERATURA = "temperatura"
ACAO_MODO = "modo"
ACAO_VENTO = "vento"
TRANSPORTE = ("tocar", "pausar", "parar", "proxima", "anterior")
# Why: section 14, a play on a slave dismantles the group, and so does a radio or a preset
# pressed on it; everything that starts audio on a member of a group belongs to the master.
# Por que: seção 14, um play num escravo desmonta o grupo, e uma rádio ou um preset apertado
# nele também; tudo que inicia áudio num membro de um grupo é do mestre.
DO_MESTRE = (*TRANSPORTE, "atalho")

# The one action of a scene that is not a capability of section 6, section 8.
# A única ação de uma cena que não é capacidade da seção 6, seção 8.
ACAO_GRUPO = "grupo"

# The functions of the map this module answers for, section 8.
# As funções do mapa por que este módulo responde, seção 8.
F_LIGADO = "ligado"
F_TEMPERATURA = "temperatura"
F_MODO = "modo"
F_VENTO = "vento"
F_NIVEL = "nivel"
F_GRUPO = "grupo"
F_COMANDO = "comando"
F_ONLINE = "online"
F_MUDOS = "mudos"
F_ENTRADAS = "entradas"
F_MODOS = "modos"
F_TITULOS = "titulos"
F_PERFIS = "perfis"
F_NOMES = "nomes"
F_CENA = "cena"

# The stable codes an order refuses with; the panel translates them, section 11.
# Os códigos estáveis com que uma ordem recusa; o painel os traduz, seção 11.
NUMEROS_DEMAIS = "numeros_demais"
NUMERO_REPETIDO = "numero_repetido"
NUMERO_OCUPADO = "numero_ocupado"
IDENTIDADE_INVALIDA = "identidade_invalida"
PRODUTO_INCOMPATIVEL = "produto_incompativel"
CODIGOS_DE_ORDEM = (
    NUMEROS_DEMAIS,
    NUMERO_REPETIDO,
    NUMERO_OCUPADO,
    EQ_NAO_ENCONTRADO,
    IDENTIDADE_INVALIDA,
    PRODUTO_INCOMPATIVEL,
    mapa.PERFIS_LONGOS,
)

# Everything aplicar may answer, and nothing else: the bus vocabulary of section 8 plus the
# two codes of section 6 that say the equipment itself refused.
# Tudo que o aplicar pode responder, e nada mais: o vocabulário de barramento da seção 8 mais
# os dois códigos da seção 6 que dizem que o próprio equipamento recusou.
CODIGOS = (
    protocolo.DP_DESCONHECIDO,
    protocolo.DP_SOMENTE_LEITURA,
    protocolo.VALOR_INVALIDO,
    protocolo.NUMERO_OFFLINE,
    protocolo.LICENCA_DESCONHECIDA,
    NAO_SUPORTADO,
    "auth_pendente",
    ERRO_APARELHO,
)

INVALID_VALUE = "invalid_value"

# The methods a speaker has to offer for a group to be made of it; a multiroom driver that
# does not carry them is a driver this module refuses to command instead of one it breaks.
# Os métodos que uma caixa precisa oferecer para um grupo ser feito dela; um driver multiroom
# que não os carrega é um driver que este módulo recusa comandar em vez de um que ele quebra.
MOVIMENTOS = (
    "e_escravo",
    "entrar_no_grupo",
    "desfazer_grupo",
    "volume_de_escravo",
    "ler_grupo",
    "marcar_grupo",
    "espelhar",
    "saiu_do_grupo",
)


class OrdemInvalida(ValueError):
    """Carries the stable code the route answers with, so no route invents one of its own.

    Carrega o código estável com que a rota responde, para nenhuma rota inventar um próprio.
    """

    def __init__(self, codigo: str, detalhe: str) -> None:
        self.codigo = codigo
        super().__init__(f"{codigo}: {detalhe}")


@dataclass(frozen=True)
class _Alvo:
    """One filled number: the number, its registration and the driver mounted for it.

    Um número ocupado: o número, o cadastro dele e o driver montado para ele.
    """

    numero: int
    cadastro: Cadastro
    driver: Driver


def sem(ordem: Sequence[str], identidade: str) -> tuple[str, ...]:
    """The same order with that identity gone and its NUMBER still there, empty.

    A mesma ordem sem aquela identidade e com o NÚMERO dela ainda ali, vazio.
    """
    # Why: section 8 numbers by position, so closing the hole would move every equipment
    # below it one number up, in silence, on a bus a customer already automated.
    # Por que: a seção 8 numera pela posição, então fechar o buraco moveria todo equipamento
    # abaixo dele um número para cima, em silêncio, num barramento que um cliente já
    # automatizou.
    return tuple(VAZIA if atual == identidade else atual for atual in ordem)


def _identidade_em(ordem: tuple[str, ...], numero: int) -> str:
    """The identity a given order puts in one number, which is how a change is judged before
    it is written.

    A identidade que uma dada ordem põe num número, que é como uma mudança é julgada antes de
    ser gravada.
    """
    if numero < 1 or numero > len(ordem):
        return VAZIA
    return ordem[numero - 1]


class Numeros:
    """The numbers of ONE licence, the data points they publish and the group they may form.

    Os números de UMA licença, os data points que eles publicam e o grupo que podem formar.
    """

    def __init__(
        self,
        gestor: Gestor,
        licenca: Licenca,
        ordem: Iterable[str] = (),
        *,
        limite_s: float = LIMITE_S,
    ) -> None:
        self._gestor = gestor
        self._licenca = licenca
        self._ordem = tuple(ordem)
        self._limite_s = limite_s
        # Why: forming a group, sanitizing a zombie one on boot and reconciling one that
        # dissolved by itself all rewrite who leads whom, and the bench showed them landing on
        # top of each other; a command of a number reads the same book to decide its route.
        # Por que: formar um grupo, sanear um zumbi no boot e reconciliar um que se desfez
        # sozinho reescrevem todos quem lidera quem, e a bancada os viu caindo um sobre o
        # outro; um comando de número lê o mesmo livro para decidir a rota dele.
        self._trava = asyncio.Lock()
        self._mestre = SOLO
        self._escravos: tuple[int, ...] = ()
        self._pedidos_de_saida: dict[int, float] = {}

    @property
    def licenca(self) -> Licenca:
        return self._licenca

    @property
    def id(self) -> str:
        return self._licenca.id

    @property
    def produto(self) -> str:
        return self._licenca.produto

    @property
    def capacidade(self) -> int:
        return mapa.NUMEROS[self.produto]

    @property
    def ordem(self) -> tuple[str, ...]:
        return self._ordem

    @property
    def multiroom(self) -> bool:
        """Only a licence of audio and video ever forms a group, section 8.

        Só uma licença de áudio e vídeo forma grupo, seção 8.
        """
        return self.produto == mapa.PRODUTO_AV

    def trocar_licenca(self, licenca: Licenca) -> None:
        """Takes the edited identity of the licence, keeping the numbers and the group.

        Assume a identidade editada da licença, mantendo os números e o grupo.
        """
        if licenca.produto != self._licenca.produto:
            raise ValueError("the product of a licence never changes")
        self._licenca = licenca

    def identidade(self, numero: int) -> str:
        """The identity occupying one number, or the empty string when nobody occupies it.

        A identidade que ocupa um número, ou a string vazia quando ninguém o ocupa.
        """
        if not 1 <= numero <= self.capacidade or numero > len(self._ordem):
            return VAZIA
        return self._ordem[numero - 1]

    def numero(self, identidade: str) -> int:
        """The number one identity occupies, or 0 for an identity that occupies none.

        O número que uma identidade ocupa, ou 0 para uma identidade que não ocupa nenhum.
        """
        if not identidade:
            return 0
        for posicao, atual in enumerate(self._ordem, start=1):
            if atual == identidade:
                return posicao
        return 0

    def ocupadas(self) -> tuple[str, ...]:
        return tuple(identidade for identidade in self._ordem if identidade)

    def grupo(self) -> int:
        """The value of the group data point right now: 0 solo, n led by number n.

        O valor do data point de grupo agora: 0 solo, n liderado pelo número n.
        """
        return self._mestre

    def escravos(self) -> tuple[int, ...]:
        return self._escravos

    def segue_um_mestre(self, identidade: str) -> bool:
        """Whether this equipment is a slave of the group of this licence right now.

        Se este equipamento é escravo do grupo desta licença agora.
        """
        return self.numero(identidade) in self._escravos

    def validar(self, ordem: object, alheias: Iterable[str] = ()) -> tuple[str, ...]:
        """The order as it would be saved, or OrdemInvalida with the code that refused it.

        alheias are the identities occupying a number of ANOTHER licence, which this one may
        not take: one equipment in two numbers of the installation would answer two data
        points, and the bridge would read a device that contradicts itself.

        A ordem como ela seria salva, ou OrdemInvalida com o código que a recusou.

        alheias são as identidades que ocupam número de OUTRA licença, que esta não pode
        tomar: um equipamento em dois números da instalação responderia dois data points, e a
        ponte leria um aparelho que se contradiz.
        """
        if not isinstance(ordem, list | tuple):
            raise OrdemInvalida(IDENTIDADE_INVALIDA, f"the order is a list, found {ordem!r}")
        lista: list[str] = []
        for bruto in ordem:
            if not isinstance(bruto, str):
                raise OrdemInvalida(IDENTIDADE_INVALIDA, f"an identity is text, found {bruto!r}")
            lista.append(bruto.strip())
        if len(lista) > self.capacidade:
            raise OrdemInvalida(
                NUMEROS_DEMAIS,
                f"the product {self.produto} numbers {self.capacidade}, found {len(lista)}",
            )
        ocupadas = [identidade for identidade in lista if identidade]
        repetidas = sorted({i for i in ocupadas if ocupadas.count(i) > 1})
        if repetidas:
            raise OrdemInvalida(NUMERO_REPETIDO, f"the identidade {repetidas} occupies two numbers")
        tomadas = set(alheias)
        cadastros = self._cadastros()
        for identidade in ocupadas:
            if identidade not in cadastros:
                raise OrdemInvalida(
                    EQ_NAO_ENCONTRADO, f"{identidade!r} is not a registered equipment"
                )
            if identidade in tomadas:
                raise OrdemInvalida(
                    NUMERO_OCUPADO, f"{identidade!r} already occupies a number of another licence"
                )
            manifesto = self._gestor.manifesto(identidade)
            # Why: section 8, an air conditioner only enters a licence of ar and everything
            # else only enters a licence of av; a manifest that left the image cannot be
            # judged, and a number is not emptied on boot because a driver failed to load.
            # Por que: seção 8, um ar condicionado só entra numa licença de ar e todo o resto
            # só entra numa licença de av; um manifesto que saiu da imagem não pode ser
            # julgado, e um número não é esvaziado no boot porque um driver falhou ao carregar.
            if manifesto is not None and produto_de(manifesto.categoria) != self.produto:
                raise OrdemInvalida(
                    PRODUTO_INCOMPATIVEL,
                    f"{identidade!r} does not belong in a licence of {self.produto}",
                )
        if self.multiroom and not self._perfis_cabem(tuple(lista), cadastros, None):
            raise OrdemInvalida(
                mapa.PERFIS_LONGOS, "the profiles of these numbers do not fit their strings"
            )
        return tuple(lista)

    async def definir_ordem(self, ordem: object, alheias: Iterable[str] = ()) -> tuple[str, ...]:
        """Saves the order after validating it, and drops a group a number just left.

        Grava a ordem depois de validá-la, e desfaz um grupo que um número acabou de deixar.
        """
        nova = self.validar(ordem, alheias)
        async with self._trava:
            # Why: the group has to be taken down while the OLD order still resolves the
            # master, because _multiroom reads the order: rewriting it first makes the master
            # unreachable, so multiroom:Ungroup never reaches the wire and the speakers stay
            # physically grouped forever while the hub publishes solo.
            # Por que: o grupo precisa cair enquanto a ordem ANTIGA ainda resolve o mestre,
            # porque o _multiroom lê a ordem: reescrevê-la antes deixa o mestre inalcançável,
            # então o multiroom:Ungroup nunca chega ao fio e as caixas ficam fisicamente
            # agrupadas para sempre enquanto o hub publica solo.
            await self._conferir_membros(nova)
            self._ordem = nova
        return nova

    async def esquecer(self, identidade: str) -> tuple[str, ...]:
        """The number of a removed equipment stays there, empty, and its group is dismantled.

        O número de um equipamento removido continua ali, vazio, e o grupo dele é desfeito.
        """
        async with self._trava:
            nova = sem(self._ordem, identidade)
            await self._conferir_membros(nova)
            self._ordem = nova
        return self._ordem

    async def desligar(self) -> None:
        """The licence is leaving the installation: its group falls, whatever the master says.

        A licença está saindo da instalação: o grupo dela cai, diga o que disser o mestre.
        """
        async with self._trava:
            await self._desfazer(forcar=True)

    def perfis_cabem(self, substituto: Cadastro | None = None) -> bool:
        """True when the profiles of this licence still pack with one registration replaced,
        which is what a route checks before writing an edited registration.

        Verdadeiro quando os perfis desta licença ainda cabem com um cadastro trocado, que é o
        que uma rota confere antes de gravar um cadastro editado.
        """
        if not self.multiroom:
            return True
        return self._perfis_cabem(self._ordem, self._cadastros(), substituto)

    def valores(self) -> dict[int, object]:
        """Every reportable data point of this licence, ready for a report or a snapshot.

        Todo data point reportável desta licença, pronto para um report ou um snapshot.
        """
        if self.produto == mapa.PRODUTO_AR:
            return self._valores_ar()
        return self._valores_av()

    async def aplicar(self, dpid: object, valor: object) -> str | None:
        """One set of section 8 on this licence, done or refused with a stable code; nothing
        raises out.

        Um set da seção 8 nesta licença, feito ou recusado com um código estável; nada
        estoura daqui.
        """
        dp = mapa.de_dp(self.produto, dpid)
        if dp is None:
            return protocolo.DP_DESCONHECIDO
        if not dp.ajustavel:
            return protocolo.DP_SOMENTE_LEITURA
        if dp.funcao == F_CENA:
            # Why: the scene belongs to the module that owns the scenes; a numbers module that
            # answered for it would run a scene from the wrong book.
            # Por que: a cena é do módulo dono das cenas; um módulo de números que respondesse
            # por ela rodaria uma cena do livro errado.
            return protocolo.DP_DESCONHECIDO
        if not protocolo.valor_valido(dp, valor):
            return protocolo.VALOR_INVALIDO
        async with self._trava:
            if dp.funcao == F_GRUPO:
                return await self._ativar(valor)
            if dp.funcao == F_COMANDO:
                return await self._comando(valor)
            return await self._no_numero(dp, valor)

    async def acionar(self, identidade: str, acao: str, valor: object) -> str | None:
        """One action of a scene on an equipment of this licence, routed through the group the
        way a data point would be.

        Uma ação de cena num equipamento desta licença, roteada pelo grupo do jeito que um
        data point seria.
        """
        async with self._trava:
            if acao == ACAO_GRUPO:
                return await self._grupo_por_nome(valor)
            alvo = self._alvo(self.numero(identidade))
            if alvo is None:
                return protocolo.NUMERO_OFFLINE
            if acao == ACAO_VOLUME:
                return await self._volume(alvo, valor)
            if acao in DO_MESTRE:
                return await self._transporte(alvo, acao, valor)
            return await self._executar(identidade, acao, valor)

    async def reler(self, dpid: object, valor: object = None) -> None:
        """Asks the equipment that owns a data point for its state, out of turn and awaited.

        Why: the reread of section 8 is a check against the DEVICE. Publishing from the cache
        1.5 s after the command compares the optimistic value against a cache the command
        itself wrote, so the check agreed with the guess every time and a speaker that
        accepted a volume and ignored it kept the wrong value on the bus until the next poll.

        Pede o estado ao equipamento dono de um data point, fora da vez e esperando.

        Por que: a releitura da seção 8 é uma conferência contra o APARELHO. Publicar do cache
        1,5 s depois do comando compara o valor otimista com um cache que o próprio comando
        escreveu, então a conferência concordava com o palpite toda vez e uma caixa que
        aceitasse um volume e o ignorasse mantinha o valor errado no barramento até o poll
        seguinte.
        """
        dp = mapa.de_dp(self.produto, dpid)
        if dp is None:
            return
        numero = dp.numero
        if dp.funcao == F_COMANDO:
            lido = comando.ler(valor, self.capacidade)
            numero = 0 if lido is None else lido.numero
        identidade = self.identidade(numero) if numero else VAZIA
        if identidade:
            await self._gestor.visitar_e_esperar(identidade)

    def escravos_alheios(self) -> tuple[int, ...]:
        """The numbers whose speaker is in multiroom slave mode of a group this hub does NOT
        lead, which is a state the customer can reach with the app of the manufacturer, or a
        lost reply to a join, or a restart while a group was up.

        Why: a speaker in that mode refuses volume, transport, preset and input, and nothing
        here ever put it there, so reporting it as solo drew a panel full of controls that
        only ever answer no, with nothing anywhere saying why.

        Os números cuja caixa está em modo escravo de multiroom de um grupo que este hub NÃO
        lidera, que é um estado que o cliente alcança com o app do fabricante, ou uma resposta
        perdida a um convite, ou um reinício com um grupo de pé.

        Por que: uma caixa nesse modo recusa volume, transporte, preset e entrada, e nada aqui
        a pôs lá, então reportá-la como solo desenhava um painel cheio de controles que só
        respondem não, sem nada em lugar nenhum dizendo por quê.
        """
        nossos = {self._mestre, *self._escravos}
        alheios = []
        for alvo in self._multirooms():
            if alvo.numero in nossos:
                continue
            if alvo.driver.e_escravo():
                alheios.append(alvo.numero)
        return tuple(alheios)

    async def sanear(self) -> None:
        """Boot: a group left behind by a previous run is taken down before anything else.

        Boot: um grupo deixado por uma execução anterior cai antes de qualquer outra coisa.
        """
        if not self.multiroom:
            return
        async with self._trava:
            self._mestre = SOLO
            self._escravos = ()
            alvos = tuple(self._multirooms())
            for alvo in alvos:
                alvo.driver.marcar_grupo(False)
            # Why: this runs before the listening socket opens, and section 14 measured
            # /health answering in about 7 s on the reference appliance. Asking the speakers
            # one after the other spends a deadline per speaker, so a site whose boxes are
            # unreachable (a VLAN change, a router reboot) had no panel for half a minute,
            # which is exactly when the operator needs it most. Asking them together costs the
            # slowest one instead of the sum.
            # Por que: isto roda antes de o socket de escuta abrir, e a seção 14 mediu o
            # /health respondendo em uns 7 s no appliance de referência. Perguntar às caixas
            # uma depois da outra gasta um prazo por caixa, então um site com as caixas
            # inalcançáveis (troca de VLAN, reboot de roteador) ficava sem painel por meio
            # minuto, que é justamente quando o operador mais precisa dele. Perguntar a todas
            # juntas custa a mais lenta, não a soma.
            grupos = await asyncio.gather(
                *(self._ler(alvo.driver.ler_grupo()) for alvo in alvos),
                return_exceptions=True,
            )
            lideres = [
                alvo
                for alvo, grupo in zip(alvos, grupos, strict=True)
                if not isinstance(grupo, BaseException) and getattr(grupo, "escravos", ())
            ]
            for alvo in lideres:
                # Why: section 14, a zombie group of a previous run, or one an address change
                # left behind, answers commands nobody in this run asked for; the hub only
                # publishes a state it knows, so the physical group goes down first.
                # Por que: seção 14, um grupo zumbi de uma execução anterior, ou um que uma
                # troca de endereço deixou para trás, responde a comandos que ninguém desta
                # execução pediu; o hub só publica estado que conhece, então o grupo físico
                # cai primeiro.
                log.warning("number %d led a group nobody asked for, taking it down", alvo.numero)
            await asyncio.gather(
                *(self._chamar(alvo.driver.desfazer_grupo()) for alvo in lideres),
                return_exceptions=True,
            )
            await self._recuperar_alheios()

    async def sincronizar(self) -> None:
        """Reconciles a group that dissolved by itself and mirrors the master onto the slaves.

        Reconcilia um grupo que se desfez sozinho e espelha o mestre nos escravos.
        """
        if not self.multiroom:
            return
        async with self._trava:
            # Why: a speaker the customer grouped with the application of the manufacturer,
            # hours after boot, is held in a group this hub does not lead, and this used to
            # return before ever looking because our books say solo, which is exactly the case
            # in question.
            # Por que: uma caixa que o cliente agrupou com o aplicativo do fabricante, horas
            # depois do boot, fica presa num grupo que este hub não lidera, e isto voltava
            # antes de olhar porque os nossos livros dizem solo, que é justamente o caso.
            await self._recuperar_alheios()
            if not self._mestre:
                return
            mestre = self._multiroom(self._mestre)
            if mestre is None:
                self._soltar()
                return
            restantes = []
            for numero in self._escravos:
                escravo = self._multiroom(numero)
                if escravo is None:
                    continue
                # Why: section 14, a slave out of the multiroom mode for two polls in a row
                # lost the group to a reboot of the master or to the application of the
                # manufacturer; keeping it in our books would route its volume through a
                # master that no longer commands it.
                # Por que: seção 14, um escravo fora do modo multiroom por dois polls seguidos
                # perdeu o grupo para um reboot do mestre ou para o aplicativo do fabricante;
                # mantê-lo nos nossos livros mandaria o volume dele por um mestre que não o
                # comanda mais.
                if escravo.driver.saiu_do_grupo():
                    log.warning("number %d left the group of number %d", numero, self._mestre)
                    escravo.driver.marcar_grupo(False)
                    continue
                restantes.append(numero)
            self._escravos = tuple(restantes)
            if not restantes:
                await self._desfazer()
                return
            self._espelhar(mestre)

    def _valores_ar(self) -> dict[int, object]:
        """The data points of a licence of air conditioners, read from the typed state.

        Os data points de uma licença de ares condicionados, lidos do estado tipado.
        """
        estados = self._gestor.estados()
        valores: dict[int, object] = {}
        online = []
        for numero in range(1, self.capacidade + 1):
            identidade = self.identidade(numero)
            estado = estados.get(identidade) if identidade else None
            # Why: a number nobody occupies publishes nothing at all, because a bridge that
            # read a false there would show an empty number as a machine that is switched off.
            # Por que: um número que ninguém ocupa não publica nada, porque uma ponte que
            # lesse um falso ali mostraria um número vazio como uma máquina desligada.
            if estado is None:
                continue
            if estado.online:
                online.append(numero)
            if estado.ligado is not None:
                valores[self._dp(F_LIGADO, numero)] = estado.ligado
            if type(estado.temperatura) is int and self._cabe(F_TEMPERATURA, estado.temperatura):
                valores[self._dp(F_TEMPERATURA, numero)] = estado.temperatura
            if estado.modo in MODOS_AR:
                valores[self._dp(F_MODO, numero)] = estado.modo
            if estado.vento in VENTOS:
                valores[self._dp(F_VENTO, numero)] = estado.vento
        valores[self._dp(F_ONLINE)] = mapa.bits(online)
        nomes = _nomes_json_encurtado(
            mapa.CHAVE_NOMES_MAQUINAS, self._nomes(), mapa.NUMEROS[mapa.PRODUTO_AR]
        )
        if nomes is not None:
            valores[self._dp(F_NOMES)] = nomes
        return valores

    def _valores_av(self) -> dict[int, object]:
        """The data points of a licence of audio and video, read from the typed state.

        Os data points de uma licença de áudio e vídeo, lidos do estado tipado.
        """
        estados = self._gestor.estados()
        cadastros = self._cadastros()
        valores: dict[int, object] = {}
        online: list[int] = []
        mudos: list[int] = []
        entradas: dict[int, int] = {}
        modos: dict[int, int] = {}
        titulos: dict[int, str] = {}
        for numero in range(1, self.capacidade + 1):
            identidade = self.identidade(numero)
            estado = estados.get(identidade) if identidade else None
            cadastro = cadastros.get(identidade)
            if estado is None or cadastro is None:
                continue
            if estado.online:
                online.append(numero)
            # Why: section 8, an always-on equipment (one whose manifest does not declare the
            # power pair) stays silent on its power data point instead of publishing a state
            # nobody can change.
            # Por que: seção 8, um equipamento always-on (cujo manifesto não declara o par de
            # energia) fica calado no data point de ligar em vez de publicar um estado que
            # ninguém consegue mudar.
            if estado.ligado is not None and self._com_energia(identidade):
                valores[self._dp(F_LIGADO, numero)] = estado.ligado
            if estado.volume is not None:
                valores[self._dp(F_NIVEL, numero)] = estado.volume
            if estado.mudo:
                mudos.append(numero)
            indice = _indice_de(cadastro, "entradas", estado.fonte)
            if indice:
                entradas[numero] = indice
            indice = _indice_de(cadastro, "modos", estado.modo)
            if indice:
                modos[numero] = indice
            if estado.tocando:
                titulos[numero] = estado.tocando
        valores[self._dp(F_GRUPO)] = self.grupo()
        valores[self._dp(F_ONLINE)] = mapa.bits(online)
        valores[self._dp(F_MUDOS)] = mapa.bits(mudos)
        valores[self._dp(F_ENTRADAS)] = mapa.pares(entradas)
        valores[self._dp(F_MODOS)] = mapa.pares(modos)
        valores[self._dp(F_TITULOS)] = mapa.titulos(titulos)
        try:
            partes = mapa.empacotar(self._perfis(self._ordem, cadastros, None))
        except mapa.NomesInvalidos as erro:
            # Why: the routes refuse a registration whose profiles do not pack, so this is a
            # config.json edited by hand; the strings stay off the bus instead of leaving cut.
            # Por que: as rotas recusam um cadastro cujos perfis não cabem, então isto é um
            # config.json editado na mão; as strings ficam fora do barramento em vez de sair
            # cortadas.
            log.error("licence %s cannot publish its profiles: %s", self.id, erro)
        else:
            for indice, parte in enumerate(partes, start=1):
                valores[self._dp(F_PERFIS, indice=indice)] = parte
        return valores

    def _perfis(
        self,
        ordem: tuple[str, ...],
        cadastros: Mapping[str, Cadastro],
        substituto: Cadastro | None,
    ) -> tuple[str, ...]:
        """The profile of every occupied number whose manifest is known, section 8.

        O perfil de todo número ocupado cujo manifesto se conhece, seção 8.
        """
        perfis = []
        for numero, identidade in enumerate(ordem, start=1):
            cadastro = cadastros.get(identidade) if identidade else None
            if substituto is not None and substituto.identidade == identidade:
                cadastro = substituto
            manifesto = self._manifesto(cadastro)
            if cadastro is None or manifesto is None:
                continue
            perfis.append(perfil.montar(numero, cadastro, manifesto))
        return tuple(perfis)

    def _perfis_cabem(
        self,
        ordem: tuple[str, ...],
        cadastros: Mapping[str, Cadastro],
        substituto: Cadastro | None,
    ) -> bool:
        try:
            mapa.empacotar(self._perfis(ordem, cadastros, substituto))
        except mapa.NomesInvalidos:
            return False
        return True

    def _manifesto(self, cadastro: Cadastro | None) -> Manifesto | None:
        # Why: the profile is judged for the registration as it WILL be, so the manifest is
        # the one of its tipo and never the one of the tipo the gestor still holds for it.
        # Por que: o perfil é julgado pelo cadastro como ele VAI ficar, então o manifesto é o
        # do tipo dele e nunca o do tipo que o gestor ainda guarda para ele.
        if cadastro is None:
            return None
        return self._gestor.manifesto_de_tipo(cadastro.tipo)

    def _nomes(self) -> list[str]:
        """The names of the numbers up to the last one somebody occupies.

        Os nomes dos números até o último que alguém ocupa.
        """
        cadastros = self._cadastros()
        ocupados = [numero for numero in range(1, self.capacidade + 1) if self.identidade(numero)]
        ultimo = max(ocupados) if ocupados else 0
        nomes = []
        for numero in range(1, ultimo + 1):
            cadastro = cadastros.get(self.identidade(numero))
            nomes.append(cadastro.nome if cadastro is not None and cadastro.nome else VAZIA)
        return nomes

    def _dp(self, funcao: str, numero: int = 0, indice: int = 0) -> int:
        return mapa.dp_de(self.produto, funcao, numero, indice)

    def _cabe(self, funcao: str, valor: int) -> bool:
        dp = mapa.de_dp(self.produto, self._dp(funcao, 1))
        return dp is not None and dp.minimo <= valor <= dp.maximo

    def _com_transporte(self, identidade: str) -> bool:
        """Section 8: the transport of a driver that declares both transport capabilities;
        half of the pair is no transport at all.

        Seção 8: o transporte de um driver que declara as duas capacidades de transporte;
        metade do par não é transporte nenhum.
        """
        return self._declara(identidade, ACAO_TOCAR, ACAO_PAUSAR)

    def _com_energia(self, identidade: str) -> bool:
        """Section 8: the power switch of a driver that declares both power capabilities; a
        switch that turns on and cannot turn off is a switch the customer cannot trust.

        Seção 8: a chave de ligar de um driver que declara as duas capacidades de energia;
        uma chave que liga e não desliga é uma chave em que o cliente não pode confiar.
        """
        return self._declara(identidade, ACAO_LIGAR, ACAO_DESLIGAR)

    def _declara(self, identidade: str, *acoes: str) -> bool:
        manifesto = self._gestor.manifesto(identidade)
        if manifesto is None:
            return False
        return all(acao in manifesto.capacidades for acao in acoes)

    def _companheiras(self, numero: int) -> tuple[int, ...]:
        """The numbers a group led by this one may hold: same tipo, and never a mixed one.

        Os números que um grupo liderado por este pode ter: mesmo tipo, e nunca um misto.
        """
        cadastros = self._cadastros()
        mestre = cadastros.get(self.identidade(numero))
        if mestre is None or not self._e_multiroom(mestre.identidade):
            return ()
        companheiras = []
        for outro in range(1, self.capacidade + 1):
            cadastro = cadastros.get(self.identidade(outro))
            # Why: section 14, a group only ever exists between speakers of the same domain,
            # so a speaker of another kind is never even invited; offering a mixed group is
            # what leaves half of it playing and the other half silent.
            # Por que: seção 14, um grupo só existe entre caixas do mesmo domínio, então uma
            # caixa de outro tipo nunca é convidada; oferecer grupo misto é o que deixa metade
            # dele tocando e a outra metade calada.
            if outro != numero and cadastro is not None and cadastro.tipo == mestre.tipo:
                companheiras.append(outro)
        return tuple(companheiras)

    async def _ativar(self, valor: object) -> str | None:
        if not self.multiroom:
            return NAO_SUPORTADO
        numero = int(valor) if type(valor) is int else -1
        if numero < 0 or numero > self.capacidade:
            return protocolo.VALOR_INVALIDO
        if numero == SOLO:
            return await self._desfazer()
        return await self._formar(numero)

    async def _grupo_por_nome(self, valor: object) -> str | None:
        """The group action of a scene: the master by identity, or the empty string for solo.

        A ação grupo de uma cena: o mestre pela identidade, ou a string vazia para solo.
        """
        if not self.multiroom:
            return NAO_SUPORTADO
        if valor == VAZIA or valor is None:
            return await self._desfazer()
        if not isinstance(valor, str):
            return protocolo.VALOR_INVALIDO
        numero = self.numero(valor)
        if not numero:
            return protocolo.VALOR_INVALIDO
        return await self._formar(numero)

    async def _formar(self, numero: int) -> str | None:
        """Forms the group led by one number: every speaker of its tipo joins that master.

        Forma o grupo liderado por um número: toda caixa do tipo dele entra naquele mestre.
        """
        mestre = self._multiroom(numero)
        if mestre is None:
            # Why: a number whose equipment cannot group answers the code of a capability the
            # manifest does not declare; offline is only for a number nothing answers for.
            # Por que: um número cujo equipamento não agrupa responde o código de uma
            # capacidade que o manifesto não declara; offline é só para um número por que
            # ninguém responde.
            return NAO_SUPORTADO if self._alvo(numero) is not None else protocolo.NUMERO_OFFLINE
        if not mestre.cadastro.ip:
            return protocolo.NUMERO_OFFLINE
        companheiras = self._companheiras(numero)
        if not companheiras:
            # Why: a group of one is not a group, and a bus that answered ok for it would
            # publish a group the customer cannot hear.
            # Por que: um grupo de um não é grupo, e um barramento que respondesse ok por ele
            # publicaria um grupo que o cliente não escuta.
            return NAO_SUPORTADO
        presentes = [alvo for alvo in map(self._multiroom, companheiras) if alvo is not None]
        if not presentes:
            return protocolo.NUMERO_OFFLINE
        if self._mestre and self._mestre != numero:
            codigo = await self._desfazer()
            if codigo is not None:
                # Why: the old master refused or did not answer, so its slaves are still
                # physically playing its audio; a second group formed over them would leave
                # the first one with nobody in the books to take it down.
                # Por que: o mestre antigo recusou ou não respondeu, então os escravos dele
                # ainda tocam fisicamente o áudio dele; um segundo grupo formado por cima
                # deixaria o primeiro sem ninguém nos livros para derrubá-lo.
                return codigo
        antigos = self._escravos
        # Why: every slave joins the master on its own, so the invitations go out together and
        # a licence of twelve numbers costs the slowest speaker instead of the sum; the lock of
        # the licence is held meanwhile, and the publish loop waits on it.
        # Por que: cada escravo entra no mestre por conta própria, então os convites saem juntos
        # e uma licença de doze números custa a caixa mais lenta em vez da soma; a trava da
        # licença fica presa enquanto isso, e o laço de publicação espera por ela.
        respostas = await asyncio.gather(
            *(self._chamar(alvo.driver.entrar_no_grupo(mestre.cadastro.ip)) for alvo in presentes)
        )
        entraram: list[int] = []
        recusa: str | None = None
        for alvo, codigo in zip(presentes, respostas, strict=True):
            if codigo is None:
                alvo.driver.marcar_grupo(True)
                entraram.append(alvo.numero)
            else:
                log.warning("number %d did not join the group of number %d", alvo.numero, numero)
                recusa = recusa or codigo
        for antigo in antigos:
            if antigo in entraram or antigo == numero:
                continue
            # Why: a member of the group being re-formed that did not answer the invitation is
            # still a slave when the speaker says so, and the books keep it; only a member
            # that really left has its mark cleared, so nothing is evicted for a lost reply.
            # Por que: um membro do grupo sendo refeito que não respondeu ao convite continua
            # escravo quando a caixa diz isso, e os livros o mantêm; só um membro que saiu de
            # verdade tem a marca limpa, então nada é expulso por uma resposta perdida.
            alvo = self._multiroom(antigo)
            if alvo is not None and alvo.driver.e_escravo():
                entraram.append(antigo)
            else:
                self._largar((antigo,))
        if not entraram:
            return recusa
        entraram.sort()
        mestre.driver.marcar_grupo(True)
        self._mestre = numero
        self._escravos = tuple(entraram)
        self._espelhar(mestre)
        return None

    async def _desfazer(self, *, forcar: bool = False) -> str | None:
        """Dismantles the group from the MASTER, which is the only speaker that may do it.

        Why: forgetting the group when the master refused the command, or did not answer in
        time, tells the customer the speakers are apart while they are still playing together,
        and the retry then finds no group in the books and answers ok without touching the
        wire. The books are only cleared when the physical move landed. forcar is for the
        equipment that is leaving the installation anyway, where there is nothing left to
        retry with.

        Desfaz o grupo pelo MESTRE, que é a única caixa que pode fazer isso.

        Por que: esquecer o grupo quando o mestre recusou o comando, ou não respondeu a tempo,
        diz ao cliente que as caixas estão separadas enquanto elas seguem tocando juntas, e a
        repetição então não acha grupo nos livros e responde ok sem tocar no fio. Os livros só
        são limpos quando o movimento físico aconteceu. O forcar é para o equipamento que está
        saindo da instalação de todo jeito, onde não sobrou com o que repetir.
        """
        if not self._mestre:
            return None
        mestre = self._multiroom(self._mestre)
        codigo = None if mestre is None else await self._chamar(mestre.driver.desfazer_grupo())
        if codigo is not None and not forcar:
            log.warning("number %d refused to dismantle its group: %s", self._mestre, codigo)
            return codigo
        self._soltar()
        return codigo

    def _largar(self, numeros: Iterable[int]) -> None:
        """Takes numbers out of the group in our books, clearing the mark on each speaker.

        Why: a number dropped from the books while its driver still believes it is in a group
        refuses transport and input forever, for a group nobody is in any more.

        Tira números do grupo nos nossos livros, limpando a marca em cada caixa.

        Por que: um número largado dos livros com o driver dele ainda achando que está num
        grupo recusa transporte e entrada para sempre, por um grupo em que ninguém mais está.
        """
        for numero in numeros:
            alvo = self._multiroom(numero)
            if alvo is not None:
                alvo.driver.marcar_grupo(False)

    def _soltar(self) -> None:
        """Forgets the group in our books and clears the mark on every speaker of it.

        Esquece o grupo nos nossos livros e limpa a marca em toda caixa dele.
        """
        self._largar((self._mestre, *self._escravos))
        self._mestre = SOLO
        self._escravos = ()

    async def _conferir_membros(self, nova: tuple[str, ...]) -> None:
        """A group whose master or whose last slave leaves the order is not a group any more,
        and it is taken down while the CURRENT order can still reach the master.

        Why: the books are kept by IDENTITY and never by position, because any registered
        equipment may take a number now; a projector put in the number of a slave would
        inherit its role and receive, as the slave, the volume meant for a speaker.

        Um grupo cujo mestre ou cujo último escravo sai da ordem deixou de ser grupo, e ele é
        derrubado enquanto a ordem ATUAL ainda alcança o mestre.

        Por que: os livros são mantidos por IDENTIDADE e nunca por posição, porque qualquer
        equipamento cadastrado pode ocupar um número agora; um projetor posto no número de um
        escravo herdaria o papel dele e receberia, como escravo, o volume de uma caixa.
        """
        if not self._mestre:
            return
        if _identidade_em(nova, self._mestre) != self.identidade(self._mestre):
            await self._desfazer(forcar=True)
            return
        ficam = tuple(
            numero
            for numero in self._escravos
            if _identidade_em(nova, numero) == self.identidade(numero)
        )
        self._largar(numero for numero in self._escravos if numero not in ficam)
        self._escravos = ficam
        if not self._escravos:
            await self._desfazer(forcar=True)

    def _espelhar(self, mestre: _Alvo) -> None:
        """Section 14: a slave answers stop even while it plays, so it reads what the master
        plays and never what it says about itself.

        Seção 14: um escravo responde stop mesmo tocando, então ele lê o que o mestre toca e
        nunca o que ele diz de si.
        """
        estado = self._estado(mestre.cadastro.identidade)
        tocando = None if estado is None else estado.tocando
        reproduzindo = None if estado is None else estado.reproduzindo
        for numero in self._escravos:
            alvo = self._multiroom(numero)
            if alvo is not None:
                alvo.driver.espelhar(tocando, reproduzindo)

    async def _recuperar_alheios(self) -> None:
        """Asks a speaker held in someone else's group to leave it, and says so when it stays.

        Why: a speaker in that mode refuses volume, transport, preset and input, so leaving it
        there is leaving the number dead. Section 14 records Ungroup as a command of the
        master, so it is not certain a slave obeys it, and the honest behaviour when it does
        not is to keep the number flagged instead of publishing it as an ordinary number.

        Pede a uma caixa presa no grupo de outro que saia dele, e diz quando ela fica.

        Por que: uma caixa nesse modo recusa volume, transporte, preset e entrada, então
        deixá-la ali é deixar o número morto. A seção 14 registra o Ungroup como comando do
        mestre, então não é certo que um escravo obedeça, e o comportamento honesto quando ele
        não obedece é manter o número sinalizado em vez de publicá-lo como número comum.
        """
        agora = time.monotonic()
        for numero in self.escravos_alheios():
            alvo = self._multiroom(numero)
            if alvo is None:
                continue
            if agora - self._pedidos_de_saida.get(numero, -ESPERA_DE_SAIDA_S) < ESPERA_DE_SAIDA_S:
                continue
            self._pedidos_de_saida[numero] = agora
            log.warning(
                "number %d is a multiroom slave of a group this hub does not lead, "
                "asking it to leave",
                numero,
            )
            codigo = await self._chamar(alvo.driver.desfazer_grupo())
            if codigo is not None:
                log.warning(
                    "number %d would not leave the group it is held in: %s, so it refuses "
                    "every command until it does",
                    numero,
                    codigo,
                )

    async def _no_numero(self, dp: mapa.Dp, valor: object) -> str | None:
        """A set on a data point of one number, as the capability of section 6 it is.

        Um set num data point de um número, como a capacidade da seção 6 que ele é.
        """
        alvo = self._alvo(dp.numero)
        if alvo is None:
            return protocolo.NUMERO_OFFLINE
        identidade = alvo.cadastro.identidade
        if dp.funcao == F_LIGADO:
            if not self._com_energia(identidade):
                return NAO_SUPORTADO
            return await self._executar(identidade, ACAO_LIGAR if valor else ACAO_DESLIGAR, None)
        if dp.funcao == F_NIVEL:
            return await self._volume(alvo, valor)
        if dp.funcao == F_TEMPERATURA:
            return await self._executar(identidade, ACAO_TEMPERATURA, valor)
        if dp.funcao == F_MODO:
            return await self._executar(identidade, ACAO_MODO, valor)
        if dp.funcao == F_VENTO:
            return await self._executar(identidade, ACAO_VENTO, valor)
        return protocolo.DP_DESCONHECIDO

    async def _comando(self, valor: object) -> str | None:
        """One string of the command channel, section 8, as one capability on one equipment.

        Uma string do canal de comando, seção 8, como uma capacidade num equipamento.
        """
        lido = comando.ler(valor, self.capacidade)
        if lido is None:
            return protocolo.VALOR_INVALIDO
        alvo = self._alvo(lido.numero)
        if alvo is None:
            return protocolo.NUMERO_OFFLINE
        identidade = alvo.cadastro.identidade
        if lido.acao in comando.COM_INDICE:
            itens = perfil.itens(alvo.cadastro, comando.COM_INDICE[lido.acao])
            if not 1 <= lido.indice <= len(itens):
                return protocolo.VALOR_INVALIDO
            escolhido = itens[lido.indice - 1].valor
            if lido.capacidade in DO_MESTRE:
                return await self._transporte(alvo, lido.capacidade, escolhido)
            return await self._executar(identidade, lido.capacidade, escolhido)
        if lido.acao == comando.ACAO_MUDO:
            # Why: section 8, the mute of the channel toggles, because the panel has one
            # button and the state comes back by the report of the muted bits.
            # Por que: seção 8, o mudo do canal alterna, porque o painel tem um botão só e o
            # estado volta pelo report dos bits de mudo.
            estado = self._estado(identidade)
            return await self._executar(identidade, ACAO_MUDO, not (estado and estado.mudo))
        if lido.capacidade in DO_MESTRE:
            return await self._transporte(alvo, lido.capacidade, lido.valor)
        return await self._executar(identidade, lido.capacidade, lido.valor)

    async def _volume(self, alvo: _Alvo, valor: object) -> str | None:
        """Section 14: the volume of a slave goes through the master, never to the slave.

        Seção 14: o volume de um escravo passa pelo mestre, nunca vai para o escravo.
        """
        mestre = self._mestre_de(alvo.numero)
        if mestre is None:
            return await self._executar(alvo.cadastro.identidade, ACAO_VOLUME, valor)
        return await self._chamar(mestre.driver.volume_de_escravo(alvo.cadastro.ip, valor))

    async def _transporte(self, alvo: _Alvo, acao: str, valor: object) -> str | None:
        """Section 14: a play, a radio or a preset on a slave dismantles the group, so what
        starts audio goes to the master.

        Seção 14: um play, uma rádio ou um preset num escravo desmonta o grupo, então o que
        inicia áudio vai para o mestre.
        """
        mestre = self._mestre_de(alvo.numero)
        destino = alvo if mestre is None else mestre
        return await self._executar(destino.cadastro.identidade, acao, valor)

    def _mestre_de(self, numero: int) -> _Alvo | None:
        """The master of a number that is a slave right now, or None when it answers for
        itself.

        O mestre de um número que é escravo agora, ou None quando ele responde por si.
        """
        if numero not in self._escravos:
            return None
        return self._multiroom(self._mestre)

    async def _executar(self, identidade: str, acao: str, valor: object) -> str | None:
        return traduzir(await self._gestor.executar(identidade, acao, valor))

    async def _chamar(self, chamada: Awaitable[str | None]) -> str | None:
        """One group move straight into a driver, with the deadline and with no exception out.

        Um movimento de grupo direto no driver, com prazo e sem exceção saindo.
        """
        try:
            async with asyncio.timeout(self._limite_s):
                return traduzir(await chamada)
        except TimeoutError:
            # Why: the same as the gestor, a speaker that did not answer within the deadline is
            # offline, and not a fault of the device nor a traceback; the deadline fires while
            # the call waits for the lock a poll of the same unreachable master holds.
            # Por que: o mesmo que o gestor, uma caixa que não respondeu dentro do prazo está
            # offline, e não é falha do aparelho nem traceback; o prazo dispara enquanto a
            # chamada espera a trava que um poll do mesmo mestre inalcançável segura.
            log.warning("a group move did not finish within %.1f s", self._limite_s)
            return protocolo.NUMERO_OFFLINE
        except Exception as erro:
            log.exception("a group move failed: %s", erro or type(erro).__name__)
            return ERRO_APARELHO

    async def _ler(self, chamada: Awaitable[object]) -> object:
        try:
            async with asyncio.timeout(self._limite_s):
                return await chamada
        except Exception as erro:
            log.warning("a speaker did not answer its group: %s", erro or type(erro).__name__)
            return None

    def _cadastros(self) -> dict[str, Cadastro]:
        return {cadastro.identidade: cadastro for cadastro in self._gestor.cadastros}

    def _estado(self, identidade: str) -> Estado | None:
        if not identidade:
            return None
        return self._gestor.estados().get(identidade)

    def _e_multiroom(self, identidade: str) -> bool:
        """Section 6: what the manifest declares decides, and no second table is consulted.

        Seção 6: o que o manifesto declara decide, e nenhuma segunda tabela é consultada.
        """
        manifesto = self._gestor.manifesto(identidade)
        if manifesto is None:
            return False
        return (
            manifesto.categoria == CATEGORIA_DE_GRUPO
            and CAPACIDADE_DE_GRUPO in manifesto.capacidades
        )

    def _alvo(self, numero: int) -> _Alvo | None:
        """The number as something that can be commanded, or None when nothing answers for
        it.

        O número como algo que se pode comandar, ou None quando nada responde por ele.
        """
        identidade = self.identidade(numero)
        cadastro = self._cadastros().get(identidade)
        driver = self._gestor.driver(identidade) if identidade else None
        if cadastro is None or driver is None:
            # Why: an identity that is not registered any more is an empty number and not an
            # error of the bus, because the file may have been edited by hand.
            # Por que: uma identidade que não está mais cadastrada é um número vazio e não um
            # erro do barramento, porque o arquivo pode ter sido editado na mão.
            return None
        return _Alvo(numero=numero, cadastro=cadastro, driver=driver)

    def _multiroom(self, numero: int) -> _Alvo | None:
        """The number only when a group can really be made of what is mounted for it.

        O número só quando um grupo pode mesmo ser feito do que está montado nele.
        """
        alvo = self._alvo(numero)
        if alvo is None or not self._e_multiroom(alvo.cadastro.identidade):
            return None
        if not all(hasattr(alvo.driver, movimento) for movimento in MOVIMENTOS):
            log.error("driver of number %d declares agrupar and offers no group move", numero)
            return None
        return alvo

    def _multirooms(self) -> tuple[_Alvo, ...]:
        alvos = (self._multiroom(numero) for numero in range(1, self.capacidade + 1))
        return tuple(alvo for alvo in alvos if alvo is not None)


class Licencas:
    """Every licence of the installation, each with its numbers, as one book.

    Todas as licenças da instalação, cada uma com os números dela, num livro só.
    """

    def __init__(
        self,
        gestor: Gestor,
        licencas: Iterable[Licenca] = (),
        numeros: Mapping[str, Sequence[str]] | None = None,
        *,
        limite_s: float = LIMITE_S,
    ) -> None:
        self._gestor = gestor
        self._limite_s = limite_s
        self._por_id: dict[str, Numeros] = {}
        ordens = numeros or {}
        for licenca in licencas:
            self._por_id[licenca.id] = Numeros(
                gestor,
                licenca,
                self._confiavel(licenca, ordens.get(licenca.id, ())),
                limite_s=limite_s,
            )

    def _confiavel(self, licenca: Licenca, ordem: Sequence[str]) -> tuple[str, ...]:
        """The saved order with every number this module refuses left empty.

        Why: the route validates an order and config.json does not, so an order edited by
        hand boots a hub whose numbers name an identity that is not registered at all, or the
        same one twice, or an equipment of the other product. A number it refuses is left
        empty instead of publishing a number nothing can command.

        A ordem salva com todo número que este módulo recusa deixado vazio.

        Por que: a rota valida uma ordem e o config.json não, então uma ordem editada na mão
        sobe um hub cujos números nomeiam uma identidade que nem está cadastrada, ou a mesma
        duas vezes, ou um equipamento do outro produto. Um número que ele recusa fica vazio em
        vez de publicar um número que ninguém comanda.
        """
        juiz = Numeros(self._gestor, licenca, limite_s=self._limite_s)
        aceitos: list[str] = []
        for identidade in tuple(ordem)[: juiz.capacidade]:
            try:
                juiz.validar([*aceitos, identidade], self._alheias(licenca.id))
            except OrdemInvalida as erro:
                log.warning(
                    "number %d of licence %s was dropped: %s", len(aceitos) + 1, licenca.id, erro
                )
                aceitos.append(VAZIA)
            else:
                aceitos.append(identidade)
        return tuple(aceitos)

    def _alheias(self, id_licenca: str) -> set[str]:
        """The identities occupying a number of any OTHER licence.

        As identidades que ocupam número de qualquer OUTRA licença.
        """
        return {
            identidade
            for outra in self._por_id.values()
            if outra.id != id_licenca
            for identidade in outra.ocupadas()
        }

    def ids(self) -> tuple[str, ...]:
        return tuple(self._por_id)

    def todas(self) -> tuple[Numeros, ...]:
        return tuple(self._por_id.values())

    def de(self, id_licenca: object) -> Numeros | None:
        if not isinstance(id_licenca, str):
            return None
        return self._por_id.get(id_licenca)

    def produto_de(self, id_licenca: object) -> str | None:
        numeros = self.de(id_licenca)
        return None if numeros is None else numeros.produto

    def onde(self, identidade: str) -> tuple[str, int] | None:
        """The licence and the number one equipment occupies, or None for one that occupies
        none.

        A licença e o número que um equipamento ocupa, ou None para um que não ocupa nenhum.
        """
        for numeros in self._por_id.values():
            numero = numeros.numero(identidade)
            if numero:
                return numeros.id, numero
        return None

    def numeros(self) -> dict[str, tuple[str, ...]]:
        """The order of every licence, which is what config.json persists.

        A ordem de toda licença, que é o que o config.json guarda.
        """
        return {id_licenca: numeros.ordem for id_licenca, numeros in self._por_id.items()}

    def adicionar(self, licenca: Licenca) -> Numeros:
        if licenca.id in self._por_id:
            raise ValueError(f"licence {licenca.id!r} already exists")
        numeros = Numeros(self._gestor, licenca, limite_s=self._limite_s)
        self._por_id[licenca.id] = numeros
        return numeros

    def trocar(self, licenca: Licenca) -> None:
        numeros = self._por_id[licenca.id]
        numeros.trocar_licenca(licenca)

    async def remover(self, id_licenca: str) -> None:
        """Takes the licence out of the book after its group fell; the equipment stays.

        Tira a licença do livro depois de o grupo dela cair; o equipamento fica.
        """
        numeros = self._por_id.get(id_licenca)
        if numeros is None:
            return
        await numeros.desligar()
        del self._por_id[id_licenca]

    def validar_ordem(self, id_licenca: str, ordem: object) -> tuple[str, ...]:
        numeros = self._por_id[id_licenca]
        return numeros.validar(ordem, self._alheias(id_licenca))

    async def definir_ordem(self, id_licenca: str, ordem: object) -> tuple[str, ...]:
        numeros = self._por_id[id_licenca]
        return await numeros.definir_ordem(ordem, self._alheias(id_licenca))

    async def esquecer(self, identidade: str) -> dict[str, tuple[str, ...]]:
        """The number of a removed equipment stays empty in whichever licence held it.

        O número de um equipamento removido fica vazio na licença que o segurava.
        """
        # Why: a licence removed while a master takes its deadline would change the book under
        # this loop, so it walks a copy.
        # Por que: uma licença removida enquanto um mestre gasta o prazo dele mudaria o livro
        # debaixo deste laço, então ele percorre uma cópia.
        for numeros in self.todas():
            if numeros.numero(identidade):
                await numeros.esquecer(identidade)
        return self.numeros()

    def segue_um_mestre(self, identidade: str) -> bool:
        """Whether the licence that holds this equipment has it following a master right now.

        Se a licença que segura este equipamento o tem seguindo um mestre agora.
        """
        onde = self.onde(identidade)
        return onde is not None and self._por_id[onde[0]].segue_um_mestre(identidade)

    def perfis_cabem(self, substituto: Cadastro) -> bool:
        """True when an edited registration still packs in the licence that holds it.

        Verdadeiro quando um cadastro editado ainda cabe na licença que o segura.
        """
        onde = self.onde(substituto.identidade)
        if onde is None:
            return True
        return self._por_id[onde[0]].perfis_cabem(substituto)

    def valores(self, id_licenca: str) -> dict[int, object]:
        numeros = self._por_id.get(id_licenca)
        return {} if numeros is None else numeros.valores()

    async def aplicar(self, id_licenca: object, dpid: object, valor: object) -> str | None:
        numeros = self.de(id_licenca)
        if numeros is None:
            return protocolo.LICENCA_DESCONHECIDA
        return await numeros.aplicar(dpid, valor)

    async def acionar(self, identidade: str, acao: str, valor: object) -> str | None:
        """One action of a scene: through the licence that holds the equipment when one does,
        straight to the gestor when none does.

        Uma ação de cena: pela licença que segura o equipamento quando alguma segura, direto
        no gestor quando nenhuma segura.
        """
        onde = self.onde(identidade)
        if onde is not None:
            return await self._por_id[onde[0]].acionar(identidade, acao, valor)
        if acao == ACAO_GRUPO:
            return NAO_SUPORTADO
        return traduzir(await self._gestor.executar(identidade, acao, valor))

    async def reler(self, id_licenca: object, dpid: object, valor: object = None) -> None:
        numeros = self.de(id_licenca)
        if numeros is not None:
            await numeros.reler(dpid, valor)

    async def sanear(self) -> None:
        await asyncio.gather(*(numeros.sanear() for numeros in self.todas()))

    async def sincronizar(self) -> None:
        for numeros in self.todas():
            await numeros.sincronizar()


def traduzir(codigo: str | None) -> str | None:
    """A code of section 6 in the vocabulary the bus of section 8 speaks.

    Um código da seção 6 no vocabulário que o barramento da seção 8 fala.
    """
    if codigo is None:
        return None
    if codigo in (EQ_OFFLINE, EQ_NAO_ENCONTRADO):
        # Why: on the bus an equipment that did not answer and a number whose equipment is
        # gone are the same thing to the bridge, which asked a number and got no number.
        # Por que: no barramento um equipamento que não respondeu e um número cujo equipamento
        # sumiu são a mesma coisa para a ponte, que perguntou por um número e não achou número.
        return protocolo.NUMERO_OFFLINE
    if codigo == INVALID_VALUE:
        return protocolo.VALOR_INVALIDO
    if codigo in CODIGOS:
        return codigo
    log.error("a driver answered %r, outside the vocabulary of the bus", codigo)
    return ERRO_APARELHO


def _indice_de(cadastro: Cadastro, lista: str, valor: str | None) -> int:
    """The 1-based position of a driver value in a list of the registration, 0 for none.

    A posição a partir de 1 de um valor do driver numa lista do cadastro, 0 para nenhuma.
    """
    if not valor:
        return 0
    for indice, item in enumerate(perfil.itens(cadastro, lista), start=1):
        if item.valor == valor:
            return indice
    return 0


def _nomes_json_encurtado(chave: str, nomes: Sequence[str], limite: int) -> str | None:
    """The names of a string DP inside its 255 bytes, shortened only when they do not fit.

    Os nomes de um DP string dentro dos 255 bytes dele, encurtados só quando não couberem.
    """
    try:
        return mapa.nomes_json(chave, nomes, limite)
    except mapa.NomesInvalidos:
        pass
    # Why: the names of the machines are the names of the equipment, which the registration
    # takes long and in any alphabet; refusing the whole DP would take the names of EIGHT
    # machines off the bus because one of them is long, so each name is shortened to its fair
    # share of the budget instead, on a character boundary, and the JSON always reaches the
    # bridge whole. The budget is squeezed until the encoded JSON really fits, because json
    # escapes a quote as \" and a backslash as \\, and a budget measured in raw bytes lies.
    # Por que: os nomes das máquinas são os nomes dos equipamentos, que o cadastro aceita
    # longos e em qualquer alfabeto; recusar o DP inteiro tiraria do barramento os nomes de
    # OITO máquinas porque um deles é longo, então cada nome é encurtado para a parte justa do
    # orçamento, em fronteira de caractere, e o JSON sempre chega inteiro à ponte. O orçamento
    # é apertado até o JSON codificado caber de verdade, porque o json escapa uma aspa como \"
    # e uma barra como \\, e um orçamento medido em bytes crus mente.
    try:
        moldura = len(mapa.nomes_json(chave, [VAZIA] * len(nomes), limite).encode("utf-8"))
    except mapa.NomesInvalidos:
        log.error("the names of the machines have no room for even the empty names")
        return None
    orcamento = (mapa.TEXTO_MAXIMO_BYTES - moldura) // max(len(nomes), 1)
    while orcamento > 0:
        try:
            return mapa.nomes_json(chave, [_encurtar(nome, orcamento) for nome in nomes], limite)
        except mapa.NomesInvalidos:
            orcamento -= 1
    log.error("the names of the machines do not fit even shortened")
    return None


def _encurtar(nome: str, orcamento: int) -> str:
    """The name inside a byte budget, never cut inside a character.

    O nome dentro de um orçamento de bytes, nunca cortado dentro de um caractere.
    """
    if orcamento <= 0:
        return VAZIA
    bruto = nome.encode("utf-8", errors="ignore")
    if len(bruto) <= orcamento:
        return bruto.decode("utf-8")
    return bruto[:orcamento].decode("utf-8", errors="ignore")

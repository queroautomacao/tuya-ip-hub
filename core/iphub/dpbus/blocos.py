# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 6, 8 and 14: the six blocks, the data points they publish and the group.

A block is only what section 6 says it is, one of the six equipment numbers of the app, which
any registered equipment may occupy, so there is no second registry here: the blocks are an
ORDER over identities already registered as equipment, the position IS the block, and an
empty string is a block nobody occupies. Removing an equipment empties its slot instead of
shifting the rest, because a shift moves a speaker from block 2 to block 1 in every automation
the customer already built on the platform, and nothing on the bus would say it happened.
What DP 102 of a block means follows the manifest of its driver: play/pause when it declares
transport, power for everything else (section 8).

The group logic the LinkPlay driver deliberately did not take lives here, and every rule of
it was paid for on the bench (section 14):

- a group is formed by naming a MASTER, and only speakers of the same tipo are invited: a
  mixed group is never offered, so a speaker of another kind is not even asked to join;
- a play on a slave dismantles the group, so the transport of a block that is a slave is
  routed to the master;
- the volume of a slave goes through the master, never to the slave itself;
- a slave answers stop even while the group plays, so what the master plays is mirrored onto
  every slave and read back from the state of the slave;
- a slave that left the multiroom mode for two polls in a row lost its group to a reboot or
  to the application of the manufacturer, and the logical state is reconciled;
- a zombie group of a previous run, or of an address that changed under us, is sanitized on
  boot before anything is published;
- forming, sanitizing and reconciling race each other, so ONE lock serializes all of it, and
  a command of a block takes the same lock because the routing decision it makes (is this
  block a slave, and who leads it) has to be the same one the group logic is holding.

grupoN of DP 132 is the group led by the speaker of BLOCK N, which is what keeps the enum
stable: a group defined as an entry in a list would renumber itself the day a block is added,
which is the same silent move the empty slot exists to prevent. Multiroom is a capability of
the equipment (section 6), so only a block whose manifest declares it can lead or join.

Seções 6, 8 e 14: os seis blocos, os data points que eles publicam e o grupo.

Um bloco é só o que a seção 6 diz que ele é, um dos seis números de equipamento do app, que
qualquer equipamento cadastrado pode ocupar, então não existe segundo cadastro aqui: os
blocos são uma ORDEM sobre identidades já cadastradas como equipamento, a posição É o bloco,
e uma string vazia é um bloco que ninguém ocupa. Remover um equipamento esvazia a vaga dele em
vez de empurrar o resto, porque empurrar move uma caixa do bloco 2 para o bloco 1 em toda
automação que o cliente já montou na plataforma, e nada no barramento diria que isso
aconteceu. O que o DP 102 de um bloco significa segue o manifesto do driver dele: play/pause
quando ele declara transporte, ligar/desligar para todo o resto (seção 8).

A lógica de grupo que o driver LinkPlay de propósito não tomou mora aqui, e cada regra dela
foi paga na bancada (seção 14):

- um grupo é formado nomeando um MESTRE, e só caixas do mesmo tipo são convidadas: um grupo
  misto nunca é oferecido, então uma caixa de outro tipo nem chega a ser chamada;
- um play num escravo desmonta o grupo, então o transporte de um bloco que é escravo vai para
  o mestre;
- o volume de um escravo passa pelo mestre, nunca pelo próprio escravo;
- um escravo responde stop mesmo com o grupo tocando, então o que o mestre toca é espelhado
  em todo escravo e lido de volta no estado do escravo;
- um escravo que saiu do modo multiroom por dois polls seguidos perdeu o grupo para um reboot
  ou para o aplicativo do fabricante, e o estado lógico é reconciliado;
- um grupo zumbi de uma execução anterior, ou de um endereço que mudou por baixo, é saneado
  no boot antes de qualquer publicação;
- formar, sanear e reconciliar correm uns sobre os outros, então UMA trava serializa tudo, e
  um comando de bloco toma a mesma trava porque a decisão de rota que ele faz (este bloco é
  escravo, e quem o lidera) precisa ser a mesma que a lógica de grupo está segurando.

O grupoN do DP 132 é o grupo liderado pela caixa do BLOCO N, que é o que mantém o enum
estável: um grupo definido como entrada de uma lista se renumeraria no dia em que um bloco
fosse acrescentado, que é a mesma mudança silenciosa que a vaga vazia existe para impedir.
Multiroom é capacidade do equipamento (seção 6), então só um bloco cujo manifesto a declara
pode liderar ou entrar num grupo.
"""

import asyncio
import logging
from collections.abc import Awaitable, Iterable, Sequence
from dataclasses import dataclass

from iphub.config import Cadastro
from iphub.dpbus import mapa, protocolo
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.gestor import EQ_NAO_ENCONTRADO, EQ_OFFLINE, ERRO_APARELHO, Gestor
from iphub.drivers.manifesto import CAPACIDADE_DE_GRUPO, CATEGORIA_DE_GRUPO, Estado

log = logging.getLogger("iphub.dpbus.blocos")

VAZIA = ""
SOLO = mapa.SOLO
PREFIXO_GRUPO = "grupo"
PREFIXO_PRESET = "cmd"

# Why: the lock is held while a speaker answers, so the deadline of one call into a driver is
# what keeps a box that accepted the connection and went quiet from freezing the group of the
# whole installation.
# Por que: a trava fica presa enquanto uma caixa responde, então o prazo de uma chamada para
# dentro de um driver é o que impede uma caixa que aceitou a conexão e emudeceu de congelar o
# grupo da instalação inteira.
LIMITE_S = 5.0

# The actions of section 6 a data point of a block turns into.
# As ações da seção 6 em que um data point de bloco se transforma.
ACAO_LIGAR = "ligar"
ACAO_DESLIGAR = "desligar"
ACAO_VOLUME = "volume"
ACAO_FONTE = "fonte"
ACAO_TOCAR = "tocar"
ACAO_PAUSAR = "pausar"
ACAO_PRESET = "comando_extra"

# The stable codes an order refuses with; the panel translates them, section 11.
# Os códigos estáveis com que uma ordem recusa; o painel os traduz, seção 11.
BLOCOS_DEMAIS = "blocos_demais"
BLOCO_REPETIDO = "bloco_repetido"
IDENTIDADE_INVALIDA = "identidade_invalida"
CODIGOS_DE_ORDEM = (
    BLOCOS_DEMAIS,
    BLOCO_REPETIDO,
    EQ_NAO_ENCONTRADO,
    IDENTIDADE_INVALIDA,
)

# Everything aplicar may answer, and nothing else: the bus vocabulary of section 8 plus the
# two codes of section 6 that say the speaker itself refused.
# Tudo que o aplicar pode responder, e nada mais: o vocabulário de barramento da seção 8 mais
# os dois códigos da seção 6 que dizem que a própria caixa recusou.
CODIGOS = (
    protocolo.DP_DESCONHECIDO,
    protocolo.DP_SOMENTE_LEITURA,
    protocolo.VALOR_INVALIDO,
    protocolo.BLOCO_OFFLINE,
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
    """One filled block: the block, its registration and the driver mounted for it.

    Um bloco ocupado: o bloco, o cadastro dele e o driver montado para ele.
    """

    bloco: int
    cadastro: Cadastro
    driver: Driver


def valor_do_grupo(bloco: int) -> str:
    """The DP 132 value of the group led by one block, or solo for no group at all.

    O valor do DP 132 do grupo liderado por um bloco, ou solo para grupo nenhum.
    """
    return SOLO if bloco == 0 else f"{PREFIXO_GRUPO}{bloco}"


def bloco_do_grupo(valor: object) -> int | None:
    """The block leading the group named on the wire, 0 for solo, None for anything else.

    O bloco que lidera o grupo nomeado no fio, 0 para solo, None para qualquer outra coisa.
    """
    if valor == SOLO:
        return 0
    for bloco in range(1, mapa.BLOCOS + 1):
        if valor == valor_do_grupo(bloco):
            return bloco
    return None


def sem(ordem: Sequence[str], identidade: str) -> tuple[str, ...]:
    """The same order with that identity gone and its BLOCK still there, empty.

    A mesma ordem sem aquela identidade e com o BLOCO dela ainda ali, vazio.
    """
    # Why: section 8 numbers the block by position, so closing the hole would move every
    # speaker below it one block up, in silence, on a bus a customer already automated.
    # Por que: a seção 8 numera o bloco pela posição, então fechar o buraco moveria toda caixa
    # abaixo dele um bloco para cima, em silêncio, num barramento que um cliente já automatizou.
    return tuple(VAZIA if atual == identidade else atual for atual in ordem)


def _identidade_em(ordem: tuple[str, ...], bloco: int) -> str:
    """The identity a given order puts in one block, which is how a change is judged before
    it is written.

    A identidade que uma dada ordem põe num bloco, que é como uma mudança é julgada antes de
    ser gravada.
    """
    if not 1 <= bloco <= mapa.BLOCOS or bloco > len(ordem):
        return VAZIA
    return ordem[bloco - 1]


class Blocos:
    """The order of the blocks, the data points they publish and the group they may form.

    A ordem dos blocos, os data points que eles publicam e o grupo que eles podem formar.
    """

    def __init__(
        self, gestor: Gestor, ordem: Iterable[str] = (), *, limite_s: float = LIMITE_S
    ) -> None:
        self._gestor = gestor
        self._ordem = tuple(ordem)
        self._limite_s = limite_s
        # Why: forming a group, sanitizing a zombie one on boot and reconciling one that
        # dissolved by itself all rewrite who leads whom, and the bench showed them landing on
        # top of each other; a command of a block reads the same book to decide its route.
        # Por que: formar um grupo, sanear um zumbi no boot e reconciliar um que se desfez
        # sozinho reescrevem todos quem lidera quem, e a bancada os viu caindo um sobre o
        # outro; um comando de bloco lê o mesmo livro para decidir a rota dele.
        self._trava = asyncio.Lock()
        self._mestre = 0
        self._escravos: tuple[int, ...] = ()

    @property
    def ordem(self) -> tuple[str, ...]:
        return self._ordem

    def identidade(self, bloco: int) -> str:
        """The identity occupying one block, or the empty string when nobody occupies it.

        A identidade que ocupa um bloco, ou a string vazia quando ninguém o ocupa.
        """
        if not 1 <= bloco <= mapa.BLOCOS or bloco > len(self._ordem):
            return VAZIA
        return self._ordem[bloco - 1]

    def bloco(self, identidade: str) -> int:
        """The block one identity occupies, or 0 for an identity that occupies none.

        O bloco que uma identidade ocupa, ou 0 para uma identidade que não ocupa nenhum.
        """
        if not identidade:
            return 0
        for posicao, atual in enumerate(self._ordem, start=1):
            if atual == identidade:
                return posicao
        return 0

    def grupo(self) -> str:
        """The DP 132 value of the group that is active right now.

        O valor do DP 132 do grupo ativo agora.
        """
        return valor_do_grupo(self._mestre)

    def escravos(self) -> tuple[int, ...]:
        return self._escravos

    def validar(self, ordem: object) -> tuple[str, ...]:
        """The order as it would be saved, or OrdemInvalida with the code that refused it.

        A ordem como ela seria salva, ou OrdemInvalida com o código que a recusou.
        """
        if not isinstance(ordem, list | tuple):
            raise OrdemInvalida(IDENTIDADE_INVALIDA, f"the order is a list, found {ordem!r}")
        lista: list[str] = []
        for bruto in ordem:
            if not isinstance(bruto, str):
                raise OrdemInvalida(IDENTIDADE_INVALIDA, f"an identity is text, found {bruto!r}")
            lista.append(bruto.strip())
        if len(lista) > mapa.BLOCOS:
            raise OrdemInvalida(
                BLOCOS_DEMAIS, f"section 8 numbers {mapa.BLOCOS} blocks, found {len(lista)}"
            )
        ocupadas = [identidade for identidade in lista if identidade]
        repetidas = sorted({i for i in ocupadas if ocupadas.count(i) > 1})
        if repetidas:
            # Why: one speaker in two blocks answers the volume of two blocks on the bus, and
            # the bridge reads a device that contradicts itself.
            # Por que: uma caixa em dois blocos responde o volume de dois blocos no barramento,
            # e a ponte lê um aparelho que se contradiz.
            raise OrdemInvalida(BLOCO_REPETIDO, f"the identidade {repetidas} occupies two blocks")
        cadastros = self._cadastros()
        for identidade in ocupadas:
            if identidade not in cadastros:
                raise OrdemInvalida(
                    EQ_NAO_ENCONTRADO, f"{identidade!r} is not a registered equipment"
                )
        return tuple(lista)

    async def definir_ordem(self, ordem: object) -> tuple[str, ...]:
        """Saves the order after validating it, and drops a group a block just left.

        Grava a ordem depois de validá-la, e desfaz um grupo que um bloco acabou de deixar.
        """
        nova = self.validar(ordem)
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
        """The block of a removed equipment stays there, empty, and its group is dismantled.

        O bloco de um equipamento removido continua ali, vazio, e o grupo dele é desfeito.
        """
        async with self._trava:
            nova = sem(self._ordem, identidade)
            await self._conferir_membros(nova)
            self._ordem = nova
        return self._ordem

    def valores_de(self, dp: mapa.Dp) -> tuple[str, ...]:
        """The values a runtime enum really offers, which is the input list of one speaker.

        Os valores que um enum de runtime realmente oferece, que é a lista de entradas de uma
        caixa.
        """
        if dp.valores or dp.funcao != mapa.FUNCAO_ENTRADA:
            return dp.valores
        estado = self._estado(self.identidade(dp.bloco))
        if estado is None:
            return ()
        # Why: section 14, only the inputs the hardware declares are offered, and the ceiling
        # of ten of section 8 is what keeps an enum the platform refuses whole from taking the
        # input of that block off the bus entirely.
        # Por que: seção 14, só as entradas que o hardware declara são oferecidas, e o teto de
        # dez da seção 8 é o que impede um enum que a plataforma recusa inteiro de tirar a
        # entrada daquele bloco do barramento de vez.
        return mapa.valores_de_enum(estado.fontes)

    def valores(self) -> dict[int, object]:
        """Every reportable data point this module owns, ready for a report or a snapshot.

        Todo data point reportável que este módulo tem, pronto para um report ou um snapshot.
        """
        estados = self._gestor.estados()
        valores: dict[int, object] = {}
        for bloco in range(1, mapa.BLOCOS + 1):
            identidade = self.identidade(bloco)
            estado = estados.get(identidade) if identidade else None
            # Why: a block nobody occupies publishes nothing at all, because a bridge that
            # read a false online would show an empty block as a speaker that is switched off.
            # Por que: um bloco que ninguém ocupa não publica nada, porque uma ponte que lesse
            # um online falso mostraria um bloco vazio como uma caixa desligada.
            if estado is None:
                continue
            valores.update(self._do_bloco(bloco, estado))
        valores[mapa.GRUPO] = self.grupo()
        self._com_nomes(valores)
        return valores

    def escravos_alheios(self) -> tuple[int, ...]:
        """The blocks whose speaker is in multiroom slave mode of a group this hub does NOT
        lead, which is a state the customer can reach with the app of the manufacturer, or a
        lost reply to a join, or a restart while a group was up.

        Why: a speaker in that mode refuses volume, transport, preset and input, and nothing
        here ever put it there, so reporting it as solo drew a panel full of controls that
        only ever answer no, with nothing anywhere saying why.

        Os blocos cuja caixa está em modo escravo de multiroom de um grupo que este hub NÃO
        lidera, que é um estado que o cliente alcança com o app do fabricante, ou uma resposta
        perdida a um convite, ou um reinício com um grupo de pé.

        Por que: uma caixa nesse modo recusa volume, transporte, preset e entrada, e nada aqui
        a pôs lá, então reportá-la como solo desenhava um painel cheio de controles que só
        respondem não, sem nada em lugar nenhum dizendo por quê.
        """
        nossos = {self._mestre, *self._escravos}
        alheios = []
        for alvo in self._multirooms():
            if alvo.bloco in nossos:
                continue
            if alvo.driver.e_escravo():
                alheios.append(alvo.bloco)
        return tuple(alheios)

    async def _recuperar_alheios(self) -> None:
        """Asks a speaker held in someone else's group to leave it, and says so when it stays.

        Why: a speaker in that mode refuses volume, transport, preset and input, so leaving it
        there is leaving the block dead. Section 14 records Ungroup as a command of the master,
        so it is not certain a slave obeys it, and the honest behaviour when it does not is to
        keep the block flagged instead of publishing it as an ordinary block.

        Pede a uma caixa presa no grupo de outro que saia dele, e diz quando ela fica.

        Por que: uma caixa nesse modo recusa volume, transporte, preset e entrada, então
        deixá-la ali é deixar o bloco morto. A seção 14 registra o Ungroup como comando do
        mestre, então não é certo que um escravo obedeça, e o comportamento honesto quando ele
        não obedece é manter o bloco sinalizado em vez de publicá-lo como bloco comum.
        """
        for bloco in self.escravos_alheios():
            alvo = self._multiroom(bloco)
            if alvo is None:
                continue
            log.warning(
                "block %d is a multiroom slave of a group this hub does not lead, "
                "asking it to leave",
                bloco,
            )
            codigo = await self._chamar(alvo.driver.desfazer_grupo())
            if codigo is not None:
                log.warning(
                    "block %d would not leave the group it is held in: %s, so it refuses "
                    "every command until it does",
                    bloco,
                    codigo,
                )

    async def reler(self, dpid: int) -> None:
        """Asks the device that owns a data point for its state, out of turn and awaited.

        Why: the reread of section 8 is a check against the DEVICE. Publishing from the cache
        1.5 s after the command compares the optimistic value against a cache the command
        itself wrote, so the check agreed with the guess every time and a speaker that
        accepted a volume and ignored it kept the wrong value on the bus until the next poll.

        Pede o estado ao aparelho dono de um data point, fora da vez e esperando.

        Por que: a releitura da seção 8 é uma conferência contra o APARELHO. Publicar do cache
        1,5 s depois do comando compara o valor otimista com um cache que o próprio comando
        escreveu, então a conferência concordava com o palpite toda vez e uma caixa que
        aceitasse um volume e o ignorasse mantinha o valor errado no barramento até o poll
        seguinte.
        """
        dp = mapa.de_dp(dpid)
        if dp is None or not dp.bloco:
            return
        identidade = self.identidade(dp.bloco)
        if identidade:
            await self._gestor.visitar_e_esperar(identidade)

    async def sanear(self) -> None:
        """Boot: a group left behind by a previous run is taken down before anything else.

        Boot: um grupo deixado por uma execução anterior cai antes de qualquer outra coisa.
        """
        async with self._trava:
            self._mestre = 0
            self._escravos = ()
            alvos = tuple(self._multirooms())
            for alvo in alvos:
                alvo.driver.marcar_grupo(False)
            # Why: this runs before the listening socket opens, and section 14 measured
            # /health answering in about 7 s on the reference appliance. Asking six speakers
            # one after the other spends a deadline per speaker, so a site whose boxes are
            # unreachable (a VLAN change, a router reboot) had no panel for half a minute,
            # which is exactly when the operator needs it most. Asking them together costs the
            # slowest one instead of the sum.
            # Por que: isto roda antes de o socket de escuta abrir, e a seção 14 mediu o
            # /health respondendo em uns 7 s no appliance de referência. Perguntar a seis
            # caixas uma depois da outra gasta um prazo por caixa, então um site com as caixas
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
                log.warning("block %d led a group nobody asked for, taking it down", alvo.bloco)
            await asyncio.gather(
                *(self._chamar(alvo.driver.desfazer_grupo()) for alvo in lideres),
                return_exceptions=True,
            )
            await self._recuperar_alheios()

    async def sincronizar(self) -> None:
        """Reconciles a group that dissolved by itself and mirrors the master onto the slaves.

        Reconcilia um grupo que se desfez sozinho e espelha o mestre nos escravos.
        """
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
            for bloco in self._escravos:
                escravo = self._multiroom(bloco)
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
                    log.warning("block %d left the group of block %d", bloco, self._mestre)
                    escravo.driver.marcar_grupo(False)
                    continue
                restantes.append(bloco)
            self._escravos = tuple(restantes)
            if not restantes:
                await self._desfazer()
                return
            self._espelhar(mestre)

    async def aplicar(self, dpid: object, valor: object) -> str | None:
        """One set of section 8, done or refused with a stable code; nothing raises out.

        Um set da seção 8, feito ou recusado com um código estável; nada estoura daqui.
        """
        dp = mapa.de_dp(dpid)
        if dp is None:
            return protocolo.DP_DESCONHECIDO
        if not dp.ajustavel:
            return protocolo.DP_SOMENTE_LEITURA
        if dp.bloco == 0 and dp.dpid != mapa.GRUPO:
            # Why: DP 131 is the scene, which belongs to the module that owns the scenes; a
            # block module that answered for it would run a scene from the wrong book.
            # Por que: o DP 131 é a cena, que é do módulo dono das cenas; um módulo de blocos
            # que respondesse por ela rodaria uma cena do livro errado.
            return protocolo.DP_DESCONHECIDO
        if not protocolo.valor_valido(dp, valor, self.valores_de(dp)):
            return protocolo.VALOR_INVALIDO
        async with self._trava:
            if dp.dpid == mapa.GRUPO:
                return await self._ativar(valor)
            return await self._na_bloco(dp, valor)

    def _do_bloco(self, bloco: int, estado: Estado) -> dict[int, object]:
        """The five data points of one block, read from the typed state of section 6.

        Os cinco data points de um bloco, lidos do estado tipado da seção 6.
        """
        valores: dict[int, object] = {mapa.dp_de(bloco, "online"): estado.online}
        if estado.volume is not None:
            valores[mapa.dp_de(bloco, "volume")] = estado.volume
        # Why: section 6 publishes the transport and the title as different facts, because
        # reading DP 102 from the title reported a speaker playing over bluetooth, over a line
        # input, or a radio with no metadata, as paused. A driver that cannot tell leaves
        # reproduzindo None, and a data point of section 8 is never reported on a guess.
        # Por que: a seção 6 publica o transporte e o título como fatos diferentes, porque ler
        # o DP 102 do título reportava como pausada uma caixa tocando por bluetooth, por
        # entrada de linha, ou um rádio sem metadado. Um driver que não sabe dizer deixa o
        # reproduzindo em None, e um data point da seção 8 nunca é reportado por palpite.
        play = self._play_de(bloco, estado)
        if play is not None:
            valores[mapa.dp_de(bloco, "play")] = play
        valores[mapa.dp_de(bloco, "tocando")] = mapa.texto_de_dp(estado.tocando or "")
        if estado.fonte and estado.fonte in mapa.valores_de_enum(estado.fontes):
            valores[mapa.dp_de(bloco, mapa.FUNCAO_ENTRADA)] = estado.fonte
        return valores

    def _play_de(self, bloco: int, estado: Estado) -> bool | None:
        """DP 102 of one block: the transport of a driver that has one, the power of any other.

        O DP 102 de um bloco: o transporte de um driver que o tem, o liga/desliga de qualquer
        outro.
        """
        identidade = self.identidade(bloco)
        if self._com_transporte(identidade):
            return estado.reproduzindo
        if self._com_energia(identidade):
            return estado.ligado
        return None

    def _com_transporte(self, identidade: str) -> bool:
        """Section 8: DP 102 is play/pause for a driver that declares both transport
        capabilities and the power switch for one that declares both power capabilities; the
        manifest decides, never the category, so a receiver in a block gets a switch on the
        app and a speaker gets play/pause. Half of either pair is no key at all, because a
        switch that turns on and cannot turn off is a switch the customer cannot trust.

        Seção 8: o DP 102 é play/pause para um driver que declara as duas capacidades de
        transporte e a chave de ligar para um que declara as duas de energia; o manifesto
        decide, nunca a categoria, então um receiver num bloco ganha uma chave no app e uma
        caixa ganha play/pause. Metade de qualquer par não é tecla nenhuma, porque uma chave
        que liga e não desliga é uma chave em que o cliente não pode confiar.
        """
        return self._declara(identidade, ACAO_TOCAR, ACAO_PAUSAR)

    def _com_energia(self, identidade: str) -> bool:
        return self._declara(identidade, ACAO_LIGAR, ACAO_DESLIGAR)

    def _declara(self, identidade: str, *acoes: str) -> bool:
        manifesto = self._gestor.manifesto(identidade)
        if manifesto is None:
            return False
        return all(acao in manifesto.capacidades for acao in acoes)

    def _com_nomes(self, valores: dict[int, object]) -> None:
        """DP 133 and DP 135, in the compact JSON of section 8 and inside its 255 bytes.

        O DP 133 e o DP 135, no JSON compacto da seção 8 e dentro dos 255 bytes dele.
        """
        cadastros = self._cadastros()
        ocupadas = [self.identidade(bloco) for bloco in range(1, self._quantas() + 1)]
        nomes = [self._nome(cadastros.get(identidade)) for identidade in ocupadas]
        # Why: grupoN is the group led by block N, so the name of a group is the name of the
        # block that leads it and the list stays positional; a block that has nobody of its own
        # kind to group with leads no group and carries no name.
        # Por que: o grupoN é o grupo liderado pelo bloco N, então o nome de um grupo é o nome
        # do bloco que o lidera e a lista continua posicional; um bloco que não tem ninguém do
        # tipo dela para agrupar não lidera grupo e não carrega nome.
        grupos = [
            nome if self._pode_liderar(bloco) else VAZIA
            for bloco, nome in enumerate(nomes, start=1)
        ]
        for dpid, lista in ((mapa.NOMES_BLOCOS, nomes), (mapa.NOMES_GRUPOS, grupos)):
            texto = _nomes_json(dpid, lista)
            if texto is not None:
                valores[dpid] = texto

    def _quantas(self) -> int:
        """How many blocks the names carry: up to the last one somebody occupies.

        Quantos blocos os nomes carregam: até o último que alguém ocupa.
        """
        ocupados = [bloco for bloco in range(1, mapa.BLOCOS + 1) if self.identidade(bloco)]
        return max(ocupados) if ocupados else 0

    def _nome(self, cadastro: Cadastro | None) -> str:
        return cadastro.nome if cadastro is not None and cadastro.nome else VAZIA

    def _pode_liderar(self, bloco: int) -> bool:
        """A block leads a group when there is another speaker of its own tipo to lead.

        Um bloco lidera um grupo quando existe outra caixa do tipo dele para liderar.
        """
        return bool(self.identidade(bloco)) and bool(self._companheiras(bloco))

    def _companheiras(self, bloco: int) -> tuple[int, ...]:
        """The blocks a group led by this one may hold: same tipo, and never a mixed one.

        Os blocos que um grupo liderado por este pode ter: mesmo tipo, e nunca um misto.
        """
        cadastros = self._cadastros()
        mestre = cadastros.get(self.identidade(bloco))
        if mestre is None or not self._e_multiroom(mestre.identidade):
            return ()
        companheiras = []
        for outra in range(1, mapa.BLOCOS + 1):
            cadastro = cadastros.get(self.identidade(outra))
            # Why: section 14, a group only ever exists between speakers of the same domain,
            # so a speaker of another kind is never even invited; offering a mixed group is
            # what leaves half of it playing and the other half silent.
            # Por que: seção 14, um grupo só existe entre caixas do mesmo domínio, então uma
            # caixa de outro tipo nunca é convidada; oferecer grupo misto é o que deixa metade
            # dele tocando e a outra metade calada.
            if outra != bloco and cadastro is not None and cadastro.tipo == mestre.tipo:
                companheiras.append(outra)
        return tuple(companheiras)

    async def _ativar(self, valor: object) -> str | None:
        bloco = bloco_do_grupo(valor)
        if bloco is None:
            return protocolo.VALOR_INVALIDO
        if bloco == 0:
            return await self._desfazer()
        return await self._formar(bloco)

    async def _formar(self, bloco: int) -> str | None:
        """Forms the group led by one block: every speaker of its tipo joins that master.

        Forma o grupo liderado por um bloco: toda caixa do tipo dele entra naquele mestre.
        """
        mestre = self._multiroom(bloco)
        if mestre is None:
            # Why: a block whose equipment cannot group answers the code of a capability the
            # manifest does not declare; offline is only for a block nothing answers for.
            # Por que: um bloco cujo equipamento não agrupa responde o código de uma capacidade
            # que o manifesto não declara; offline é só para um bloco por que ninguém responde.
            return NAO_SUPORTADO if self._alvo(bloco) is not None else protocolo.BLOCO_OFFLINE
        if not mestre.cadastro.ip:
            return protocolo.BLOCO_OFFLINE
        companheiras = self._companheiras(bloco)
        if not companheiras:
            # Why: a group of one is not a group, and a bus that answered ok for it would
            # publish a group the customer cannot hear.
            # Por que: um grupo de um não é grupo, e um barramento que respondesse ok por ele
            # publicaria um grupo que o cliente não escuta.
            return NAO_SUPORTADO
        presentes = [alvo for alvo in map(self._multiroom, companheiras) if alvo is not None]
        if not presentes:
            return protocolo.BLOCO_OFFLINE
        if self._mestre and self._mestre != bloco:
            await self._desfazer()
        entraram: list[int] = []
        recusa: str | None = None
        for alvo in presentes:
            codigo = await self._chamar(alvo.driver.entrar_no_grupo(mestre.cadastro.ip))
            if codigo is None:
                alvo.driver.marcar_grupo(True)
                entraram.append(alvo.bloco)
            else:
                log.warning("block %d did not join the group of block %d", alvo.bloco, bloco)
                recusa = recusa or codigo
        if not entraram:
            return recusa
        mestre.driver.marcar_grupo(True)
        self._mestre = bloco
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
            log.warning("block %d refused to dismantle its group: %s", self._mestre, codigo)
            return codigo
        self._soltar()
        return codigo

    def _largar(self, blocos: Iterable[int]) -> None:
        """Takes blocks out of the group in our books, clearing the mark on each speaker.

        Why: a block dropped from the books while its driver still believes it is in a group
        refuses transport and input forever, for a group nobody is in any more.

        Tira blocos do grupo nos nossos livros, limpando a marca em cada caixa.

        Por que: um bloco largado dos livros com o driver dele ainda achando que está num
        grupo recusa transporte e entrada para sempre, por um grupo em que ninguém mais está.
        """
        for bloco in blocos:
            alvo = self._multiroom(bloco)
            if alvo is not None:
                alvo.driver.marcar_grupo(False)

    def _soltar(self) -> None:
        """Forgets the group in our books and clears the mark on every speaker of it.

        Esquece o grupo nos nossos livros e limpa a marca em toda caixa dele.
        """
        self._largar((self._mestre, *self._escravos))
        self._mestre = 0
        self._escravos = ()

    async def _conferir_membros(self, nova: tuple[str, ...]) -> None:
        """A group whose master or whose last slave leaves the order is not a group any more,
        and it is taken down while the CURRENT order can still reach the master.

        Why: the books are kept by IDENTITY and never by position, because any registered
        equipment may take a block now; a projector put in the block of a slave would inherit
        its role and receive, as the slave, the volume meant for a speaker.

        Um grupo cujo mestre ou cujo último escravo sai da ordem deixou de ser grupo, e ele é
        derrubado enquanto a ordem ATUAL ainda alcança o mestre.

        Por que: os livros são mantidos por IDENTIDADE e nunca por posição, porque qualquer
        equipamento cadastrado pode ocupar um bloco agora; um projetor posto no bloco de um
        escravo herdaria o papel dele e receberia, como escravo, o volume de uma caixa.
        """
        if not self._mestre:
            return
        if _identidade_em(nova, self._mestre) != self.identidade(self._mestre):
            await self._desfazer(forcar=True)
            return
        ficam = tuple(
            bloco
            for bloco in self._escravos
            if _identidade_em(nova, bloco) == self.identidade(bloco)
        )
        self._largar(bloco for bloco in self._escravos if bloco not in ficam)
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
        for bloco in self._escravos:
            alvo = self._multiroom(bloco)
            if alvo is not None:
                alvo.driver.espelhar(tocando, reproduzindo)

    async def _na_bloco(self, dp: mapa.Dp, valor: object) -> str | None:
        alvo = self._alvo(dp.bloco)
        if alvo is None:
            return protocolo.BLOCO_OFFLINE
        if dp.funcao == "volume":
            return await self._volume(alvo, valor)
        if dp.funcao == "play":
            if self._com_transporte(alvo.cadastro.identidade):
                return await self._transporte(alvo, ACAO_TOCAR if valor else ACAO_PAUSAR, None)
            if self._com_energia(alvo.cadastro.identidade):
                acao = ACAO_LIGAR if valor else ACAO_DESLIGAR
                return await self._executar(alvo.cadastro.identidade, acao, None)
            return NAO_SUPORTADO
        if dp.funcao == "preset":
            # Why: a preset is "play the configured URL N", the vocabulary of a multiroom
            # driver (section 14); a matrix that declares comando_extra would otherwise get
            # the literal preset:N written on its wire.
            # Por que: um preset é "toca a URL configurada N", vocabulário de um driver
            # multiroom (seção 14); uma matriz que declara comando_extra receberia o literal
            # preset:N escrito no fio dela.
            if not self._e_multiroom(alvo.cadastro.identidade):
                return NAO_SUPORTADO
            return await self._transporte(alvo, ACAO_PRESET, _preset(valor))
        # Why: the input of a speaker is its own even inside a group, and the driver is the
        # one that refuses it while grouped, because it is the driver that knows the group
        # breaks; routing it to the master would change the input of the wrong speaker.
        # Por que: a entrada de uma caixa é dela mesmo dentro de um grupo, e é o driver que a
        # recusa enquanto agrupada, porque é ele que sabe que o grupo quebra; mandá-la ao
        # mestre trocaria a entrada da caixa errada.
        return await self._executar(alvo.cadastro.identidade, ACAO_FONTE, valor)

    async def _volume(self, alvo: _Alvo, valor: object) -> str | None:
        """Section 14: the volume of a slave goes through the master, never to the slave.

        Seção 14: o volume de um escravo passa pelo mestre, nunca vai para o escravo.
        """
        mestre = self._mestre_de(alvo.bloco)
        if mestre is None:
            return await self._executar(alvo.cadastro.identidade, ACAO_VOLUME, valor)
        return await self._chamar(mestre.driver.volume_de_escravo(alvo.cadastro.ip, valor))

    async def _transporte(self, alvo: _Alvo, acao: str, valor: object) -> str | None:
        """Section 14: a play on a slave dismantles the group, so transport goes to the master.

        Seção 14: um play num escravo desmonta o grupo, então o transporte vai para o mestre.
        """
        mestre = self._mestre_de(alvo.bloco)
        destino = alvo if mestre is None else mestre
        return await self._executar(destino.cadastro.identidade, acao, valor)

    def _mestre_de(self, bloco: int) -> _Alvo | None:
        """The master of a block that is a slave right now, or None when it answers for itself.

        O mestre de um bloco que é escravo agora, ou None quando ele responde por si.
        """
        if bloco not in self._escravos:
            return None
        return self._multiroom(self._mestre)

    async def _executar(self, identidade: str, acao: str, valor: object) -> str | None:
        return _traduzir(await self._gestor.executar(identidade, acao, valor))

    async def _chamar(self, chamada: Awaitable[str | None]) -> str | None:
        """One group move straight into a driver, with the deadline and with no exception out.

        Um movimento de grupo direto no driver, com prazo e sem exceção saindo.
        """
        try:
            async with asyncio.timeout(self._limite_s):
                return _traduzir(await chamada)
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

    def _alvo(self, bloco: int) -> _Alvo | None:
        """The block as something that can be commanded, or None when nothing answers for it.

        O bloco como algo que se pode comandar, ou None quando nada responde por ele.
        """
        identidade = self.identidade(bloco)
        cadastro = self._cadastros().get(identidade)
        driver = self._gestor.driver(identidade) if identidade else None
        if cadastro is None or driver is None:
            # Why: an identity that is not registered any more is an empty block and not an
            # error of the bus, because the file may have been edited by hand.
            # Por que: uma identidade que não está mais cadastrada é um bloco vazio e não um
            # erro do barramento, porque o arquivo pode ter sido editado na mão.
            return None
        return _Alvo(bloco=bloco, cadastro=cadastro, driver=driver)

    def _multiroom(self, bloco: int) -> _Alvo | None:
        """The block only when a group can really be made of what is mounted for it.

        O bloco só quando um grupo pode mesmo ser feito do que está montado nele.
        """
        alvo = self._alvo(bloco)
        if alvo is None or not self._e_multiroom(alvo.cadastro.identidade):
            return None
        if not all(hasattr(alvo.driver, movimento) for movimento in MOVIMENTOS):
            log.error("driver of block %d declares agrupar and offers no group move", bloco)
            return None
        return alvo

    def _multirooms(self) -> tuple[_Alvo, ...]:
        alvos = (self._multiroom(bloco) for bloco in range(1, mapa.BLOCOS + 1))
        return tuple(alvo for alvo in alvos if alvo is not None)


def _preset(valor: object) -> str:
    """cmd3 of DP 103 as the preset the driver of section 6 takes in comando_extra.

    O cmd3 do DP 103 como o preset que o driver da seção 6 recebe no comando_extra.
    """
    numero = str(valor)[len(PREFIXO_PRESET) :]
    return f"preset:{numero}"


def _traduzir(codigo: str | None) -> str | None:
    """A code of section 6 in the vocabulary the bus of section 8 speaks.

    Um código da seção 6 no vocabulário que o barramento da seção 8 fala.
    """
    if codigo is None:
        return None
    if codigo in (EQ_OFFLINE, EQ_NAO_ENCONTRADO):
        # Why: on the bus a speaker that did not answer and a block whose equipment is gone
        # are the same thing to the bridge, which asked a block and got no block.
        # Por que: no barramento uma caixa que não respondeu e um bloco cujo equipamento sumiu
        # são a mesma coisa para a ponte, que perguntou por um bloco e não achou bloco.
        return protocolo.BLOCO_OFFLINE
    if codigo == INVALID_VALUE:
        return protocolo.VALOR_INVALIDO
    if codigo in CODIGOS:
        return codigo
    log.error("a driver answered %r, outside the vocabulary of the bus", codigo)
    return ERRO_APARELHO


def _nomes_json(dpid: int, nomes: Sequence[str]) -> str | None:
    """The names of a string DP inside its 255 bytes, shortened only when they do not fit.

    Os nomes de um DP string dentro dos 255 bytes dele, encurtados só quando não couberem.
    """
    try:
        return mapa.nomes_json(dpid, nomes)
    except mapa.NomesInvalidos:
        pass
    # Why: the names of the blocks are the names of the equipment, which the registration takes
    # long and in any alphabet; refusing the whole DP would take the names of SIX blocks off
    # the bus because one of them is long, so each name is shortened to its fair share of the
    # budget instead, on a character boundary, and the JSON always reaches the bridge whole.
    # Por que: os nomes dos blocos são os nomes dos equipamentos, que o cadastro aceita longos e
    # em qualquer alfabeto; recusar o DP inteiro tiraria do barramento os nomes de SEIS blocos
    # porque um deles é longo, então cada nome é encurtado para a parte justa do orçamento, em
    # fronteira de caractere, e o JSON sempre chega inteiro à ponte.
    # Why: json escapes a quote as \" and a backslash as \\, so a budget measured in raw
    # bytes lies for a name that carries them and the shortened list overflows again, which
    # dropped the names of all six blocks off the bus. The budget is squeezed until the encoded
    # JSON really fits, because a fallback that can fail is the failure it was written against.
    # Por que: o json escapa uma aspa como \" e uma barra como \\, então um orçamento medido
    # em bytes crus mente para um nome que as carrega e a lista encurtada estoura de novo, o
    # que tirava do barramento os nomes dos seis blocos. O orçamento é apertado até o JSON
    # codificado caber de verdade, porque um plano B que pode falhar é a falha que ele evita.
    try:
        moldura = len(mapa.nomes_json(dpid, [VAZIA] * len(nomes)).encode("utf-8"))
    except mapa.NomesInvalidos:
        log.error("dp %d has no room for even the empty names", dpid)
        return None
    orcamento = (mapa.TEXTO_MAXIMO_BYTES - moldura) // max(len(nomes), 1)
    while orcamento > 0:
        try:
            return mapa.nomes_json(dpid, [_encurtar(nome, orcamento) for nome in nomes])
        except mapa.NomesInvalidos:
            orcamento -= 1
    log.error("dp %d carries names that do not fit even shortened", dpid)
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

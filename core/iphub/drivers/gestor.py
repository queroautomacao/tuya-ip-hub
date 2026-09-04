# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6 in one place: the capability gate, the staggered poll and the offline counter.

Seção 6 num lugar só: o portão de capacidade, o poll escalonado e o contador de offline.
"""

import asyncio
import contextlib
import functools
import logging
import time
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from dataclasses import replace

from iphub.config import Cadastro
from iphub.drivers.base import (
    CODIGOS,
    CONTRATO_QUEBRADO,
    NAO_SUPORTADO,
    RESULTADOS,
    TIPO_DESCONHECIDO,
    Driver,
)
from iphub.drivers.manifesto import CAPACIDADES, Estado, Manifesto

log = logging.getLogger("iphub.drivers.gestor")

INTERVALO_S = 10.0

# Why: section 14 fixed two failures for the speaker poll and the reason generalizes; one
# lost datagram is not an offline device, and a panel that blinks offline on every hiccup
# teaches the integrator to stop believing it.
# Por que: a seção 14 fixou duas falhas para o poll da caixa e o motivo se generaliza; um
# datagrama perdido não é aparelho offline, e um painel que pisca offline a cada soluço
# ensina o integrador a parar de acreditar nele.
FALHAS_ATE_OFFLINE = 2

# Why: the only deadline a driver guarantees is per exchange, so half the poll interval is
# the ceiling the gestor imposes on a single call into one, and the other half stays with
# the rest of the segment.
# Por que: o único prazo que um driver garante é por troca, então metade do intervalo de poll
# é o teto que o gestor impõe a uma chamada para dentro de um, e a outra metade fica com o
# resto do segmento.
FRACAO_DO_LIMITE = 0.5

EQ_NAO_ENCONTRADO = "eq_nao_encontrado"
EQ_OFFLINE = "eq_offline"
ERRO_APARELHO = "erro_aparelho"
IDENTIDADE_DUPLICADA = "identidade_duplicada"
FALHOU = "falhou"

type Relogio = Callable[[], float]
type Dormir = Callable[[float], Awaitable[None]]


class ErroDeCadastro(ValueError):
    """Carries the stable code the API answers with, so no route invents one of its own.

    Carrega o código estável com que a API responde, para nenhuma rota inventar um próprio.
    """

    codigo = ""


class IdentidadeDuplicada(ErroDeCadastro):
    codigo = IDENTIDADE_DUPLICADA


class EquipamentoDesconhecido(ErroDeCadastro):
    codigo = EQ_NAO_ENCONTRADO


class Gestor:
    """Holds the drivers, imposes the rules of section 6 and polls on a single task.

    Guarda os drivers, impõe as regras da seção 6 e faz o poll numa única tarefa.
    """

    def __init__(
        self,
        catalogo: dict[str, type[Driver]],
        cadastros: Iterable[Cadastro],
        *,
        intervalo_s: float = INTERVALO_S,
        limite_s: float | None = None,
        agora: Relogio = time.monotonic,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        self._catalogo = dict(catalogo)
        self._intervalo_s = intervalo_s
        self._limite_s = intervalo_s * FRACAO_DO_LIMITE if limite_s is None else limite_s
        self._agora = agora
        self._dormir = dormir
        self._cadastros: dict[str, Cadastro] = {}
        for cadastro in cadastros:
            if cadastro.identidade in self._cadastros:
                raise IdentidadeDuplicada(cadastro.identidade)
            self._cadastros[cadastro.identidade] = cadastro
        self._drivers: dict[str, Driver] = {}
        self._falhas: dict[str, int] = {}
        self._problemas: dict[str, str] = {}
        self._tarefa: asyncio.Task | None = None
        self._visitas: set[asyncio.Task] = set()
        self._em_voo: dict[str, asyncio.Task] = {}
        self._montando: set[str] = set()
        self._proximo = 0.0

    @property
    def cadastros(self) -> tuple[Cadastro, ...]:
        return tuple(self._cadastros.values())

    async def iniciar(self) -> None:
        for cadastro in self._cadastros.values():
            await self._montar(cadastro)
        self._tarefa = asyncio.create_task(self._laco(), name="gestor-poll")
        self._tarefa.add_done_callback(self._fim_do_laco)

    async def parar(self) -> None:
        tarefa, self._tarefa = self._tarefa, None
        if tarefa is not None:
            tarefa.cancel()
            # Why: a poll task that already died carries its exception here, and stopping the
            # daemon must not fail on it; the done callback logged it when it happened.
            # Por que: uma tarefa de poll que já morreu carrega a exceção dela aqui, e parar o
            # daemon não pode falhar por causa disso; o callback já registrou quando aconteceu.
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await tarefa
        # Why: a poll of the loop is a task of its own now, and cancelling only the loop
        # would leave it on the wire of a device the shutdown is closing.
        # Por que: um poll do laço agora é uma tarefa própria, e cancelar só o laço o deixaria
        # no fio de um aparelho que o desligamento está fechando.
        visitas = tuple(self._visitas | set(self._em_voo.values()))
        for visita in visitas:
            visita.cancel()
        await asyncio.gather(*visitas, return_exceptions=True)
        for identidade in list(self._drivers):
            await self._desmontar(identidade)

    def estados(self) -> dict[str, Estado]:
        """One Estado per registration, the ones with no live driver included.

        Um Estado por cadastro, incluindo os que não têm driver vivo.
        """
        return {identidade: self._estado_de(identidade) for identidade in self._cadastros}

    def driver(self, identidade: str) -> Driver | None:
        """The live driver of one registration, or None when nothing is mounted for it.

        O driver vivo de um cadastro, ou None quando nada está montado para ele.
        """
        # Why: the group moves of section 14 (join a master, ungroup, a slave volume, read the
        # real group, mirror what the master plays) are not actions of section 6, so the gate
        # of executar has no vocabulary for them and the module that owns the blocks has no
        # other door to the driver. It stays a read: the gestor still owns the lifecycle.
        # Por que: os movimentos de grupo da seção 14 (entrar num mestre, desagrupar, volume de
        # escravo, ler o grupo real, espelhar o que o mestre toca) não são ações da seção 6,
        # então o portão do executar não tem vocabulário para eles e o módulo dono dos blocos
        # não tem outra porta para o driver. Isto continua sendo leitura: o ciclo de vida
        # segue sendo do gestor.
        return self._drivers.get(identidade)

    def manifesto(self, identidade: str) -> Manifesto | None:
        cadastro = self._cadastros.get(identidade)
        if cadastro is None:
            return None
        classe = self._catalogo.get(cadastro.tipo)
        return None if classe is None else classe.MANIFESTO

    async def executar(self, identidade: str, acao: str, valor: object = None) -> str | None:
        """None for done, or one of the stable codes; never an exception out of a driver.

        None para feito, ou um dos códigos estáveis; nunca uma exceção saindo de um driver.
        """
        if identidade not in self._cadastros:
            return EQ_NAO_ENCONTRADO
        manifesto = self.manifesto(identidade)
        # Why: section 6 refuses what the manifest does not declare BEFORE the driver is
        # touched, so no driver ever writes a method only to say no.
        # Por que: a seção 6 recusa o que o manifesto não declara ANTES de tocar no driver,
        # então nenhum driver escreve método só para dizer não.
        if acao not in CAPACIDADES or manifesto is None or acao not in manifesto.capacidades:
            return NAO_SUPORTADO
        driver = self._drivers.get(identidade)
        if driver is None:
            return EQ_OFFLINE
        try:
            resposta = await self._com_prazo(driver.executar(acao, valor))
        except TimeoutError:
            # Why: the deadline is the gestor's and it can fire before the driver's own,
            # because the action still waits for the lock a poll of the same unreachable
            # device is holding. Of the stable codes of section 6, a device that did not
            # answer in time is eq_offline: erro_aparelho would send the integrator looking
            # for a fault in a device that is simply not there. No traceback, because an
            # unreachable device is the ordinary case and not a defect of the hub.
            # Por que: o prazo é do gestor e pode estourar antes do prazo do próprio driver,
            # porque a ação ainda espera a trava que um poll do mesmo aparelho inalcançável
            # segura. Dos códigos estáveis da seção 6, um aparelho que não respondeu a tempo
            # é eq_offline: erro_aparelho mandaria o integrador procurar defeito num aparelho
            # que simplesmente não está lá. Sem traceback, porque aparelho inalcançável é o
            # caso comum e não defeito do hub.
            log.warning("equipment %s did not answer %r within the deadline", identidade, acao)
            return EQ_OFFLINE
        except Exception as erro:
            log.exception("equipment %s failed on %r: %s", identidade, acao, _causa(erro))
            return ERRO_APARELHO
        if resposta is None or resposta in CODIGOS:
            return resposta
        # Why: a driver that answers a code of its own would reach the panel as an untranslated
        # phrase, and section 11 says the API never answers a phrase.
        # Por que: um driver que responde um código próprio chegaria ao painel como frase sem
        # tradução, e a seção 11 diz que a API nunca responde frase.
        log.error("equipment %s answered %r, outside the stable codes", identidade, resposta)
        return ERRO_APARELHO

    async def autenticar(self, identidade: str) -> str:
        """One of RESULTADOS, always; a driver that breaks the contract answers falhou.

        Um de RESULTADOS, sempre; um driver que quebra o contrato responde falhou.
        """
        if identidade not in self._cadastros:
            raise EquipamentoDesconhecido(identidade)
        driver = self._drivers.get(identidade)
        if driver is None:
            return FALHOU
        try:
            resultado = await self._com_prazo(driver.autenticar())
        except Exception as erro:
            log.exception("equipment %s failed to pair: %s", identidade, _causa(erro))
            return FALHOU
        if resultado in RESULTADOS:
            return resultado
        log.error("equipment %s answered %r, outside %s", identidade, resultado, list(RESULTADOS))
        return FALHOU

    async def cadastrar(self, cadastro: Cadastro) -> tuple[Cadastro, ...]:
        """Hands the new tuple back; persisting it is the caller's job, never the gestor's.

        Devolve a tupla nova; gravá-la é trabalho de quem chama, nunca do gestor.
        """
        if cadastro.identidade in self._cadastros:
            raise IdentidadeDuplicada(cadastro.identidade)
        self._cadastros[cadastro.identidade] = cadastro
        await self._montar(cadastro)
        self.visitar_agora(cadastro.identidade)
        return self.cadastros

    async def remover(self, identidade: str) -> tuple[Cadastro, ...]:
        if identidade not in self._cadastros:
            raise EquipamentoDesconhecido(identidade)
        del self._cadastros[identidade]
        await self._desmontar(identidade)
        return self.cadastros

    async def atualizar_cadastro(self, cadastro: Cadastro) -> tuple[Cadastro, ...]:
        """Rebuilds the driver, because it read the address and the fields when it was born.

        Reconstrói o driver, porque ele leu o endereço e os campos quando nasceu.
        """
        if cadastro.identidade not in self._cadastros:
            raise EquipamentoDesconhecido(cadastro.identidade)
        await self._desmontar(cadastro.identidade)
        self._cadastros[cadastro.identidade] = cadastro
        await self._montar(cadastro)
        self.visitar_agora(cadastro.identidade)
        return self.cadastros

    async def trocar_catalogo(
        self, catalogo: dict[str, type[Driver]], *, refazer: Iterable[str] = ()
    ) -> None:
        """Section 7: a driver saved in the panel is usable at once, with no restart.

        Seção 7: um driver salvo no painel serve na hora, sem reiniciar.
        """
        # Why: a declaration that was saved is a NEW class for its tipo, so an equipment
        # already mounted goes on speaking the file it was born with until it is built again.
        # Only the tipos named are rebuilt, because rebuilding the others would drop the
        # session of every device on the installation to publish a driver none of them uses.
        # Por que: uma declaração salva é uma classe NOVA para o tipo dela, então um
        # equipamento já montado segue falando o arquivo com que nasceu até ser montado de
        # novo. Só os tipos nomeados são refeitos, porque refazer os outros derrubaria a
        # sessão de todo aparelho da instalação para publicar um driver que nenhum deles usa.
        self._catalogo = dict(catalogo)
        alvos = frozenset(refazer)
        for cadastro in tuple(self._cadastros.values()):
            if cadastro.tipo not in alvos:
                continue
            await self._desmontar(cadastro.identidade)
            await self._montar(cadastro)
            self.visitar_agora(cadastro.identidade)

    def visitar_agora(self, identidade: str) -> None:
        """Polls one equipment out of turn, without holding the caller that asked for it:
        waiting for the scheduled visit shows a fresh registration offline for up to two
        intervals, which the integrator reads as a registration that did not work.

        Faz o poll de um equipamento fora da vez, sem segurar quem o pediu: esperar a visita
        agendada mostra um cadastro novo offline por até dois intervalos, o que o integrador
        lê como cadastro que não funcionou.
        """
        tarefa = self._agendar(identidade)
        if tarefa is None:
            return
        self._visitas.add(tarefa)
        tarefa.add_done_callback(self._visitas.discard)

    async def visitar_e_esperar(self, identidade: str) -> None:
        """One out of turn poll, awaited: the caller needs the state that comes back.

        Why: the reread of section 8 exists to check what the device really did, so a caller
        that does not wait for the poll compares its own optimistic value against a cache it
        wrote itself, and the check always agrees with the guess.

        Um poll fora da vez, esperado: quem chama precisa do estado que voltar.

        Por que: a releitura da seção 8 existe para conferir o que o aparelho fez de verdade,
        então quem não espera o poll compara o próprio valor otimista com um cache que ele
        mesmo escreveu, e a conferência sempre concorda com o palpite.
        """
        tarefa = self._agendar(identidade)
        if tarefa is None:
            return
        # Why: a poll that failed already answered with the offline counter and the detalhe of
        # section 6; raising it here would take down the verification of the bus with it.
        # Por que: um poll que falhou já respondeu pelo contador de offline e pelo detalhe da
        # seção 6; levantá-lo aqui derrubaria junto a verificação do barramento.
        await asyncio.gather(tarefa, return_exceptions=True)

    def _agendar(self, identidade: str) -> asyncio.Task | None:
        """The one place a poll of one equipment starts, so a device never gets two sessions.

        O único lugar onde um poll de um equipamento começa, para um aparelho nunca receber
        duas sessões.
        """
        # Why: section 14, a matrix and a projector accept ONE connection at a time. An out of
        # turn visit landing on top of the scheduled one is a second session on the wire, and
        # so is a poll of an equipment whose driver is still opening its own.
        # Por que: seção 14, uma matriz e um projetor aceitam UMA conexão por vez. Uma visita
        # fora da vez em cima da agendada é uma segunda sessão no fio, e um poll de um
        # equipamento cujo driver ainda está abrindo a dele também é.
        if identidade not in self._drivers or identidade in self._montando:
            return None
        tarefa = self._em_voo.get(identidade)
        if tarefa is not None and not tarefa.done():
            return tarefa
        tarefa = asyncio.create_task(self._poll(identidade), name=f"gestor-visita:{identidade}")
        self._em_voo[identidade] = tarefa
        tarefa.add_done_callback(functools.partial(self._fim_do_poll, identidade))
        return tarefa

    def _fim_do_poll(self, identidade: str, tarefa: asyncio.Task) -> None:
        if self._em_voo.get(identidade) is tarefa:
            del self._em_voo[identidade]

    async def _encerrar_poll(self, identidade: str) -> None:
        """Takes the poll in flight off the wire, so nothing opens a session on top of it.

        Tira do fio o poll em voo, para nada abrir uma sessão em cima dele.
        """
        # Why: waiting for it instead would hand a device that accepted the connection and
        # went quiet the power to hold a save of the panel for the whole deadline.
        # Por que: esperar por ele daria a um aparelho que aceitou a conexão e emudeceu o poder
        # de segurar um salvamento do painel pelo prazo inteiro.
        tarefa = self._em_voo.pop(identidade, None)
        if tarefa is None or tarefa.done():
            return
        tarefa.cancel()
        await asyncio.gather(tarefa, return_exceptions=True)

    async def _com_prazo[T](self, chamada: Coroutine[object, object, T]) -> T:
        """The deadline of a call into a driver is the gestor's: a device that accepts the
        connection and then goes quiet would otherwise hold the poll of every other one.

        O prazo de uma chamada para dentro de um driver é do gestor: um aparelho que aceita a
        conexão e emudece seguraria, sem isso, o poll de todo outro.
        """
        async with asyncio.timeout(self._limite_s):
            return await chamada

    async def _montar(self, cadastro: Cadastro) -> None:
        identidade = cadastro.identidade
        classe = self._catalogo.get(cadastro.tipo)
        if classe is None:
            # Why: the registration of the integrator outlives a driver that left the image,
            # and throwing the registration away would be worse than reporting it offline.
            # Por que: o cadastro do integrador sobrevive a um driver que saiu da imagem, e
            # jogar o cadastro fora seria pior do que reportá-lo offline.
            log.warning("equipment %s has an unknown tipo %r", identidade, cadastro.tipo)
            self._problemas[identidade] = TIPO_DESCONHECIDO
            return
        try:
            driver = classe(cadastro)
        except Exception as erro:
            log.exception("equipment %s could not be built: %s", identidade, _causa(erro))
            self._problemas[identidade] = ERRO_APARELHO
            return
        self._drivers[identidade] = driver
        self._falhas[identidade] = 0
        self._problemas.pop(identidade, None)
        self._montando.add(identidade)
        try:
            await self._com_prazo(driver.iniciar())
        except Exception as erro:
            self._falhar(identidade, driver, erro)
        finally:
            self._montando.discard(identidade)

    async def _desmontar(self, identidade: str) -> None:
        await self._encerrar_poll(identidade)
        driver = self._drivers.pop(identidade, None)
        self._falhas.pop(identidade, None)
        self._problemas.pop(identidade, None)
        if driver is None:
            return
        try:
            await self._com_prazo(driver.parar())
        except Exception as erro:
            # Why: a driver that refuses to close must not keep the daemon from closing the
            # rest of them, nor from answering the removal that asked for it.
            # Por que: um driver que se recusa a fechar não pode impedir o daemon de fechar os
            # outros, nem de responder à remoção que pediu isso.
            log.exception("equipment %s failed to stop: %s", identidade, _causa(erro))

    def _estado_de(self, identidade: str) -> Estado:
        problema = self._problemas.get(identidade, "")
        driver = self._drivers.get(identidade)
        if driver is None:
            return Estado(online=False, detalhe=problema)
        estado = self._ler_estado(identidade, driver)
        if problema:
            return replace(estado, online=False, detalhe=problema)
        return estado

    def _ler_estado(self, identidade: str, driver: Driver) -> Estado:
        """Section 6 makes the gestor the enforcer of the typed state, so it trusts no driver:
        one that raises here, or answers a loose dict, would turn the listing of EVERY
        equipment into a 500 and take even the remove button away from the panel.

        A seção 6 faz do gestor o fiscal do estado tipado, então ele não confia em driver
        algum: um que estoure aqui, ou responda um dict solto, transformaria a listagem de TODO
        equipamento num 500 e tiraria do painel até o botão de remover.
        """
        try:
            estado = driver.estado()
        except Exception as erro:
            log.exception("equipment %s failed to answer its state: %s", identidade, _causa(erro))
            return Estado(online=False, detalhe=CONTRATO_QUEBRADO)
        if not isinstance(estado, Estado):
            log.error("equipment %s answered %s, not an Estado", identidade, type(estado).__name__)
            return Estado(online=False, detalhe=CONTRATO_QUEBRADO)
        return estado

    async def _laco(self) -> None:
        """One task walks every driver, staggered, so they never hit the network together.

        Uma tarefa percorre todo driver, escalonada, para eles nunca baterem juntos na rede.
        """
        self._proximo = self._agora()
        while True:
            identidades = tuple(self._drivers)
            if not identidades:
                await self._esperar(self._intervalo_s)
                continue
            passo = self._intervalo_s / len(identidades)
            for identidade in identidades:
                await self._esperar(passo)
                await self._visitar(identidade)

    async def _esperar(self, passo: float) -> None:
        agora = self._agora()
        # Why: an alvo already in the past means a poll took longer than its slot, and catching
        # up in a burst would put every driver on the network in the same instant, which is
        # exactly what the stagger exists to avoid.
        # Por que: um alvo já no passado significa que um poll levou mais que a vaga dele, e
        # correr atrás em rajada colocaria todo driver na rede no mesmo instante, que é
        # exatamente o que o escalonamento existe para evitar.
        alvo = max(self._proximo + passo, agora)
        await self._dormir(alvo - agora)
        self._proximo = alvo

    async def _visitar(self, identidade: str) -> None:
        tarefa = self._agendar(identidade)
        if tarefa is None:
            return
        # Why: asyncio.wait, because the swap cancels the poll in flight and awaiting a
        # cancelled task would end the loop with the cancellation of somebody else.
        # Por que: asyncio.wait, porque a troca cancela o poll em voo e esperar por uma tarefa
        # cancelada encerraria o laço com o cancelamento de outra pessoa.
        await asyncio.wait({tarefa})

    async def _poll(self, identidade: str) -> None:
        driver = self._drivers.get(identidade)
        if driver is None:
            return
        try:
            await self._com_prazo(driver.atualizar())
        except asyncio.CancelledError:
            raise
        except BaseException as erro:
            # Why: a driver that raises outside Exception would end the poll task for good and
            # the hub would stop polling every device, in silence; a failure of one poll of one
            # driver is all it is allowed to be, and the next driver goes on being visited.
            # Por que: um driver que estoura fora de Exception encerraria a tarefa de poll de
            # vez e o hub pararia de fazer poll de todo aparelho, em silêncio; uma falha de um
            # poll de um driver é tudo que isso pode ser, e o driver seguinte segue visitado.
            self._falhar(identidade, driver, erro)
        else:
            if self._drivers.get(identidade) is driver:
                self._falhas[identidade] = 0
                self._problemas.pop(identidade, None)

    def _falhar(self, identidade: str, driver: Driver, erro: BaseException) -> None:
        # Why: a poll in flight lands after its registration was removed or rebuilt under it,
        # and writing then resurrects the bookkeeping of an equipment that no longer exists,
        # which nothing ever clears again.
        # Por que: um poll em voo aterrissa depois de o cadastro dele ser removido ou refeito
        # por baixo, e gravar aí ressuscita a contabilidade de um equipamento que não existe
        # mais, que nada nunca mais limpa.
        if self._drivers.get(identidade) is not driver:
            return
        falhas = self._falhas.get(identidade, 0) + 1
        self._falhas[identidade] = falhas
        log.warning("equipment %s did not answer: %s", identidade, _causa(erro))
        if falhas >= FALHAS_ATE_OFFLINE:
            self._problemas[identidade] = EQ_OFFLINE

    def _fim_do_laco(self, tarefa: asyncio.Task) -> None:
        """The poll task ending while the gestor runs is a silent hub, so it is logged loud.

        A tarefa de poll acabando com o gestor rodando é um hub calado, então isso vai alto.
        """
        if tarefa is not self._tarefa or tarefa.cancelled():
            return
        log.error("the poll loop ended while the gestor was running: %r", tarefa.exception())


def _causa(erro: BaseException) -> str:
    return str(erro) or type(erro).__name__

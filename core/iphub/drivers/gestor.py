# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6 in one place: the capability gate, the staggered poll and the offline counter.

Seção 6 num lugar só: o portão de capacidade, o poll escalonado e o contador de offline.
"""

import asyncio
import contextlib
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
        visitas = tuple(self._visitas)
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

    def visitar_agora(self, identidade: str) -> None:
        """Polls one equipment out of turn, without holding the caller that asked for it:
        waiting for the scheduled visit shows a fresh registration offline for up to two
        intervals, which the integrator reads as a registration that did not work.

        Faz o poll de um equipamento fora da vez, sem segurar quem o pediu: esperar a visita
        agendada mostra um cadastro novo offline por até dois intervalos, o que o integrador
        lê como cadastro que não funcionou.
        """
        if identidade not in self._drivers:
            return
        tarefa = asyncio.create_task(self._visitar(identidade), name=f"gestor-visita:{identidade}")
        self._visitas.add(tarefa)
        tarefa.add_done_callback(self._visitas.discard)

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
        try:
            await self._com_prazo(driver.iniciar())
        except Exception as erro:
            self._falhar(identidade, driver, erro)

    async def _desmontar(self, identidade: str) -> None:
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

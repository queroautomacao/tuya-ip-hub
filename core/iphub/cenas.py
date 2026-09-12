# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""A scene: DATA, a list of steps that each run one action on one equipment.

The rule fixes for a driver holds here for the same reason: what an integrator
saves is data and never program. A step names one equipment, one action and
one value, plus an optional wait in milliseconds after it, and that is the whole vocabulary.
No condition, no loop, no expression, no arithmetic. There is no step that runs a scene,
because a scene starting a scene is a loop written in data, and two of them naming each other
would be a hub that never stops.

Gives the ceiling: the scene data point of every licence carries a number from 1 to
32, so there are thirty two scenes and the POSITION of a scene is its number, the same number
in every licence. A scene that was erased leaves its slot empty instead of pulling the next
one back, because the shift would silently move scene 3 to scene 2 in every automation the
customer already built on the platform. Two string data points carry the names of all of
them, sixteen each, inside 255 bytes, so a name that does not fit there is refused when it is
saved and never cut when it is published.

Running one is fire and forget for whoever asked: the answer comes at once and the steps run
in order on a task of their own. A step that fails is logged with its stable code and the
scene goes on, because a projector that is off must not stop the lights of the same scene.
"""

import asyncio
import functools
import logging
import re
from collections.abc import Awaitable, Callable, Collection, Mapping, Sequence
from dataclasses import dataclass

from iphub.dpbus import mapa
from iphub.drivers.manifesto import (
    CAPACIDADES,
    TECLAS,
    TEMPERATURA_MAXIMA,
    TEMPERATURA_MINIMA,
    VENTOS,
)

log = logging.getLogger("iphub.cenas")

# Numbers 1 to 32 on the scene data point, and there is no thirty third.
MAXIMO = mapa.CENAS

# A scene that touches every equipment of a full installation (twelve of audio and video
# plus eight air conditioners) with a power, a level and an input each is sixty steps, so
# this ceiling holds a scene that touches everything and still refuses a file that grew into
# a program.
PASSOS_MAXIMOS = 64

# The longest real wait inside a scene is the warmup of a projector between the power
# command and the input one; a scene is not a scheduler, and anything longer than this is an
# automation on the platform and not a step here.
ESPERA_MAXIMA_MS = 30_000

# An AV device needs a moment between one command and the next (a receiver that is
# powering on drops the input it is sent in the same second), so a scene waits this much
# after every step that does not name its own wait, and the integrator edits it per scene.
INTERVALO_PADRAO_MS = 1_000

NOME_MAXIMO = 40
VALOR_TEXTO_MAXIMO = 64
IDENTIDADE_MAXIMA = 200

MILISSEGUNDO_S = 0.001

# The one action of a scene that is not a capability: the group of the licence
# of audio and video the equipment sits in, led by the identity in the value, or solo.
ACAO_GRUPO = "grupo"

# Agrupar is the capability a manifest declares to say the equipment CAN group; the move
# itself is the grupo action, so a scene never writes agrupar on a driver.
ACOES = (*(acao for acao in CAPACIDADES if acao != "agrupar"), ACAO_GRUPO)

SEM_VALOR = ("ligar", "desligar", "tocar", "pausar", "parar", "proxima", "anterior")
COM_TEXTO = ("fonte", "atalho", "modo", "comando_extra")

CAMPO = "cenas"
CHAVES_CENA = ("nome", "passos", "intervalo_ms")
CHAVES_PASSO = ("equipamento", "acao", "valor", "espera_ms")

# The stable codes a refusal carries, the daemon answers a code and never a
# phrase, and the panel translates it. The codes of the map come with the verdict of the
# names, which belongs to the map and is not written a second time here.
CENAS_NAO_LISTA = "cenas_nao_lista"
CENAS_DEMAIS = "cenas_demais"
CENA_NAO_OBJETO = "cena_nao_objeto"
CENA_CHAVE_DESCONHECIDA = "cena_chave_desconhecida"
CENA_NOME_INVALIDO = "cena_nome_invalido"
CENA_PASSOS_INVALIDOS = "cena_passos_invalidos"
CENA_PASSOS_DEMAIS = "cena_passos_demais"
CENA_PASSO_NAO_OBJETO = "cena_passo_nao_objeto"
CENA_EQUIPAMENTO_INVALIDO = "cena_equipamento_invalido"
CENA_EQUIPAMENTO_DESCONHECIDO = "cena_equipamento_desconhecido"
CENA_ACAO_DESCONHECIDA = "cena_acao_desconhecida"
CENA_VALOR_INVALIDO = "cena_valor_invalido"
CENA_ESPERA_INVALIDA = "cena_espera_invalida"
CENA_INTERVALO_INVALIDO = "cena_intervalo_invalido"

CODIGOS = (
    CENAS_NAO_LISTA,
    CENAS_DEMAIS,
    CENA_NAO_OBJETO,
    CENA_CHAVE_DESCONHECIDA,
    CENA_NOME_INVALIDO,
    CENA_PASSOS_INVALIDOS,
    CENA_PASSOS_DEMAIS,
    CENA_PASSO_NAO_OBJETO,
    CENA_EQUIPAMENTO_INVALIDO,
    CENA_EQUIPAMENTO_DESCONHECIDO,
    CENA_ACAO_DESCONHECIDA,
    CENA_VALOR_INVALIDO,
    CENA_ESPERA_INVALIDA,
    CENA_INTERVALO_INVALIDO,
    mapa.NOMES_LONGOS,
    mapa.NOME_NAO_GRAVAVEL,
)

# What a request to run a scene answers with, which is not a problem of the saved file.
CENA_NAO_ENCONTRADA = "cena_nao_encontrada"
CENA_EM_CURSO = "cena_em_curso"
CODIGOS_DE_EXECUCAO = (CENA_NAO_ENCONTRADA, CENA_EM_CURSO)

_CONTROLE = re.compile(r"[\x00-\x1f\x7f]")

type Acionar = Callable[[str, str, object], Awaitable[str | None]]
type Dormir = Callable[[float], Awaitable[None]]


@dataclass(frozen=True)
class Passo:
    """One step: one equipment, one action, one value and the pause that follows it.

    Espera_ms is the pause AFTER the step, so a file reads in the order it happens: power the
    projector, wait for it, choose the input. None takes the interval of the scene. The wait
    of the LAST step is not slept, because nothing follows it and holding the task would only
    delay a shutdown.
    """

    equipamento: str
    acao: str
    valor: object = None
    espera_ms: int | None = None


@dataclass(frozen=True)
class Cena:
    """One scene, whose number is its position; a slot nobody uses carries no step."""

    nome: str = ""
    passos: tuple[Passo, ...] = ()
    intervalo_ms: int = INTERVALO_PADRAO_MS


class CenasInvalidas(ValueError):
    """Carries every problem as (campo, codigo), so the panel fixes the list in one pass."""

    def __init__(self, problemas: tuple[tuple[str, str], ...]) -> None:
        self.problemas = problemas
        super().__init__("; ".join(f"{campo}: {codigo}" for campo, codigo in problemas))


def validar(dados: object, identidades: Collection[str] | None = None) -> tuple[Cena, ...]:
    """The scenes as typed data, or CenasInvalidas listing EVERY problem at once.

    With identidades, a step that names an equipment outside them is refused; without them,
    which is how a config.json is read on boot, the equipment is judged when the step runs,
    because a registration erased by hand must not keep the whole file from loading.

    Nothing but CenasInvalidas leaves here, for any input at all: this judges what a route
    received and what a hand edited config.json holds, and a validation that raised anything
    else would take the boot of the appliance down with it.
    """
    leitor = _Leitor(identidades)
    cenas = leitor.cenas(dados)
    if leitor.problemas:
        raise CenasInvalidas(tuple(leitor.problemas))
    return cenas


def nomes(cenas: Sequence[Cena]) -> tuple[str, ...]:
    """The names in the order of the scenes, which is what the two name data points publish."""
    return tuple(cena.nome for cena in cenas)


def numero_de(valor: object) -> int | None:
    """The scene number of a scene data point value, or None for anything outside 1..32."""
    return mapa.numero_de_cena(valor)


class Executor:
    """Holds the saved scenes and runs one on a task of its own, one run at a time each."""

    def __init__(
        self,
        cenas: Sequence[Cena],
        acionar: Acionar,
        *,
        dormir: Dormir = asyncio.sleep,
    ) -> None:
        self._cenas = tuple(cenas)
        self._acionar = acionar
        self._dormir = dormir
        self._em_curso: dict[int, asyncio.Task] = {}

    @property
    def cenas(self) -> tuple[Cena, ...]:
        return self._cenas

    def trocar(self, cenas: Sequence[Cena]) -> None:
        """Takes the saved list, with no restart. A run already going keeps the steps it
        started with, because half of one file and half of the next is a scene nobody wrote.
        """
        self._cenas = tuple(cenas)

    def nomes(self) -> tuple[str, ...]:
        return nomes(self._cenas)

    def cena_de(self, numero: object) -> Cena | None:
        """The scene of a number from 1 to 32, or None for a number outside the contract."""
        posicao = self._posicao(numero)
        return None if posicao is None else self._cenas[posicao - 1]

    def em_curso(self, numero: object) -> bool:
        posicao = self._posicao(numero)
        return posicao is not None and self._rodando(posicao)

    def executar(self, numero: object) -> str | None:
        """Answers at once: None when the scene started, or a stable code that refused it."""
        posicao = self._posicao(numero)
        if posicao is None:
            return CENA_NAO_ENCONTRADA
        cena = self._cenas[posicao - 1]
        if not cena.passos:
            # A slot with no step is a scene that was erased and whose number is held
            # open for the automations already built on it, not a scene that does nothing.
            return CENA_NAO_ENCONTRADA
        if self._rodando(posicao):
            # The same scene twice at once would interleave two sequences over the same
            # equipment, and the volume the customer ends up with is whichever step landed
            # last; one run at a time is the only outcome that matches what was written.
            return CENA_EM_CURSO
        tarefa = asyncio.create_task(self._rodar(posicao, cena), name=f"cena:{posicao}")
        self._em_curso[posicao] = tarefa
        tarefa.add_done_callback(functools.partial(self._fim, posicao))
        return None

    async def parar(self) -> None:
        """Takes every run off the wire, so a shutdown leaves no task behind."""
        tarefas = tuple(self._em_curso.values())
        for tarefa in tarefas:
            tarefa.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        self._em_curso.clear()

    def _posicao(self, numero: object) -> int | None:
        """The number as a slot of this list, or None for anything that is not one."""
        # The number comes from a route path or from a data point, so True and "2" reach
        # here; neither is a scene number and neither may pick one by luck.
        if type(numero) is not int or not 1 <= numero <= min(MAXIMO, len(self._cenas)):
            return None
        return numero

    def _rodando(self, posicao: int) -> bool:
        tarefa = self._em_curso.get(posicao)
        return tarefa is not None and not tarefa.done()

    async def _rodar(self, numero: int, cena: Cena) -> None:
        ultimo = len(cena.passos) - 1
        for posicao, passo in enumerate(cena.passos):
            await self._passo(numero, passo)
            espera = cena.intervalo_ms if passo.espera_ms is None else passo.espera_ms
            if espera and posicao < ultimo:
                await self._dormir(espera * MILISSEGUNDO_S)

    async def _passo(self, numero: int, passo: Passo) -> None:
        try:
            codigo = await self._acionar(passo.equipamento, passo.acao, passo.valor)
        except asyncio.CancelledError:
            raise
        except BaseException as erro:
            # A device library raising outside Exception deep in a socket call would end
            # the scene at that step, and the lights of the same scene would never be reached;
            # one failed step is all a failure here is allowed to be.
            log.warning(
                "scene %s could not run %s on %s: %s",
                numero,
                passo.acao,
                passo.equipamento,
                _causa(erro),
            )
            return
        if codigo is not None:
            log.warning(
                "scene %s was refused on %s of %s: %s",
                numero,
                passo.acao,
                passo.equipamento,
                codigo,
            )

    def _fim(self, numero: int, tarefa: asyncio.Task) -> None:
        if self._em_curso.get(numero) is tarefa:
            del self._em_curso[numero]


class _Leitor:
    """Collects every problem instead of stopping at the first, and never raises."""

    def __init__(self, identidades: Collection[str] | None) -> None:
        self.problemas: list[tuple[str, str]] = []
        self._identidades = None if identidades is None else set(identidades)

    def anotar(self, campo: str, codigo: str) -> None:
        self.problemas.append((campo, codigo))

    def cenas(self, dados: object) -> tuple[Cena, ...]:
        if not isinstance(dados, list | tuple):
            self.anotar(CAMPO, CENAS_NAO_LISTA)
            return ()
        if len(dados) > MAXIMO:
            self.anotar(CAMPO, CENAS_DEMAIS)
            return ()
        cenas = tuple(self.cena(item, indice) for indice, item in enumerate(dados))
        self.conferir_nomes(cenas)
        return cenas

    def conferir_nomes(self, cenas: tuple[Cena, ...]) -> None:
        """The two name data points carry every name, so the list is judged whole and by the
        map, which owns the 255 bytes and the code that says why they do not fit.
        """
        try:
            mapa.nomes_das_cenas(nomes(cenas))
        except mapa.NomesInvalidos as erro:
            self.anotar(CAMPO, erro.codigo)

    def cena(self, item: object, indice: int) -> Cena:
        onde = f"{CAMPO}[{indice}]"
        if not isinstance(item, Mapping):
            self.anotar(onde, CENA_NAO_OBJETO)
            return Cena()
        self.chaves(item, CHAVES_CENA, onde)
        return Cena(
            nome=self.nome(item.get("nome", ""), onde),
            passos=self.passos(item.get("passos", ()), onde),
            intervalo_ms=self.intervalo(item.get("intervalo_ms", INTERVALO_PADRAO_MS), onde),
        )

    def chaves(self, item: Mapping, aceitas: tuple[str, ...], onde: str) -> None:
        # A key that was typed and that nothing reads is a scene silently doing less
        # than what the integrator wrote, which is for a driver and holds here.
        for chave in item:
            if chave not in aceitas:
                self.anotar(f"{onde}.{chave}", CENA_CHAVE_DESCONHECIDA)

    def nome(self, valor: object, onde: str) -> str:
        if not isinstance(valor, str) or len(valor) > NOME_MAXIMO or _CONTROLE.search(valor):
            self.anotar(f"{onde}.nome", CENA_NOME_INVALIDO)
            return ""
        return valor

    def passos(self, valor: object, onde: str) -> tuple[Passo, ...]:
        campo = f"{onde}.passos"
        if not isinstance(valor, list | tuple):
            self.anotar(campo, CENA_PASSOS_INVALIDOS)
            return ()
        if len(valor) > PASSOS_MAXIMOS:
            self.anotar(campo, CENA_PASSOS_DEMAIS)
            return ()
        lidos = [self.passo(item, f"{campo}[{indice}]") for indice, item in enumerate(valor)]
        return tuple(passo for passo in lidos if passo is not None)

    def passo(self, item: object, onde: str) -> Passo | None:
        if not isinstance(item, Mapping):
            self.anotar(onde, CENA_PASSO_NAO_OBJETO)
            return None
        self.chaves(item, CHAVES_PASSO, onde)
        espera = self.espera(item.get("espera_ms"), onde)
        equipamento = self.equipamento(item.get("equipamento"), onde)
        acao = self.acao(item.get("acao"), onde)
        if equipamento is None or acao is None:
            return None
        valor = item.get("valor")
        if not valor_valido(acao, valor):
            self.anotar(f"{onde}.valor", CENA_VALOR_INVALIDO)
            return None
        return Passo(equipamento=equipamento, acao=acao, valor=valor, espera_ms=espera)

    def equipamento(self, valor: object, onde: str) -> str | None:
        campo = f"{onde}.equipamento"
        if not _texto(valor, IDENTIDADE_MAXIMA):
            self.anotar(campo, CENA_EQUIPAMENTO_INVALIDO)
            return None
        if self._identidades is not None and valor not in self._identidades:
            # A scene saved over an identity nobody registered is a button that will
            # never do anything, and the integrator is at the keyboard right now to fix it.
            self.anotar(campo, CENA_EQUIPAMENTO_DESCONHECIDO)
            return None
        return valor

    def acao(self, valor: object, onde: str) -> str | None:
        if not isinstance(valor, str) or valor not in ACOES:
            self.anotar(f"{onde}.acao", CENA_ACAO_DESCONHECIDA)
            return None
        return valor

    def espera(self, valor: object, onde: str) -> int | None:
        # An absent wait is the interval of the scene, so a file only names the waits
        # that differ from it.
        if valor is None:
            return None
        if not _milissegundos(valor):
            self.anotar(f"{onde}.espera_ms", CENA_ESPERA_INVALIDA)
            return None
        return valor

    def intervalo(self, valor: object, onde: str) -> int:
        if not _milissegundos(valor):
            self.anotar(f"{onde}.intervalo_ms", CENA_INTERVALO_INVALIDO)
            return INTERVALO_PADRAO_MS
        return valor


def valor_valido(acao: str, valor: object) -> bool:
    """The value against what the action takes, and nothing wider."""
    if acao in SEM_VALOR:
        return valor is None
    if acao == "volume":
        return type(valor) is int and mapa.VALOR_MINIMO <= valor <= mapa.VALOR_MAXIMO
    if acao == "temperatura":
        return type(valor) is int and TEMPERATURA_MINIMA <= valor <= TEMPERATURA_MAXIMA
    if acao == "mudo":
        return type(valor) is bool
    if acao == "tecla":
        return isinstance(valor, str) and valor in TECLAS
    if acao == "vento":
        return isinstance(valor, str) and valor in VENTOS
    if acao == ACAO_GRUPO:
        # The empty value is solo, so a scene can take a group down by name.
        return isinstance(valor, str) and (valor == "" or _texto(valor, IDENTIDADE_MAXIMA))
    return acao in COM_TEXTO and _texto(valor, VALOR_TEXTO_MAXIMO)


def _milissegundos(valor: object) -> bool:
    # The JSON true is an int for Python, and it is not a millisecond count.
    return type(valor) is int and 0 <= valor <= ESPERA_MAXIMA_MS


def _texto(valor: object, maximo: int) -> bool:
    return (
        isinstance(valor, str)
        and 0 < len(valor) <= maximo
        and not _CONTROLE.search(valor)
        and _gravavel(valor)
    )


def _gravavel(texto: str) -> bool:
    """False for the lone surrogate JSON accepts and UTF-8 cannot write back to the bridge."""
    try:
        texto.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _causa(erro: BaseException) -> str:
    return str(erro) or type(erro).__name__

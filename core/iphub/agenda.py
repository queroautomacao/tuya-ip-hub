# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Schedules: a scene at a time of day, on the days of the week the owner picked.

A schedule is DATA, the way a scene is: one scene number, one time, the days, on or off. The
scene is what does things; the schedule only says when. Nothing here waits for the internet
or for the platform, which is the point: the radio at seven and everything off at eleven
keep happening on a house whose link is down.

The clock is the wall clock of the installation, in the time zone the owner set (or the one
of the box when none is set), and a schedule fires ONCE in its minute: a daemon that was
down at that minute does not run it late, because a scene that turns everything off at
eleven must not turn everything off at half past seven the next morning when the box boots.
"""

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from iphub.dpbus import mapa

log = logging.getLogger("iphub.agenda")

# One per scene is plenty, and thirty two keeps the file from growing into a calendar.
MAXIMO = 32
# How often the clock is looked at; well inside a minute, so no minute is skipped.
INTERVALO_S = 20.0
# Monday is 0 and Sunday is 6, the way datetime.weekday answers.
DIAS = tuple(range(7))
FUSO_MAXIMO = 64

CAMPO = "agendamentos"
CHAVES = ("cena", "hora", "dias", "ativo")

AGENDAMENTOS_NAO_LISTA = "agendamentos_nao_lista"
AGENDAMENTOS_DEMAIS = "agendamentos_demais"
AGENDAMENTO_NAO_OBJETO = "agendamento_nao_objeto"
AGENDAMENTO_CHAVE_DESCONHECIDA = "agendamento_chave_desconhecida"
AGENDAMENTO_CENA_INVALIDA = "agendamento_cena_invalida"
AGENDAMENTO_HORA_INVALIDA = "agendamento_hora_invalida"
AGENDAMENTO_DIAS_INVALIDOS = "agendamento_dias_invalidos"
AGENDAMENTO_ATIVO_INVALIDO = "agendamento_ativo_invalido"
FUSO_INVALIDO = "fuso_invalido"

_HORA = re.compile(r"([01][0-9]|2[0-3]):([0-5][0-9])")
_NOME_DE_FUSO = re.compile(r"[A-Za-z0-9_+\-/]{1,64}")


@dataclass(frozen=True)
class Agendamento:
    cena: int
    hora: str
    dias: tuple[int, ...]
    ativo: bool = True


class AgendamentosInvalidos(ValueError):
    """Every problem of the list, as (campo, codigo), the way the scenes answer."""

    def __init__(self, problemas: tuple[tuple[str, str], ...]) -> None:
        self.problemas = problemas
        super().__init__(", ".join(f"{campo}: {codigo}" for campo, codigo in problemas))


def fuso_valido(nome: object) -> bool:
    """An IANA name the box knows, or the empty string for the zone of the box itself."""
    if nome == "":
        return True
    if not isinstance(nome, str) or not _NOME_DE_FUSO.fullmatch(nome):
        return False
    try:
        ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        return False
    return True


def zona(nome: str) -> tzinfo:
    if nome and fuso_valido(nome):
        return ZoneInfo(nome)
    # The zone of the box: what TZ says in the container, UTC when nothing does.
    return datetime.now().astimezone().tzinfo or ZoneInfo("UTC")


def validar(dados: object) -> tuple[Agendamento, ...]:
    """The list as typed data, or AgendamentosInvalidos with every problem at once."""
    if not isinstance(dados, list):
        raise AgendamentosInvalidos(((CAMPO, AGENDAMENTOS_NAO_LISTA),))
    if len(dados) > MAXIMO:
        raise AgendamentosInvalidos(((CAMPO, AGENDAMENTOS_DEMAIS),))
    problemas: list[tuple[str, str]] = []
    lidos: list[Agendamento] = []
    for indice, item in enumerate(dados):
        onde = f"{CAMPO}[{indice}]"
        if not isinstance(item, dict):
            problemas.append((onde, AGENDAMENTO_NAO_OBJETO))
            continue
        if not set(item) <= set(CHAVES):
            problemas.append((onde, AGENDAMENTO_CHAVE_DESCONHECIDA))
            continue
        cena = item.get("cena")
        hora = item.get("hora")
        dias = item.get("dias")
        ativo = item.get("ativo", True)
        ok = True
        if type(cena) is not int or not 1 <= cena <= mapa.CENAS:
            problemas.append((f"{onde}.cena", AGENDAMENTO_CENA_INVALIDA))
            ok = False
        if not isinstance(hora, str) or not _HORA.fullmatch(hora):
            problemas.append((f"{onde}.hora", AGENDAMENTO_HORA_INVALIDA))
            ok = False
        if (
            not isinstance(dias, list)
            or not dias
            or any(type(dia) is not int or dia not in DIAS for dia in dias)
            or len(set(dias)) != len(dias)
        ):
            problemas.append((f"{onde}.dias", AGENDAMENTO_DIAS_INVALIDOS))
            ok = False
        if not isinstance(ativo, bool):
            problemas.append((f"{onde}.ativo", AGENDAMENTO_ATIVO_INVALIDO))
            ok = False
        if ok:
            lidos.append(Agendamento(cena=cena, hora=hora, dias=tuple(sorted(dias)), ativo=ativo))
    if problemas:
        raise AgendamentosInvalidos(tuple(problemas))
    return tuple(lidos)


def devidos(agendamentos: Sequence[Agendamento], momento: datetime) -> list[int]:
    """The positions of the schedules due at this minute of this day."""
    hora = momento.strftime("%H:%M")
    dia = momento.weekday()
    return [
        indice
        for indice, agendamento in enumerate(agendamentos)
        if agendamento.ativo and agendamento.hora == hora and dia in agendamento.dias
    ]


class Despertador:
    """Looks at the clock every few seconds and runs what is due, once per minute."""

    def __init__(
        self,
        obter: Callable[[], Sequence[Agendamento]],
        obter_fuso: Callable[[], str],
        executar: Callable[[int], str | None],
        agora: Callable[[], float] = time.time,
        intervalo_s: float = INTERVALO_S,
    ) -> None:
        self._obter = obter
        self._obter_fuso = obter_fuso
        self._executar = executar
        self._agora = agora
        self._intervalo_s = intervalo_s
        self._tarefa: asyncio.Task | None = None
        self._minuto = ""
        self._disparados: set[int] = set()

    def momento(self) -> datetime:
        return datetime.fromtimestamp(self._agora(), zona(self._obter_fuso()))

    def tique(self) -> list[int]:
        """One look at the clock; answers the scene numbers it fired."""
        momento = self.momento()
        minuto = momento.strftime("%Y-%m-%d %H:%M")
        if minuto != self._minuto:
            self._minuto = minuto
            self._disparados.clear()
        agendamentos = tuple(self._obter())
        hora_local = momento.strftime("%H:%M")
        cenas: list[int] = []
        for indice in devidos(agendamentos, momento):
            if indice in self._disparados:
                continue
            self._disparados.add(indice)
            cena = agendamentos[indice].cena
            codigo = self._executar(cena)
            if codigo is None:
                log.info("schedule %d ran scene %d at %s", indice + 1, cena, hora_local)
                cenas.append(cena)
            else:
                log.warning("schedule %d could not run scene %d: %s", indice + 1, cena, codigo)
        return cenas

    async def iniciar(self) -> None:
        if self._tarefa is None:
            self._tarefa = asyncio.create_task(self._laco())

    async def parar(self) -> None:
        tarefa, self._tarefa = self._tarefa, None
        if tarefa is not None:
            tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarefa

    async def _laco(self) -> None:
        while True:
            try:
                self.tique()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("the schedules failed a tick")
            await asyncio.sleep(self._intervalo_s)

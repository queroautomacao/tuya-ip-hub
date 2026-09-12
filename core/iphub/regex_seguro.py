# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Every read regex runs outside this process, with a deadline and a kill.

`re` takes no timeout and does not release the GIL, so one pattern with catastrophic
backtracking freezes the whole daemon: the poll, the panel and the API with it. A
heuristic for "dangerous pattern" is a losing game, because it catches the nested
quantifier and lets the overlapping alternation through. What works is running the match
in a process that can be killed when it blows the deadline, plus refusing the pattern when
the driver is saved, by running it against an input that makes any catastrophic pattern
take seconds.
"""

import asyncio
import logging
import multiprocessing as mp
import re
import threading
import time
from collections.abc import Callable
from multiprocessing.connection import Connection
from multiprocessing.process import BaseProcess

log = logging.getLogger("iphub.regex_seguro")

# Far above the microseconds a sane pattern takes on a device answer, far below the
# damage a frozen daemon does; a read that needs more than this is a read to abandon.
PRAZO_S = 0.25

# The deadline defends against backtracking, not against a cold interpreter, and on
# an ARM board starting one costs more than the deadline itself; measuring the startup
# with PRAZO_S would kill every worker at birth and no device would ever be read.
ARRANQUE_S = 10.0

MAX_TEXTO = 8 * 1024

# A pattern that survives the fire test and is still slow against what a real device
# sends costs a killed worker plus a fresh interpreter on EVERY read, which on an ARM board is
# the poll of every declarative device; one deadline is a hiccup, this many in a row is a
# pattern that cannot be read here.
ESTOUROS_ATE_QUARENTENA = 2

# Long enough for the cost to leave the poll, short enough for a device that started
# answering something shorter to be read again without anyone restarting the hub.
QUARENTENA_S = 300.0

# How long the shutdown waits for the worker to end on its own before killing it.
PARADA_S = 1.0

# The input that blows any catastrophic pattern in seconds, used when the driver is saved.
PROVA_DE_FOGO = "a" * 40 + "!"

PRONTO = "pronto"
ERRO = "erro"

# The answer of a read the deadline killed, which None cannot say: a worker that never started
# and a pattern that blew the deadline are the same None to whoever asked, and only the second
# one says anything about the pattern.
_ESTOUROU = object()

type Grupos = list[str | None]
type Relogio = Callable[[], float]


def compilavel(padrao: object) -> bool:
    """True when `re` accepts the pattern at all; a typo is a bad driver, not a slow one."""
    if not isinstance(padrao, str):
        return False
    try:
        re.compile(padrao)
    except Exception:
        # A hand written pattern breaks the compiler in more ways than re.error, deep
        # nesting raises RecursionError, and this function exists so none of them reaches
        # the validation that calls it.
        return False
    return True


def _resposta(pedido: object) -> Grupos | tuple[str, str]:
    """Runs in the worker: the groups of the match, [] for no match, an error as a tuple."""
    try:
        padrao, texto = pedido  # type: ignore[misc]
        casamento = re.search(padrao, texto)
    except Exception as erro:
        # A tuple, never a list, because the parent tells the two apart by type and a
        # match whose first group is the word erro must not read as a failure.
        return (ERRO, str(erro)[:200])
    return list(casamento.groups()) if casamento else []


def _trabalhador(conexao: Connection) -> None:
    """The child process: announce readiness, then one search per request, forever."""
    try:
        conexao.send(PRONTO)
        while True:
            pedido = conexao.recv()
            if pedido is None:
                return
            conexao.send(_resposta(pedido))
    except (EOFError, OSError):
        return


class RegexSeguro:
    """One worker, serialized by a lock, born again whenever a deadline kills it, and a
    pattern that keeps killing it put aside so the rebirth is not paid on every read.

    The volume is one read per declarative device every ten seconds, so the serialization
    never shows, and one worker is one extra process on the board.
    """

    def __init__(
        self,
        prazo_s: float = PRAZO_S,
        arranque_s: float = ARRANQUE_S,
        *,
        relogio: Relogio = time.monotonic,
    ) -> None:
        self.prazo_s = prazo_s
        self.arranque_s = arranque_s
        self._relogio = relogio
        # Fork copies a daemon that already has threads (the executor, the poll loop)
        # and inherits their locks half held, which is a lottery; spawn starts clean.
        self._ctx = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._proc: BaseProcess | None = None
        self._pipe: Connection | None = None
        self._estouros: dict[str, int] = {}
        self._quarentena: dict[str, float] = {}

    def buscar(self, padrao: str, texto: str) -> Grupos | None:
        """The groups of the match, [] for nothing to read, None for a deadline or a
        pattern `re` refuses. Blocks up to prazo_s: from the loop, use buscar_async.

        A pattern that keeps blowing the deadline stops being asked for QUARENTENA_S, and
        answers None without costing anything.
        """
        if not isinstance(padrao, str) or not isinstance(texto, str):
            return None
        # A device that answers a megabyte would pay a megabyte of backtracking on
        # every poll, and a state line that needs more than this ceiling is not a state line.
        texto = texto[:MAX_TEXTO]
        with self._lock:
            if self._calado(padrao):
                return None
            resposta = self._trocar(padrao, texto)
            self._contabilizar(padrao, resposta)
        # [] covers both no match and a pattern with no capture group; the validation
        # refuses the pattern with no group when the driver is saved, so a read that finds
        # nothing and a read that has nothing to give are the same answer here.
        return resposta if isinstance(resposta, list) else None

    async def buscar_async(self, padrao: str, texto: str) -> Grupos | None:
        """The same search off the event loop, so a deadline never stalls the daemon."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.buscar, padrao, texto)

    def perigosa(self, padrao: str) -> bool:
        """The fire test, run when a driver is saved: True means refuse the driver."""
        # An uncompilable pattern is a different defect with a different code, and
        # calling it dangerous would send the integrator hunting backtracking in a typo.
        if not compilavel(padrao):
            return False
        # The fire test judges a file somebody is saving and always runs. A pattern the
        # reads put in quarantine would be refused without being tried, and a pattern that
        # blows the fire test is refused anyway, so it never reaches a read to be counted.
        with self._lock:
            resposta = self._trocar(padrao, PROVA_DE_FOGO)
        return not isinstance(resposta, list)

    def fechar(self) -> None:
        """Asks the worker to end, then kills whatever is left; a later search reopens it."""
        with self._lock:
            proc = self._proc
            if self._pipe is not None:
                try:
                    self._pipe.send(None)
                except (EOFError, OSError, ValueError):
                    pass
            if proc is not None:
                proc.join(PARADA_S)
            self._matar()

    def _calado(self, padrao: str) -> bool:
        """True while a pattern that kept blowing the deadline is not asked again."""
        ate = self._quarentena.get(padrao)
        if ate is None:
            return False
        if self._relogio() < ate:
            return True
        del self._quarentena[padrao]
        self._estouros.pop(padrao, None)
        return False

    def _contabilizar(self, padrao: str, resposta: object) -> None:
        """Counts the deadlines a pattern blows in a row, and quarantines it once, out loud."""
        if isinstance(resposta, list):
            self._estouros.pop(padrao, None)
            return
        if resposta is not _ESTOUROU:
            # A worker that did not start, or a pipe that broke, says nothing about the
            # pattern, and counting it would silence a pattern that reads fine.
            return
        estouros = self._estouros.get(padrao, 0) + 1
        self._estouros[padrao] = estouros
        if estouros < ESTOUROS_ATE_QUARENTENA:
            return
        self._quarentena[padrao] = self._relogio() + QUARENTENA_S
        log.error(
            "regex blew the deadline %d times in a row and is not asked again for %.0fs: %r",
            estouros,
            QUARENTENA_S,
            padrao,
        )

    def _trocar(self, padrao: str, texto: str) -> object:
        if not self._garantir():
            return None
        pipe = self._pipe
        try:
            pipe.send((padrao, texto))
            if not pipe.poll(self.prazo_s):
                log.warning(
                    "regex blew the %.2fs deadline and was killed: %r", self.prazo_s, padrao
                )
                self._matar()
                return _ESTOUROU
            return pipe.recv()
        except (EOFError, OSError, ValueError):
            self._matar()
            return None

    def _garantir(self) -> bool:
        """Keeps a live worker, so one killed pattern never costs the reads of the others."""
        if self._proc is not None and self._proc.is_alive():
            return True
        self._matar()
        pai, filho = self._ctx.Pipe()
        proc = self._ctx.Process(target=_trabalhador, args=(filho,), daemon=True, name="regex")
        proc.start()
        filho.close()
        self._proc, self._pipe = proc, pai
        try:
            if pai.poll(self.arranque_s) and pai.recv() == PRONTO:
                return True
        except (EOFError, OSError, ValueError):
            pass
        log.error("the regex worker did not start within %.1fs", self.arranque_s)
        self._matar()
        return False

    def _matar(self) -> None:
        if self._pipe is not None:
            try:
                self._pipe.close()
            except OSError:
                pass
        if self._proc is not None and self._proc.is_alive():
            self._proc.kill()
            self._proc.join(PARADA_S)
        self._proc, self._pipe = None, None


_instancia: RegexSeguro | None = None
_validacao: RegexSeguro | None = None
_trava = threading.Lock()


def instancia() -> RegexSeguro:
    """The worker every READ goes through: the loader and the engine share it."""
    global _instancia
    with _trava:
        if _instancia is None:
            _instancia = RegexSeguro()
        return _instancia


def instancia_validacao() -> RegexSeguro:
    """The worker the panel judges a file with, which is not the one the polls read through.

    A file being typed can carry a catastrophic pattern per line, and each one costs a killed
    worker plus a fresh interpreter. On the shared worker that bill is paid by the poll of
    every device on the installation, so the fire test of a file that is not saved yet gets a
    worker of its own. It is born on the first validation, so a hub whose panel never opens
    the driver editor never carries the second process.
    """
    global _validacao
    with _trava:
        if _validacao is None:
            _validacao = RegexSeguro()
        return _validacao


def fechar_instancia() -> None:
    """Closes the shared workers on shutdown; the next instancia builds another."""
    global _instancia, _validacao
    with _trava:
        atual, _instancia = _instancia, None
        validacao, _validacao = _validacao, None
    for leitor in (atual, validacao):
        if leitor is not None:
            leitor.fechar()

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7: every read regex runs outside this process, with a deadline and a kill.

`re` takes no timeout and does not release the GIL, so one pattern with catastrophic
backtracking freezes the whole daemon: the poll, the panel and the API with it. A
heuristic for "dangerous pattern" is a losing game, because it catches the nested
quantifier and lets the overlapping alternation through. What works is running the match
in a process that can be killed when it blows the deadline, plus refusing the pattern when
the driver is saved, by running it against an input that makes any catastrophic pattern
take seconds.

Seção 7: toda regex de leitura roda fora deste processo, com prazo e com morte.

O `re` não aceita timeout e não solta a GIL, então um padrão com retrocesso catastrófico
congela o daemon inteiro: o poll, o painel e a API junto. Heurística de "padrão perigoso"
é jogo perdido, porque pega o quantificador aninhado e deixa passar a alternância
sobreposta. O que funciona é rodar o casamento num processo que pode ser morto quando
estoura o prazo, e recusar o padrão quando o driver é salvo, rodando-o contra uma entrada
que faz qualquer padrão catastrófico levar segundos.
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

# Why: far above the microseconds a sane pattern takes on a device answer, far below the
# damage a frozen daemon does; a read that needs more than this is a read to abandon.
# Por que: bem acima dos microssegundos que um padrão são leva sobre a resposta de um
# aparelho, bem abaixo do dano de um daemon congelado; leitura que precisa de mais que
# isso é leitura para abandonar.
PRAZO_S = 0.25

# Why: the deadline defends against backtracking, not against a cold interpreter, and on
# an ARM board starting one costs more than the deadline itself; measuring the startup
# with PRAZO_S would kill every worker at birth and no device would ever be read.
# Por que: o prazo defende de retrocesso, não de interpretador frio, e numa placa ARM
# subir um custa mais que o próprio prazo; medir o arranque com PRAZO_S mataria todo
# trabalhador ao nascer e nenhum aparelho jamais seria lido.
ARRANQUE_S = 10.0

MAX_TEXTO = 8 * 1024

# Why: a pattern that survives the fire test and is still slow against what a real device
# sends costs a killed worker plus a fresh interpreter on EVERY read, which on an ARM board is
# the poll of every declarative device; one deadline is a hiccup, this many in a row is a
# pattern that cannot be read here.
# Por que: um padrão que passa na prova de fogo e ainda é lento contra o que um aparelho real
# manda custa um trabalhador morto mais um interpretador novo a CADA leitura, o que numa placa
# ARM é o poll de todo aparelho declarativo; um prazo estourado é soluço, esta quantidade
# seguida é um padrão que não se lê aqui.
ESTOUROS_ATE_QUARENTENA = 2

# Why: long enough for the cost to leave the poll, short enough for a device that started
# answering something shorter to be read again without anyone restarting the hub.
# Por que: longo o bastante para o custo sair do poll, curto o bastante para um aparelho que
# passou a responder algo mais curto ser lido de novo sem ninguém reiniciar o hub.
QUARENTENA_S = 300.0

# How long the shutdown waits for the worker to end on its own before killing it.
# Quanto o desligamento espera o trabalhador acabar sozinho antes de matá-lo.
PARADA_S = 1.0

# The input that blows any catastrophic pattern in seconds, used when the driver is saved.
# A entrada que estoura qualquer padrão catastrófico em segundos, usada ao salvar o driver.
PROVA_DE_FOGO = "a" * 40 + "!"

PRONTO = "pronto"
ERRO = "erro"

# The answer of a read the deadline killed, which None cannot say: a worker that never started
# and a pattern that blew the deadline are the same None to whoever asked, and only the second
# one says anything about the pattern.
# A resposta de uma leitura que o prazo matou, o que o None não sabe dizer: um trabalhador que
# não subiu e um padrão que estourou o prazo são o mesmo None para quem pediu, e só o segundo
# diz alguma coisa sobre o padrão.
_ESTOUROU = object()

type Grupos = list[str | None]
type Relogio = Callable[[], float]


def compilavel(padrao: object) -> bool:
    """True when `re` accepts the pattern at all; a typo is a bad driver, not a slow one.

    Verdadeiro quando o `re` aceita o padrão; um erro de digitação é um driver ruim, não
    um driver lento.
    """
    if not isinstance(padrao, str):
        return False
    try:
        re.compile(padrao)
    except Exception:
        # Why: a hand written pattern breaks the compiler in more ways than re.error, deep
        # nesting raises RecursionError, and this function exists so none of them reaches
        # the validation that calls it.
        # Por que: um padrão escrito na mão quebra o compilador de mais jeitos que
        # re.error, aninhamento fundo estoura RecursionError, e esta função existe para
        # nenhum deles chegar à validação que a chama.
        return False
    return True


def _resposta(pedido: object) -> Grupos | tuple[str, str]:
    """Runs in the worker: the groups of the match, [] for no match, an error as a tuple.

    Roda no trabalhador: os grupos do casamento, [] se não casou, um erro como tupla.
    """
    try:
        padrao, texto = pedido  # type: ignore[misc]
        casamento = re.search(padrao, texto)
    except Exception as erro:
        # Why: a tuple, never a list, because the parent tells the two apart by type and a
        # match whose first group is the word erro must not read as a failure.
        # Por que: uma tupla, nunca uma lista, porque o pai distingue as duas pelo tipo e
        # um casamento cujo primeiro grupo é a palavra erro não pode virar falha.
        return (ERRO, str(erro)[:200])
    return list(casamento.groups()) if casamento else []


def _trabalhador(conexao: Connection) -> None:
    """The child process: announce readiness, then one search per request, forever.

    O processo filho: anuncia que está pronto, depois uma busca por pedido, para sempre.
    """
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

    Um trabalhador, serializado por um lock, renascido sempre que um prazo o mata, e um padrão
    que insiste em matá-lo posto de lado para o renascimento não ser pago a cada leitura.

    O volume é uma leitura por aparelho declarativo a cada dez segundos, então a
    serialização nunca aparece, e um trabalhador é um processo a mais na placa.
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
        # Why: fork copies a daemon that already has threads (the executor, the poll loop)
        # and inherits their locks half held, which is a lottery; spawn starts clean.
        # Por que: o fork copia um daemon que já tem threads (o executor, o laço de poll) e
        # herda os locks deles pela metade, o que é loteria; o spawn começa limpo.
        self._ctx = mp.get_context("spawn")
        self._lock = threading.Lock()
        self._proc: BaseProcess | None = None
        self._pipe: Connection | None = None
        self._estouros: dict[str, int] = {}
        self._quarentena: dict[str, float] = {}

    def buscar(self, padrao: str, texto: str) -> Grupos | None:
        """The groups of the match, [] for nothing to read, None for a deadline or a
        pattern `re` refuses. Blocks up to prazo_s: from the loop, use buscar_async.

        Os grupos do casamento, [] quando não há o que ler, None para prazo estourado ou
        padrão que o `re` recusa. Bloqueia até prazo_s: do loop, use o buscar_async.

        A pattern that keeps blowing the deadline stops being asked for QUARENTENA_S, and
        answers None without costing anything.

        Um padrão que insiste em estourar o prazo para de ser perguntado por QUARENTENA_S, e
        responde None sem custar nada.
        """
        if not isinstance(padrao, str) or not isinstance(texto, str):
            return None
        # Why: a device that answers a megabyte would pay a megabyte of backtracking on
        # every poll, and a state line that needs more than this ceiling is not a state line.
        # Por que: um aparelho que responde um megabyte pagaria um megabyte de retrocesso a
        # cada poll, e uma linha de estado que precisa de mais que este teto não é estado.
        texto = texto[:MAX_TEXTO]
        with self._lock:
            if self._calado(padrao):
                return None
            resposta = self._trocar(padrao, texto)
            self._contabilizar(padrao, resposta)
        # Why: [] covers both no match and a pattern with no capture group; the validation
        # refuses the pattern with no group when the driver is saved, so a read that finds
        # nothing and a read that has nothing to give are the same answer here.
        # Por que: [] cobre não casar e padrão sem grupo de captura; a validação recusa o
        # padrão sem grupo ao salvar o driver, então leitura que não achou e leitura que não
        # tem o que dar são a mesma resposta aqui.
        return resposta if isinstance(resposta, list) else None

    async def buscar_async(self, padrao: str, texto: str) -> Grupos | None:
        """The same search off the event loop, so a deadline never stalls the daemon.

        A mesma busca fora do laço de eventos, para um prazo nunca travar o daemon.
        """
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self.buscar, padrao, texto)

    def perigosa(self, padrao: str) -> bool:
        """The fire test, run when a driver is saved: True means refuse the driver.

        A prova de fogo, rodada ao salvar um driver: True significa recusar o driver.
        """
        # Why: an uncompilable pattern is a different defect with a different code, and
        # calling it dangerous would send the integrator hunting backtracking in a typo.
        # Por que: um padrão que não compila é outro defeito com outro código, e chamá-lo
        # de perigoso mandaria o integrador caçar retrocesso num erro de digitação.
        if not compilavel(padrao):
            return False
        # Why: the fire test judges a file somebody is saving and always runs. A pattern the
        # reads put in quarantine would be refused without being tried, and a pattern that
        # blows the fire test is refused anyway, so it never reaches a read to be counted.
        # Por que: a prova de fogo julga um arquivo que alguém está salvando e roda sempre. Um
        # padrão que as leituras puseram em quarentena seria recusado sem ser tentado, e um
        # padrão que estoura a prova de fogo é recusado de todo jeito, então ele nunca chega a
        # uma leitura para ser contado.
        with self._lock:
            resposta = self._trocar(padrao, PROVA_DE_FOGO)
        return not isinstance(resposta, list)

    def fechar(self) -> None:
        """Asks the worker to end, then kills whatever is left; a later search reopens it.

        Pede ao trabalhador que acabe, depois mata o que sobrar; uma busca depois o reabre.
        """
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
        """True while a pattern that kept blowing the deadline is not asked again.

        Verdadeiro enquanto um padrão que insistiu em estourar o prazo não é perguntado de novo.
        """
        ate = self._quarentena.get(padrao)
        if ate is None:
            return False
        if self._relogio() < ate:
            return True
        del self._quarentena[padrao]
        self._estouros.pop(padrao, None)
        return False

    def _contabilizar(self, padrao: str, resposta: object) -> None:
        """Counts the deadlines a pattern blows in a row, and quarantines it once, out loud.

        Conta os prazos que um padrão estoura seguidos, e o põe em quarentena uma vez, alto.
        """
        if isinstance(resposta, list):
            self._estouros.pop(padrao, None)
            return
        if resposta is not _ESTOUROU:
            # Why: a worker that did not start, or a pipe that broke, says nothing about the
            # pattern, and counting it would silence a pattern that reads fine.
            # Por que: um trabalhador que não subiu, ou um pipe que quebrou, não diz nada sobre
            # o padrão, e contar isso calaria um padrão que lê bem.
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
        """Keeps a live worker, so one killed pattern never costs the reads of the others.

        Mantém um trabalhador vivo, para um padrão morto nunca custar a leitura dos outros.
        """
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
    """The worker every READ goes through: the loader and the engine share it.

    O trabalhador por onde passa toda LEITURA: o carregador e o motor compartilham ele.
    """
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

    O trabalhador com que o painel julga um arquivo, que não é aquele por onde os polls leem.

    Um arquivo sendo digitado pode levar um padrão catastrófico por linha, e cada um custa um
    trabalhador morto mais um interpretador novo. No trabalhador compartilhado essa conta é
    paga pelo poll de todo aparelho da instalação, então a prova de fogo de um arquivo que
    ainda não foi salvo ganha um trabalhador próprio. Ele nasce na primeira validação, então um
    hub cujo painel nunca abre o editor de drivers nunca carrega o segundo processo.
    """
    global _validacao
    with _trava:
        if _validacao is None:
            _validacao = RegexSeguro()
        return _validacao


def fechar_instancia() -> None:
    """Closes the shared workers on shutdown; the next instancia() builds another.

    Fecha os trabalhadores compartilhados no desligamento; o instancia() seguinte faz outro.
    """
    global _instancia, _validacao
    with _trava:
        atual, _instancia = _instancia, None
        validacao, _validacao = _validacao, None
    for leitor in (atual, validacao):
        if leitor is not None:
            leitor.fechar()

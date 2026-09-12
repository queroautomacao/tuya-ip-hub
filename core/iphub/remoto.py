# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The window of the remote access: twenty four hours, armed by the owner, closed by itself.

The rule the whole feature hangs on is that the door is CLOSED by default and closes again on
its own. So the state kept here is not a flag saying open, it is the INSTANT it ends: a flag
would have to be turned off by something that remembers to, and an instant in the past is
already closed even if nothing ran, even if the daemon was down when it expired, even if the
clock of the machine jumped.

What this module refuses:

- a deadline further away than one window. A file edited by hand, or a clock that ran
  backwards while the file was written, cannot buy more than the twenty four hours the owner
  can grant by pressing the button;
- arming without the three things the door needs: a relay to dial, the second factor
  enrolled, and a password that meets the current minimum. The check lives with the caller,
  which has the configuration; this module owns the clock and the file.

The service at the bottom is the piece that reconciles: every few seconds it looks at the
window and at the socket, and makes the second agree with the first. A socket that died
comes back while the window is open, and a window that expired takes the socket down without
anyone having scheduled a callback for it.
"""

import asyncio
import contextlib
import logging
import secrets
import time
from collections.abc import Callable
from pathlib import Path

from iphub.arquivos import escrever_json, ler_json
from iphub.versao import SCHEMA_VERSION

log = logging.getLogger("iphub.remoto")

ARQUIVO = "remoto.json"
JANELA_S = 24 * 3600
# How often the service compares the window with the process. A window that ends between
# two ticks stays open for at most this long, and the tick costs nothing.
INTERVALO_S = 5.0

# The code of the window: what the app shows to whoever armed it, and what the remote login
# asks for. It exists because the identity this hub really has of the integrator is the one
# that armed the window through the app of the platform, and not a list of e-mail addresses
# in somebody's dashboard. It is typed from the screen of a phone into another screen, so the
# alphabet carries no character that can be read as another one (no 0 and O, no 1 and I and
# L) and no U, which is what keeps a random code from spelling something nobody wants to read
# out loud. Eight characters of the thirty are thirty nine bits that live for one day.
ALFABETO = "23456789ABCDEFGHJKMNPQRSTVWXYZ"
LETRAS_DO_CODIGO = 8
_NAO_DO_ALFABETO = str.maketrans("", "", " -\t")


def gerar_codigo() -> str:
    bruto = "".join(secrets.choice(ALFABETO) for _ in range(LETRAS_DO_CODIGO))
    # Written in two halves, which is how a person reads eight characters out loud.
    return f"{bruto[:4]}-{bruto[4:]}"


def _normalizar(bruto: object) -> str:
    if not isinstance(bruto, str):
        return ""
    return bruto.translate(_NAO_DO_ALFABETO).upper()


class Janela:
    """When the remote door closes, kept as an instant and never as a flag."""

    def __init__(self, dir_data: Path, agora: Callable[[], float] = time.time) -> None:
        self._caminho = Path(dir_data) / ARQUIVO
        self._agora = agora
        self._fim, self._codigo = self._ler()

    def armar(self) -> int:
        """Opens a full window from now, with a code of its own, whatever was left before."""
        self._fim = self._agora() + JANELA_S
        # A new window is a new code: the one of the window that ended stops working the
        # moment this one starts, so a code that leaked buys a day and never two.
        self._codigo = gerar_codigo()
        self._gravar()
        return self.restante_s()

    def desarmar(self) -> None:
        self._fim = 0.0
        self._codigo = ""
        self._gravar()

    def codigo(self) -> str:
        """The code of the open window, and nothing at all when it is closed."""
        return self._codigo if self.ativa() else ""

    def confere(self, informado: object) -> bool:
        """Whether this is the code of the window that is open right now."""
        atual = self.codigo()
        if not atual:
            return False
        return secrets.compare_digest(_normalizar(atual), _normalizar(informado))

    def ativa(self) -> bool:
        return self.restante_s() > 0

    def restante_s(self) -> int:
        return max(0, int(self._fim - self._agora()))

    def fim(self) -> float:
        return self._fim

    def _ler(self) -> tuple[float, str]:
        try:
            dados = ler_json(self._caminho)
        except (OSError, ValueError):
            # A file nobody can read is a door nobody can prove is open, so it is closed.
            return 0.0, ""
        if not isinstance(dados, dict) or dados.get("schema_version") != SCHEMA_VERSION:
            return 0.0, ""
        fim = dados.get("fim_em")
        if not isinstance(fim, (int, float)) or isinstance(fim, bool):
            return 0.0, ""
        codigo = dados.get("codigo")
        codigo = codigo if isinstance(codigo, str) else ""
        # A deadline beyond one window is a file that was edited or a clock that moved; the
        # owner can only ever grant twenty four hours, so that is all this can be worth.
        return min(float(fim), self._agora() + JANELA_S), codigo

    def _gravar(self) -> None:
        try:
            escrever_json(
                self._caminho,
                {"schema_version": SCHEMA_VERSION, "fim_em": self._fim, "codigo": self._codigo},
            )
        except OSError as erro:
            # The window still holds for the life of this process; what is lost is only
            # surviving a restart, and a door that closes on a restart errs the safe way.
            log.error("could not write %s (%s)", self._caminho.name, erro.strerror or erro)


class Servico:
    """Keeps the socket agreeing with the window, and is the only thing that opens one."""

    def __init__(
        self,
        janela: Janela,
        transporte,
        intervalo_s: float = INTERVALO_S,
    ) -> None:
        self._janela = janela
        # The transport of the remote door: one outbound socket to the relay of the maker,
        # open only while the window is. This class drives it and knows nothing about how it
        # reaches anyone.
        self._transporte = transporte
        self._intervalo_s = intervalo_s
        self._tarefa: asyncio.Task | None = None
        self._proxima_tentativa = 0.0
        self._fim_visto = janela.fim()

    @property
    def janela(self) -> Janela:
        return self._janela

    def aberto(self) -> bool:
        return self._transporte.aberto()

    def url(self) -> str:
        """The address this window is reachable at, which the relay hands over on connect."""
        obter = getattr(self._transporte, "url", None)
        return obter() if callable(obter) else ""

    def recusado(self) -> bool:
        """Whether the relay refused the licences of this hub on the last dial."""
        obter = getattr(self._transporte, "recusado", None)
        return bool(obter()) if callable(obter) else False

    async def iniciar(self) -> None:
        if self._tarefa is None:
            self._tarefa = asyncio.create_task(self._laco())

    async def parar(self) -> None:
        tarefa, self._tarefa = self._tarefa, None
        if tarefa is not None:
            tarefa.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await tarefa
        await self._transporte.fechar()

    async def reconciliar(self) -> None:
        """One comparison between what the window says and what the transport is doing."""
        transporte = self._transporte
        if self._janela.fim() != self._fim_visto:
            # A window armed again is a fresh attempt: whoever fixed the licence that the
            # relay refused must not wait out the backoff of the refusal.
            self._fim_visto = self._janela.fim()
            self._proxima_tentativa = 0.0
            transporte.recomecar_a_espera()
        if not self._janela.ativa():
            if transporte.aberto() or transporte.morreu():
                await transporte.fechar()
            transporte.recomecar_a_espera()
            return
        if transporte.aberto():
            # A transport that has been up a while is one that connected, so the next fall
            # starts the backoff from the beginning instead of from where the last one ended.
            if transporte.estavel():
                transporte.recomecar_a_espera()
            return
        if transporte.morreu():
            # A relay that is down and a link that died look the same from here, so this
            # side waits longer each time instead of hammering a service that said no.
            await transporte.fechar()
            self._proxima_tentativa = time.monotonic() + transporte.espera_s()
            transporte.adiar()
            return
        if time.monotonic() < self._proxima_tentativa:
            return
        if not await transporte.abrir():
            self._proxima_tentativa = time.monotonic() + transporte.espera_s()
            transporte.adiar()

    async def _laco(self) -> None:
        while True:
            try:
                await self.reconciliar()
            except asyncio.CancelledError:
                raise
            except Exception:
                # A bug here must not leave the socket unsupervised for the rest of the day.
                log.exception("the remote access service failed a tick")
            await asyncio.sleep(self._intervalo_s)

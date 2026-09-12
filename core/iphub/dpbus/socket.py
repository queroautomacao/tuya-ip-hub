# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 8 and 9: the WebSocket of the DP-bus, thin over the map, the protocol and the
numbers of every licence.

What a frame means was decided in protocolo.py and where a set lands was decided by the
module that owns the numbers and the scenes, so this file holds only what needs a socket: the
handshake, the first frame, who is listening for which licence, and when a report goes out.
Everything it touches of the installation arrives as a function, which is why every rule
below is tested without a speaker and without a route.

Word by word, and each word is a test that attacks it. The FIRST frame is
{"t":"auth","token":"<api_token>","licenca":"<id>"} and the token NEVER travels in the URL,
because a query string is written into every access log and into the history of whoever
pasted it; without that frame in five seconds, with a token that does not match, or with a
licence this hub does not have, the socket closes with 4401 and answers nothing else. After
that there is NO burst: the bridge asks with a consulta frame and gets the snapshot of its
licence, which is not counted as a report. Sets come in, acks and reports go out. A command
reports OPTIMISTICALLY and rereads about a second and a half later, which is the cadence the
bench measured, so the bridge sees the level it asked for at once and the truth right after;
a new command for the same data point cancels the pending verification, because the older
one would publish a state the customer already changed his mind about.

The reports, which is what keeps the daily count of the platform low:

- only what CHANGED against the last published value, never a repeat;
- class A (power, level, temperature, mode, fan, group, online) waits a window of 2 s per
data point, and the last value of the window wins;
- class B (inputs, modes, muted) waits 10 s;
- class C: profiles and names only move when the registration does, and the titles are
NEVER pushed, they only answer a consulta;
- the reports of the day are counted per licence: at 250 the class B stops and the class A
window opens to 30 s, with a warning in the log, so the cloud never gets to throttle.

Holds here exactly as it holds for /api/*: the Host rule, the Origin rule and the
four headers are the same middlewares of the gate, and the handshake response passes through
them like any other response. Nothing this module sends ever carries the api_token back.
"""

import asyncio
import contextlib
import functools
import json
import logging
import secrets
import time
from collections.abc import Awaitable, Callable

from aiohttp import WSMsgType, web

from iphub.dpbus import comando, mapa, protocolo
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.dpbus.socket")

CAMINHO = "/dpbus"

# A socket that did not authenticate is closed with this code and nothing else.
FECHAMENTO_NAO_AUTENTICADO = 4401

PRAZO_AUTH_S = 5.0

# The bench measured an ack around 30 ms and a reread at a second and a half landing on
# the state the speaker really settled into; rereading sooner reads a device still moving.
RELEITURA_S = 1.5

# A report is only ever born of real state, so something has to look at the state; this
# tick compares what the gestor already polled and sends what CHANGED, so a quiet
# installation puts nothing at all on the wire.
INTERVALO_S = 1.0

# A client that stops reading fills the kernel buffer and then send_str waits forever,
# on the single task that publishes every report and reconciles the groups. One stalled
# bridge would freeze the bus of every licence for everybody, and the frames it never took
# would grow without bound in the daemon of an appliance. A frame that does not leave in this
# many seconds means the socket is not taking frames any more, so it goes.
ENVIO_S = 2.0

# The largest honest frame is a set of the command channel, and the reader
# holds a whole message in memory before anybody looks at it; a client that sends more is
# closed by the library instead of being believed.
QUADRO_MAXIMO = 4 * 1024

SEGUNDOS_POR_DIA = 86_400

REQUER_WEBSOCKET = "requer_websocket"

# The code of a defect of ours, which the gate already answers on a 500 and the panel already
# translates; a device that refused answers one of its own.
ERRO_INTERNO = "erro_interno"

# Tells a data point that was never published from one published as a false or as a zero.
_AUSENTE = object()

type Ajuste = Callable[[str, object, object], Awaitable[str | None]]
type Fonte = Callable[[str], dict[int, object]]
type ProdutoDe = Callable[[object], str | None]
type Ids = Callable[[], tuple[str, ...]]
type Passo = Callable[[], Awaitable[None]]
type Releitura = Callable[[str, object, object], Awaitable[None]]
type Dormir = Callable[[float], Awaitable[None]]
type Relogio = Callable[[], float]
type ObterToken = Callable[[], str]


async def _nada_com_dp(_licenca: str, _dpid: object, _valor: object) -> None:
    return None


async def _nada() -> None:
    """The installation hook of a bus that was handed none, so no branch guards a call."""


def _nenhuma() -> tuple[str, ...]:
    return ()


class _Canal:
    """Everything the bus keeps about ONE licence: who listens, what was published, what is
    being verified and how many reports the day has cost.
    """

    __slots__ = ("avisado", "clientes", "dia", "publicados", "reports", "ultimos", "verificacoes")

    def __init__(self, dia: int) -> None:
        self.clientes: set[web.WebSocketResponse] = set()
        self.ultimos: dict[int, object] = {}
        self.publicados: dict[int, float] = {}
        self.verificacoes: dict[object, asyncio.Task] = {}
        self.dia = dia
        self.reports = 0
        self.avisado = False


class Barramento:
    """Holds who is listening for each licence, what was already published and the pending
    verifications.
    """

    def __init__(
        self,
        ajustar: Ajuste,
        valores: Fonte,
        obter_token: ObterToken,
        produto_de: ProdutoDe,
        *,
        licencas: Ids = _nenhuma,
        sanear: Passo = _nada,
        sincronizar: Passo = _nada,
        reler: Releitura = _nada_com_dp,
        dormir: Dormir = asyncio.sleep,
        agora: Relogio = time.time,
        prazo_auth_s: float = PRAZO_AUTH_S,
        releitura_s: float = RELEITURA_S,
        intervalo_s: float = INTERVALO_S,
        envio_s: float = ENVIO_S,
    ) -> None:
        self._ajustar = ajustar
        self._valores = valores
        self._obter_token = obter_token
        self._produto_de = produto_de
        self._licencas = licencas
        self._sanear = sanear
        self._sincronizar = sincronizar
        self._reler_dp = reler
        self._dormir = dormir
        self._agora = agora
        self._prazo_auth_s = prazo_auth_s
        self._releitura_s = releitura_s
        self._intervalo_s = intervalo_s
        self._envio_s = envio_s
        self._canais: dict[str, _Canal] = {}
        self._laco: asyncio.Task | None = None

    async def iniciar(self) -> None:
        """Boot: the zombie groups go down before anything is published."""
        await self._sanear()
        self._laco = asyncio.create_task(self._rodar(), name="dpbus:laco")

    async def parar(self) -> None:
        """Takes the loop, the verifications and the sockets off the wire, in that order."""
        laco, self._laco = self._laco, None
        tarefas = [
            tarefa for canal in self._canais.values() for tarefa in canal.verificacoes.values()
        ]
        for canal in self._canais.values():
            canal.verificacoes.clear()
        if laco is not None:
            tarefas.append(laco)
        for tarefa in tarefas:
            tarefa.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        for canal in self._canais.values():
            for cliente in tuple(canal.clientes):
                # A client parked on receive holds the handler task of its own request,
                # and the application would wait for it forever while the loop is being
                # closed.
                with contextlib.suppress(Exception):
                    await cliente.close()
            canal.clientes.clear()

    async def revogar(self) -> None:
        """The api_token was rotated, so every socket it authenticated is over.

        A socket authenticates on its first frame and is never asked again, so without
        this the documented remediation for a leaked machine credential remediates nothing:
        whoever holds the old token keeps every number of every licence for as long as the
        daemon runs, and a bridge socket is long lived by design, so it never has to
        reconnect.
        """
        for canal in self._canais.values():
            await self._fechar_clientes(canal)

    async def desligar(self, licenca: str) -> None:
        """A licence left the installation: its sockets close and its books are forgotten."""
        canal = self._canais.pop(licenca, None)
        if canal is None:
            return
        tarefas = tuple(canal.verificacoes.values())
        canal.verificacoes.clear()
        for tarefa in tarefas:
            tarefa.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        await self._fechar_clientes(canal)

    async def _fechar_clientes(self, canal: _Canal) -> None:
        # The handler task of each socket is parked reading it, so the peer may see the
        # connection drop instead of this code; what revocation guarantees is that the socket
        # is gone and answers nothing, not which of the two closes reaches the other end.
        for cliente in tuple(canal.clientes):
            with contextlib.suppress(Exception):
                await cliente.close(code=FECHAMENTO_NAO_AUTENTICADO)
        canal.clientes.clear()

    @property
    def ouvintes(self) -> int:
        """How many clients a report goes out to right now, every licence counted."""
        # A socket that went away and stayed in the set is a reference the daemon of an
        # appliance never gets back, so how many are listening has to be readable.
        return sum(len(canal.clientes) for canal in self._canais.values())

    def ouvintes_de(self, licenca: str) -> int:
        canal = self._canais.get(licenca)
        return 0 if canal is None else len(canal.clientes)

    def reports_do_dia(self, licenca: str) -> int:
        """How many reports the licence has cost today, which the panel shows."""
        canal = self._canais.get(licenca)
        if canal is None:
            return 0
        self._virar_dia(canal)
        return canal.reports

    def snapshot(self, licenca: str, identificador: object = None) -> dict:
        """Everything of one licence that may be reported right now, which answers a consulta."""
        produto = self._produto_de(licenca)
        if produto is None:
            return {"t": protocolo.T_SNAPSHOT, "id": identificador, "dps": {}}
        return protocolo.snapshot(produto, self._valores(licenca), identificador)

    async def aplicar(self, licenca: str, dpid: object, valor: object) -> str | None:
        """One set: done, or a stable code. Nothing raises out of here."""
        try:
            codigo = await self._ajustar(licenca, dpid, valor)
        except asyncio.CancelledError:
            raise
        except Exception as erro:
            # A defect of ours below this line would leave the client waiting for an ack
            # that never comes and take the socket of a whole licence down with it; the code
            # says the daemon failed, which is not the same as the equipment refusing.
            log.exception("the bus failed to set dp %r of %s: %s", dpid, licenca, _causa(erro))
            return ERRO_INTERNO
        produto = self._produto_de(licenca)
        dp = None if produto is None else mapa.de_dp(produto, dpid)
        if codigo is None and dp is not None and dp.funcao != "cena":
            await self._otimista(licenca, dp, valor)
        return codigo

    async def atender(self, request: web.Request) -> web.StreamResponse:
        """The whole life of one socket: handshake, first frame, then the sets and queries."""
        ws = web.WebSocketResponse(max_msg_size=QUADRO_MAXIMO)
        if not ws.can_prepare(request):
            # Whoever opened this address in a browser gets the stable code
            # and not the phrase the library raises, which would leave the gate as prose.
            return resposta_erro(426, REQUER_WEBSOCKET)
        await ws.prepare(request)
        licenca = await self._autenticar(ws)
        if licenca is None:
            log.warning("a dpbus client did not authenticate and was closed")
            await ws.close(code=FECHAMENTO_NAO_AUTENTICADO)
            return ws
        # There is no burst on the way up: the bridge asks with a consulta
        # and gets the slice of its licence, which is not counted as a report.
        canal = self._canal(licenca)
        canal.clientes.add(ws)
        try:
            await self._conversar(ws, licenca)
        finally:
            canal.clientes.discard(ws)
        return ws

    async def publicar(self) -> None:
        """Sends a report for every data point of every licence whose value is not the one
        already published, under the policy.
        """
        for licenca in self._licencas():
            await self._publicar(licenca)

    async def _publicar(self, licenca: str) -> None:
        produto = self._produto_de(licenca)
        if produto is None:
            return
        canal = self._canal(licenca)
        self._virar_dia(canal)
        atuais = self._valores(licenca)
        # A number whose equipment was removed stops appearing in the values at all, so
        # what was published about it is forgotten; when the number is occupied again its
        # state is new and reports again instead of being taken for the old one.
        for dpid in tuple(canal.ultimos):
            if dpid not in atuais:
                del canal.ultimos[dpid]
                canal.publicados.pop(dpid, None)
        for dpid, valor in atuais.items():
            dp = mapa.de_dp(produto, dpid)
            if dp is None or not dp.reportavel or not dp.empurrado:
                continue
            if canal.ultimos.get(dpid, _AUSENTE) == valor:
                continue
            if not self._pode(canal, dp):
                # The value is NOT recorded as published, so the first tick after the
                # window sends the last value of the window instead of losing it.
                continue
            await self._reportar(canal, dp, valor)

    def _pode(self, canal: _Canal, dp: mapa.Dp) -> bool:
        """The policy for one data point right now: its window, widened or
        closed once the day cost 250 reports.
        """
        apertado = canal.reports >= mapa.AVISO_DO_DIA
        if apertado and dp.classe is mapa.Classe.B:
            return False
        janela = dp.janela_s
        if apertado and dp.classe is mapa.Classe.A:
            janela = mapa.JANELA_APERTADA_S
        if not janela:
            return True
        ultimo = canal.publicados.get(dp.dpid)
        return ultimo is None or self._agora() - ultimo >= janela

    def _virar_dia(self, canal: _Canal) -> None:
        dia = int(self._agora() // SEGUNDOS_POR_DIA)
        if dia != canal.dia:
            canal.dia = dia
            canal.reports = 0
            canal.avisado = False

    def _canal(self, licenca: str) -> _Canal:
        canal = self._canais.get(licenca)
        if canal is None:
            canal = _Canal(int(self._agora() // SEGUNDOS_POR_DIA))
            self._canais[licenca] = canal
        return canal

    async def _autenticar(self, ws: web.WebSocketResponse) -> str | None:
        """The licence of a first frame that is an auth carrying the api_token
        and a licence this hub has, or None.
        """
        auth = protocolo.ler_auth(await self._primeiro(ws))
        esperado = self._obter_token()
        if not auth.token or not esperado:
            return None
        # Comparing with == hands whoever measures the answer the length of the common
        # prefix, and this token is the machine credential of the whole bus.
        if not secrets.compare_digest(auth.token, esperado):
            return None
        if self._produto_de(auth.licenca) is None:
            log.warning("a dpbus client named a licence this hub does not have")
            return None
        return auth.licenca

    async def _primeiro(self, ws: web.WebSocketResponse) -> object:
        """The first frame as an object, or None when the deadline won the race."""
        recebe = asyncio.ensure_future(ws.receive())
        prazo = asyncio.ensure_future(self._dormir(self._prazo_auth_s))
        try:
            await asyncio.wait({recebe, prazo}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Whichever of the two lost the race is still on the loop, and a socket that
            # is about to close must not leave a task reading it.
            pendentes = [tarefa for tarefa in (recebe, prazo) if not tarefa.done()]
            for tarefa in pendentes:
                tarefa.cancel()
            await asyncio.gather(*pendentes, return_exceptions=True)
        if not recebe.done() or recebe.cancelled():
            return None
        erro = recebe.exception()
        if erro is not None:
            log.warning("a dpbus client broke before authenticating: %s", _causa(erro))
            return None
        return _objeto(recebe.result())

    async def _conversar(self, ws: web.WebSocketResponse, licenca: str) -> None:
        """Every frame after the auth: one answer for each, and never an exception out."""
        async for mensagem in ws:
            bruto = _objeto(mensagem)
            produto = self._produto_de(licenca)
            if produto is None:
                # The licence was removed while the socket lived; nothing of it may be
                # answered any more, and the removal already closed this socket.
                return
            # An unknown frame is answered and the socket lives on, because the other end
            # is whatever bridge somebody implemented from the public contract, and one bad
            # frame must not drop a socket that is carrying a whole licence.
            leitura = protocolo.ler_quadro(bruto, produto)
            if leitura.consulta:
                log.debug("%s: consulta %s", licenca, leitura.id)
                await self._mandar(ws, self.snapshot(licenca, leitura.id))
                continue
            if leitura.pedido is None:
                log.debug("%s: quadro recusado, %s", licenca, leitura.codigo)
                await self._mandar(ws, protocolo.ack(leitura.id, leitura.codigo))
                continue
            # This line and the ack below are the two halves of what the bridge of the
            # platform asked for, and the diary of the panel is where the integrator reads
            # whether a button of the app of the customer ever reached the hub at all.
            log.debug(
                "%s: set dp %d = %r",
                licenca,
                leitura.pedido.dp.dpid,
                leitura.pedido.valor,
            )
            codigo = await self.aplicar(licenca, leitura.pedido.dp.dpid, leitura.pedido.valor)
            log.debug("%s: dp %d -> %s", licenca, leitura.pedido.dp.dpid, codigo or "ok")
            await self._mandar(ws, protocolo.ack(leitura.id, codigo))

    async def _otimista(self, licenca: str, dp: mapa.Dp, valor: object) -> None:
        """The bench cadence: publish what was asked now, reread the truth a moment later."""
        canal = self._canal(licenca)
        if dp.reportavel and dp.empurrado and canal.ultimos.get(dp.dpid, _AUSENTE) != valor:
            # The optimistic report is the one the customer is waiting for with the app
            # open, so it skips the window; the reread only reports if the device diverged. A
            # value already published is never repeated, and once the day cost 250 reports
            # the set waits the widened window like any other change, so a slider dragged
            # all afternoon cannot push the licence past the budget of the platform.
            apertado = canal.reports >= mapa.AVISO_DO_DIA
            if not apertado or self._pode(canal, dp):
                await self._reportar(canal, dp, valor)
        chave = self._chave_de_verificacao(dp, valor)
        antiga = canal.verificacoes.pop(chave, None)
        if antiga is not None:
            # A second command for the same data point makes the older verification
            # publish a state the customer already replaced, and the bridge would watch the
            # level bounce back on its own.
            antiga.cancel()
        tarefa = asyncio.create_task(
            self._reler(licenca, dp.dpid, valor), name=f"dpbus:verifica:{licenca}:{dp.dpid}"
        )
        canal.verificacoes[chave] = tarefa
        tarefa.add_done_callback(functools.partial(self._fim_da_verificacao, canal, chave))

    def _chave_de_verificacao(self, dp: mapa.Dp, valor: object) -> object:
        """One verification per data point, except the command channel, which is one per
        number: two commands to two equipment must not cancel each other's reread.
        """
        if dp.funcao != "comando":
            return dp.dpid
        lido = comando.ler(valor, mapa.NUMEROS[dp.produto])
        return (dp.dpid, 0 if lido is None else lido.numero)

    async def _reler(self, licenca: str, dpid: int, valor: object) -> None:
        """Report what was asked now, ask the device a moment later."""
        await self._dormir(self._releitura_s)
        await self._reler_dp(licenca, dpid, valor)
        await self._publicar(licenca)

    def _fim_da_verificacao(self, canal: _Canal, chave: object, tarefa: asyncio.Task) -> None:
        if canal.verificacoes.get(chave) is tarefa:
            del canal.verificacoes[chave]

    async def _rodar(self) -> None:
        """Reconciles the groups and publishes what changed, on one task."""
        while True:
            await self._dormir(self._intervalo_s)
            try:
                await self._sincronizar()
                await self.publicar()
            except asyncio.CancelledError:
                raise
            except Exception as erro:
                # This loop is the only thing publishing real state, so it never dies of
                # one bad reading; a device that answers nonsense costs a line of log.
                log.exception("the dpbus loop failed: %s", _causa(erro))

    async def _reportar(self, canal: _Canal, dp: mapa.Dp, valor: object) -> None:
        try:
            quadro = protocolo.report(dp, valor, self._agora())
        except ValueError as erro:
            log.error("dp %d does not carry that reading: %s", dp.dpid, _causa(erro))
            return
        canal.ultimos[dp.dpid] = valor
        canal.publicados[dp.dpid] = self._agora()
        log.debug("report dp %d = %r para %d ouvinte(s)", dp.dpid, valor, len(canal.clientes))
        # A report nobody is listening to never reaches the cloud, so it does not spend
        # the budget of the day; the books are still written, so a bridge that connects later
        # gets no burst of everything the hub already knew, only what changes from then on.
        if canal.clientes:
            canal.reports += 1
        if canal.reports >= mapa.AVISO_DO_DIA and not canal.avisado:
            # The platform throttles a device above 300 reports a day, and
            # this hub never lets it get there: from here on the class B stops and the class
            # A widens, and the operator reads why in the log.
            canal.avisado = True
            log.warning(
                "a licence reached %d reports today; inputs, modes and muted stop reporting "
                "and the other changes wait %d s",
                canal.reports,
                int(mapa.JANELA_APERTADA_S),
            )
        for cliente in tuple(canal.clientes):
            await self._mandar(cliente, quadro, canal)

    async def _mandar(
        self, ws: web.WebSocketResponse, quadro: dict, canal: _Canal | None = None
    ) -> None:
        try:
            async with asyncio.timeout(self._envio_s):
                await ws.send_str(_texto(quadro))
        except TimeoutError:
            log.warning("a dpbus client stopped reading and was dropped")
            self._largar(ws, canal)
            with contextlib.suppress(Exception):
                await ws.close()
        except Exception as erro:
            # A client that went away between the comparison and the send is a socket
            # that is gone and not a failure of the bus, and the other licences keep
            # publishing.
            log.debug("a dpbus client did not take a frame: %s", _causa(erro))
            self._largar(ws, canal)

    def _largar(self, ws: web.WebSocketResponse, canal: _Canal | None) -> None:
        canais = (canal,) if canal is not None else tuple(self._canais.values())
        for cada in canais:
            cada.clientes.discard(ws)


BARRAMENTO = web.AppKey("barramento", Barramento)


async def dpbus(request: web.Request) -> web.StreamResponse:
    return await request.app[BARRAMENTO].atender(request)


async def subir_barramento(app: web.Application) -> None:
    await app[BARRAMENTO].iniciar()


async def baixar_barramento(app: web.Application) -> None:
    await app[BARRAMENTO].parar()


def _objeto(mensagem: object) -> object:
    """One frame as the object it carries, or None for anything that is not JSON text."""
    if getattr(mensagem, "type", None) is not WSMsgType.TEXT:
        return None
    try:
        return json.loads(mensagem.data)
    except ValueError:
        return None


def _texto(quadro: dict) -> str:
    # Ensure_ascii would write an accented letter of a track title as six bytes, and the
    # frame is UTF-8 all the way; the compact separators keep a report small on a busy bus.
    # A lone surrogate a device answered is the one thing a str holds that UTF-8 cannot
    # write, and a frame that failed to encode dropped the socket of the whole licence.
    texto = json.dumps(quadro, ensure_ascii=False, separators=(",", ":"))
    return texto.encode("utf-8", errors="replace").decode("utf-8")


def _causa(erro: BaseException) -> str:
    return str(erro) or type(erro).__name__

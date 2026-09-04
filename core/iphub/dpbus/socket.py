# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 8 and 9: the WebSocket of the DP-bus, thin over the map, the protocol and the
blocks.

What a frame means was decided in protocolo.py and where a set lands was decided by the
module that owns the blocks and the scenes, so this file holds only what needs a socket: the
handshake, the first frame, who is listening, and when a report goes out. Everything it
touches of the installation arrives as a function, which is why every rule below is tested
without a speaker and without a route.

Section 8, word by word, and each word is a test that attacks it. The FIRST frame is
{"t":"auth","token":"<api_token>"} and the token NEVER travels in the URL, because a query
string is written into every access log and into the history of whoever pasted it; without
that frame in five seconds, or with a token that does not match, the socket closes with 4401
and answers nothing else. After that a snapshot goes out carrying only what may be reported,
sets come in, and acks and reports go out. A command reports OPTIMISTICALLY and rereads about
a second and a half later, which is the cadence the bench measured, so the bridge sees the
volume it asked for at once and the truth right after; a new command for the same data point
cancels the pending verification, because the older one would publish a state the customer
already changed his mind about.

Section 9 holds here exactly as it holds for /api/*: the Host rule, the Origin rule and the
four headers are the same middlewares of the gate, and the handshake response passes through
them like any other response. Nothing this module sends ever carries the api_token back.

Seções 8 e 9: o WebSocket do DP-bus, fino sobre o mapa, o protocolo e os blocos.

O que um quadro significa foi decidido no protocolo.py e onde um set cai foi decidido pelo
módulo dono dos blocos e das cenas, então este arquivo guarda só o que precisa de socket: o
aperto de mão, o primeiro quadro, quem está escutando, e quando um report sai. Tudo que ele
toca da instalação chega como função, e é por isso que toda regra abaixo é testada sem caixa
e sem rota.

A seção 8, palavra por palavra, e cada palavra é um teste que a ataca. O PRIMEIRO quadro é
{"t":"auth","token":"<api_token>"} e o token NUNCA viaja na URL, porque uma query string é
escrita em todo log de acesso e no histórico de quem a colou; sem esse quadro em cinco
segundos, ou com um token que não casa, o socket fecha com 4401 e não responde mais nada.
Depois disso sai um snapshot levando só o que pode ser reportado, entram sets, e saem acks e
reports. Um comando reporta OTIMISTA e relê cerca de um segundo e meio depois, que é a
cadência que a bancada mediu, então a ponte vê o volume que pediu na hora e a verdade logo em
seguida; um comando novo para o mesmo data point cancela a verificação pendente, porque a
antiga publicaria um estado sobre o qual o cliente já mudou de ideia.

A seção 9 vale aqui igual ao que vale para /api/*: a regra de Host, a regra de Origin e os
quatro cabeçalhos são os mesmos middlewares do portão, e a resposta do aperto de mão passa
por eles como qualquer outra resposta. Nada que este módulo manda leva o api_token de volta.
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

from iphub.dpbus import mapa, protocolo
from iphub.portao import resposta_erro

log = logging.getLogger("iphub.dpbus.socket")

CAMINHO = "/dpbus"

# Section 8: a socket that did not authenticate is closed with this code and nothing else.
# Seção 8: um socket que não autenticou é fechado com este código e mais nada.
FECHAMENTO_NAO_AUTENTICADO = 4401

PRAZO_AUTH_S = 5.0

# Why: the bench measured an ack around 30 ms and a reread at a second and a half landing on
# the state the speaker really settled into; rereading sooner reads a device still moving.
# Por que: a bancada mediu um ack em torno de 30 ms e uma releitura em um segundo e meio
# caindo no estado em que a caixa realmente parou; reler antes lê um aparelho ainda em
# movimento.
RELEITURA_S = 1.5

# Why: a report is only ever born of real state, so something has to look at the state; this
# tick compares what the gestor already polled and sends what CHANGED, so a quiet
# installation puts nothing at all on the wire.
# Por que: um report só nasce de estado real, então algo precisa olhar o estado; este tique
# compara o que o gestor já pesquisou e manda o que MUDOU, então uma instalação parada não põe
# nada no fio.
INTERVALO_S = 1.0

# Why: a client that stops reading fills the kernel buffer and then send_str waits forever,
# on the single task that publishes every report and reconciles the group. One stalled bridge
# would freeze the bus of six blocks for everybody, and the frames it never took would grow
# without bound in the daemon of an appliance. A frame that does not leave in this many
# seconds means the socket is not taking frames any more, so it goes.
# Por que: um cliente que para de ler enche o buffer do kernel e então o send_str espera para
# sempre, na única tarefa que publica todo report e reconcilia o grupo. Uma ponte travada
# congelaria o barramento de seis blocos para todo mundo, e os quadros que ela nunca pegou
# cresceriam sem limite no daemon de um appliance. Um quadro que não sai neste tanto de
# segundos diz que o socket não está mais pegando quadros, então ele vai embora.
ENVIO_S = 2.0

# Why: the largest honest frame of section 8 is a set of one data point, and the reader holds
# a whole message in memory before anybody looks at it; a client that sends more is closed by
# the library instead of being believed.
# Por que: o maior quadro honesto da seção 8 é um set de um data point, e o leitor guarda a
# mensagem inteira na memória antes de alguém olhar; um cliente que manda mais é fechado pela
# biblioteca em vez de ser acreditado.
QUADRO_MAXIMO = 4 * 1024

REQUER_WEBSOCKET = "requer_websocket"

# The code of a defect of ours, which the gate already answers on a 500 and the panel already
# translates; a device that refused answers one of its own, section 6.
# O código de um defeito nosso, que o portão já responde num 500 e o painel já traduz; um
# aparelho que recusou responde um código dele, seção 6.
ERRO_INTERNO = "erro_interno"

# Tells a data point that was never published from one published as a false or as a zero.
# Distingue um data point nunca publicado de um publicado como falso ou como zero.
_AUSENTE = object()

type Ajuste = Callable[[object, object], Awaitable[str | None]]
type Fonte = Callable[[], dict[int, object]]
type Enums = Callable[[mapa.Dp], tuple[str, ...]]
type Passo = Callable[[], Awaitable[None]]
type Releitura = Callable[[int], Awaitable[None]]
type Dormir = Callable[[float], Awaitable[None]]
type Relogio = Callable[[], float]
type ObterToken = Callable[[], str]


async def _nada_com_dp(_dpid: int) -> None:
    return None


async def _nada() -> None:
    """The installation hook of a bus that was handed none, so no branch guards a call.

    O gancho de instalação de um barramento que não recebeu nenhum, para nenhum desvio
    guardar uma chamada.
    """


def _do_mapa(dp: mapa.Dp) -> tuple[str, ...]:
    """The values of an enum the contract fixes; a runtime one comes from the installation.

    Os valores de um enum que o contrato fixa; um de runtime vem da instalação.
    """
    return dp.valores


class Barramento:
    """Holds who is listening, what was already published and the pending verifications.

    Guarda quem está escutando, o que já foi publicado e as verificações pendentes.
    """

    def __init__(
        self,
        ajustar: Ajuste,
        valores: Fonte,
        obter_token: ObterToken,
        *,
        valores_de: Enums = _do_mapa,
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
        self._valores_de = valores_de
        self._sanear = sanear
        self._sincronizar = sincronizar
        self._reler_dp = reler
        self._dormir = dormir
        self._agora = agora
        self._prazo_auth_s = prazo_auth_s
        self._releitura_s = releitura_s
        self._intervalo_s = intervalo_s
        self._envio_s = envio_s
        self._clientes: set[web.WebSocketResponse] = set()
        self._ultimos: dict[int, object] = {}
        self._publicados: dict[int, float] = {}
        self._verificacoes: dict[int, asyncio.Task] = {}
        self._laco: asyncio.Task | None = None

    async def iniciar(self) -> None:
        """Boot: the zombie group of section 14 goes down before anything is published.

        Boot: o grupo zumbi da seção 14 cai antes de qualquer coisa ser publicada.
        """
        await self._sanear()
        self._laco = asyncio.create_task(self._rodar(), name="dpbus:laco")

    async def parar(self) -> None:
        """Takes the loop, the verifications and the sockets off the wire, in that order.

        Tira o laço, as verificações e os sockets do fio, nessa ordem.
        """
        laco, self._laco = self._laco, None
        tarefas = [*self._verificacoes.values()]
        self._verificacoes.clear()
        if laco is not None:
            tarefas.append(laco)
        for tarefa in tarefas:
            tarefa.cancel()
        await asyncio.gather(*tarefas, return_exceptions=True)
        for cliente in tuple(self._clientes):
            # Why: a client parked on receive holds the handler task of its own request, and
            # the application would wait for it forever while the loop is being closed.
            # Por que: um cliente parado no receive segura a tarefa do handler do pedido dele,
            # e a aplicação esperaria por ela para sempre enquanto o laço está sendo fechado.
            with contextlib.suppress(Exception):
                await cliente.close()
        self._clientes.clear()

    async def revogar(self) -> None:
        """Section 9: the api_token was rotated, so every socket it authenticated is over.

        Why: a socket authenticates on its first frame and is never asked again, so without
        this the documented remediation for a leaked machine credential remediates nothing:
        whoever holds the old token keeps volume, transport, input, group and scene control
        of every block for as long as the daemon runs, and a bridge socket is long lived by
        design, so it never has to reconnect.

        Seção 9: o api_token foi rotacionado, então todo socket que ele autenticou acabou.

        Por que: um socket autentica no primeiro quadro e nunca mais é perguntado, então sem
        isto a remediação documentada de uma credencial de máquina vazada não remedia nada:
        quem tem o token antigo mantém volume, transporte, entrada, grupo e cena de todo bloco
        enquanto o daemon viver, e um socket de ponte é longevo por projeto, então ele nunca
        precisa reconectar.
        """
        # Why: the handler task of each socket is parked reading it, so the peer may see the
        # connection drop instead of this code; what revocation guarantees is that the socket
        # is gone and answers nothing, not which of the two closes reaches the other end.
        # Por que: a tarefa do handler de cada socket está parada lendo, então o outro lado
        # pode ver a conexão cair em vez deste código; o que a revogação garante é que o
        # socket acabou e não responde mais, não qual dos dois fechamentos chega lá.
        for cliente in tuple(self._clientes):
            with contextlib.suppress(Exception):
                await cliente.close(code=FECHAMENTO_NAO_AUTENTICADO)
        self._clientes.clear()

    @property
    def ouvintes(self) -> int:
        """How many clients a report goes out to right now.

        Quantos clientes recebem um report agora.
        """
        # Why: a socket that went away and stayed in the set is a reference the daemon of an
        # appliance never gets back, so how many are listening has to be readable.
        # Por que: um socket que foi embora e ficou no conjunto é uma referência que o daemon
        # de um appliance nunca recupera, então quantos estão escutando precisa ser legível.
        return len(self._clientes)

    def snapshot(self) -> dict:
        """Everything that may be reported right now, which is what a new client is handed.

        Tudo que pode ser reportado agora, que é o que um cliente novo recebe.
        """
        return protocolo.snapshot(self._valores())

    async def aplicar(self, dpid: object, valor: object) -> str | None:
        """One set of section 8: done, or a stable code. Nothing raises out of here.

        Um set da seção 8: feito, ou um código estável. Nada estoura daqui.
        """
        try:
            codigo = await self._ajustar(dpid, valor)
        except asyncio.CancelledError:
            raise
        except Exception as erro:
            # Why: a defect of ours below this line would leave the client waiting for an ack
            # that never comes and take the socket of six blocks down with it; the code says the
            # daemon failed, which is not the same as the speaker refusing.
            # Por que: um defeito nosso abaixo desta linha deixaria o cliente esperando por um
            # ack que nunca vem e levaria junto o socket de seis blocos; o código diz que o
            # daemon falhou, que não é a mesma coisa que a caixa recusar.
            log.exception("the bus failed to set dp %r: %s", dpid, _causa(erro))
            return ERRO_INTERNO
        dp = mapa.de_dp(dpid)
        if codigo is None and dp is not None:
            await self._otimista(dp, valor)
        return codigo

    async def atender(self, request: web.Request) -> web.StreamResponse:
        """The whole life of one socket: handshake, first frame, snapshot, then the sets.

        A vida inteira de um socket: aperto de mão, primeiro quadro, snapshot, depois os sets.
        """
        ws = web.WebSocketResponse(max_msg_size=QUADRO_MAXIMO)
        if not ws.can_prepare(request):
            # Why: whoever opened this address in a browser gets the stable code of section 11
            # and not the phrase the library raises, which would leave the gate as prose.
            # Por que: quem abriu este endereço num navegador recebe o código estável da seção
            # 11 e não a frase que a biblioteca estoura, que sairia do portão como prosa.
            return resposta_erro(426, REQUER_WEBSOCKET)
        await ws.prepare(request)
        if not await self._autenticar(ws):
            log.warning("a dpbus client did not authenticate and was closed")
            await ws.close(code=FECHAMENTO_NAO_AUTENTICADO)
            return ws
        # Why: listening comes BEFORE the snapshot, because a report published in between
        # would be sent to everybody but this client, and it would hold that stale value until
        # the data point changed again; a report ahead of the snapshot costs nothing, since the
        # snapshot is read after it and is never older.
        # Por que: escutar vem ANTES do snapshot, porque um report publicado no meio sairia
        # para todos menos este cliente, e ele guardaria aquele valor velho até o data point
        # mudar de novo; um report na frente do snapshot não custa nada, já que o snapshot é
        # lido depois dele e nunca é mais antigo.
        self._clientes.add(ws)
        try:
            await self._mandar(ws, self.snapshot())
            await self._conversar(ws)
        finally:
            self._clientes.discard(ws)
        return ws

    async def publicar(self) -> None:
        """Sends a report for every data point whose value is not the one already published.

        Manda um report para todo data point cujo valor não é o que já foi publicado.
        """
        atuais = self._valores()
        # Why: a block whose speaker was removed stops appearing in the values at all, so the
        # loop below never visits it and the last thing published about it stands forever: the
        # bridge keeps showing DP 104 online, with a volume and a title, for a block that has
        # no speaker. A data point that stopped existing is reported as the empty block it is.
        # Por que: um bloco cuja caixa foi removida deixa de aparecer nos valores, então o laço
        # abaixo nunca passa por ele e o último publicado a respeito fica valendo para sempre:
        # a ponte segue mostrando o DP 104 online, com volume e título, para um bloco que não
        # tem caixa. Um data point que deixou de existir é reportado como o bloco vazio que é.
        for dpid in tuple(self._ultimos):
            if dpid in atuais:
                continue
            dp = mapa.de_dp(dpid)
            if dp is None or not dp.reportavel:
                continue
            vazio = mapa.vazio_de(dp)
            if vazio is not None and self._ultimos.get(dpid, _AUSENTE) != vazio:
                await self._reportar(dp, vazio)
        for dpid, valor in atuais.items():
            dp = mapa.de_dp(dpid)
            if dp is None or not dp.reportavel or self._ultimos.get(dpid, _AUSENTE) == valor:
                continue
            if dp.throttle_s and self._agora() - self._publicados.get(dpid, 0.0) < dp.throttle_s:
                # Why: section 8 throttles DP 105 to one report every five seconds, and the
                # value is NOT recorded as published, so the first tick after the throttle
                # sends it instead of losing it.
                # Por que: a seção 8 limita o DP 105 a um report a cada cinco segundos, e o
                # valor NÃO é anotado como publicado, então o primeiro tique depois do limite o
                # manda em vez de perdê-lo.
                continue
            await self._reportar(dp, valor)

    async def _autenticar(self, ws: web.WebSocketResponse) -> bool:
        """True only for a first frame that is an auth carrying the api_token of section 9.

        Verdadeiro só para um primeiro quadro que é um auth com o api_token da seção 9.
        """
        token = protocolo.ler_auth(await self._primeiro(ws))
        esperado = self._obter_token()
        if not token or not esperado:
            return False
        # Why: comparing with == hands whoever measures the answer the length of the common
        # prefix, and this token is the machine credential of the whole bus.
        # Por que: comparar com == entrega a quem mede a resposta o tamanho do prefixo comum, e
        # este token é a credencial de máquina do barramento inteiro.
        return secrets.compare_digest(token, esperado)

    async def _primeiro(self, ws: web.WebSocketResponse) -> object:
        """The first frame as an object, or None when the deadline of section 8 won the race.

        O primeiro quadro como objeto, ou None quando o prazo da seção 8 ganhou a corrida.
        """
        recebe = asyncio.ensure_future(ws.receive())
        prazo = asyncio.ensure_future(self._dormir(self._prazo_auth_s))
        try:
            await asyncio.wait({recebe, prazo}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Why: whichever of the two lost the race is still on the loop, and a socket that
            # is about to close must not leave a task reading it.
            # Por que: o que perdeu a corrida dos dois ainda está no laço, e um socket que está
            # para fechar não pode deixar uma tarefa lendo ele.
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

    async def _conversar(self, ws: web.WebSocketResponse) -> None:
        """Every frame after the auth: one ack for each, and never an exception out.

        Todo quadro depois do auth: um ack para cada, e nunca uma exceção saindo.
        """
        async for mensagem in ws:
            bruto = _objeto(mensagem)
            # Why: an unknown frame is answered and the socket lives on, because the other end
            # is whatever bridge somebody implemented from the public contract, and one bad
            # frame must not drop a socket that is carrying six blocks.
            # Por que: um quadro desconhecido é respondido e o socket segue vivo, porque do
            # outro lado está a ponte que alguém implementou do contrato público, e um quadro
            # ruim não pode derrubar um socket que carrega seis blocos.
            leitura = protocolo.ler_set(bruto, valores=self._valores_do_quadro(bruto))
            if leitura.pedido is None:
                await self._mandar(ws, protocolo.ack(leitura.id, leitura.codigo))
                continue
            codigo = await self.aplicar(leitura.pedido.dp.dpid, leitura.pedido.valor)
            await self._mandar(ws, protocolo.ack(leitura.id, codigo))

    def _valores_do_quadro(self, bruto: object) -> tuple[str, ...]:
        """The values the enum of THAT data point really offers, which is a list of inputs.

        Section 14: only what plm_support declares is offered, and the list lives with the
        blocks; the frame only says which data point is being asked about.

        Os valores que o enum DAQUELE data point realmente oferece, que é uma lista de
        entradas.

        Seção 14: só o que o plm_support declara é oferecido, e a lista mora com os blocos; o
        quadro só diz sobre qual data point se está perguntando.
        """
        if not isinstance(bruto, dict):
            return ()
        dp = mapa.de_dp(bruto.get("dpid"))
        return () if dp is None else self._valores_de(dp)

    async def _otimista(self, dp: mapa.Dp, valor: object) -> None:
        """The bench cadence: publish what was asked now, reread the truth a moment later.

        A cadência da bancada: publica o que foi pedido agora, relê a verdade logo depois.
        """
        if dp.reportavel:
            await self._reportar(dp, valor)
        antiga = self._verificacoes.pop(dp.dpid, None)
        if antiga is not None:
            # Why: a second command for the same data point makes the older verification
            # publish a state the customer already replaced, and the bridge would watch the
            # volume bounce back on its own.
            # Por que: um segundo comando para o mesmo data point faz a verificação antiga
            # publicar um estado que o cliente já trocou, e a ponte veria o volume voltar
            # sozinho.
            antiga.cancel()
        tarefa = asyncio.create_task(self._reler(dp.dpid), name=f"dpbus:verifica:{dp.dpid}")
        self._verificacoes[dp.dpid] = tarefa
        tarefa.add_done_callback(functools.partial(self._fim_da_verificacao, dp.dpid))

    async def _reler(self, dpid: int) -> None:
        """Section 14: report what was asked now, ask the device a moment later.

        Seção 14: reporta o que foi pedido agora, pergunta ao aparelho um instante depois.
        """
        await self._dormir(self._releitura_s)
        await self._reler_dp(dpid)
        await self.publicar()

    def _fim_da_verificacao(self, dpid: int, tarefa: asyncio.Task) -> None:
        if self._verificacoes.get(dpid) is tarefa:
            del self._verificacoes[dpid]

    async def _rodar(self) -> None:
        """Reconciles the group of section 14 and publishes what changed, on one task.

        Reconcilia o grupo da seção 14 e publica o que mudou, numa tarefa só.
        """
        while True:
            await self._dormir(self._intervalo_s)
            try:
                await self._sincronizar()
                await self.publicar()
            except asyncio.CancelledError:
                raise
            except Exception as erro:
                # Why: this loop is the only thing publishing real state, so it never dies of
                # one bad reading; a device that answers nonsense costs a line of log.
                # Por que: este laço é a única coisa publicando estado real, então ele nunca
                # morre de uma leitura ruim; um aparelho que responde besteira custa uma linha
                # de log.
                log.exception("the dpbus loop failed: %s", _causa(erro))

    async def _reportar(self, dp: mapa.Dp, valor: object) -> None:
        try:
            quadro = protocolo.report(dp.dpid, valor, self._agora())
        except ValueError as erro:
            log.error("dp %d does not carry that reading: %s", dp.dpid, _causa(erro))
            return
        self._ultimos[dp.dpid] = valor
        self._publicados[dp.dpid] = self._agora()
        for cliente in tuple(self._clientes):
            await self._mandar(cliente, quadro)

    async def _mandar(self, ws: web.WebSocketResponse, quadro: dict) -> None:
        try:
            async with asyncio.timeout(self._envio_s):
                await ws.send_str(_texto(quadro))
        except TimeoutError:
            log.warning("a dpbus client stopped reading and was dropped")
            self._clientes.discard(ws)
            with contextlib.suppress(Exception):
                await ws.close()
        except Exception as erro:
            # Why: a client that went away between the comparison and the send is a socket
            # that is gone and not a failure of the bus, and the other blocks keep publishing.
            # Por que: um cliente que sumiu entre a comparação e o envio é um socket que foi
            # embora e não uma falha do barramento, e as outros blocos seguem publicando.
            log.debug("a dpbus client did not take a frame: %s", _causa(erro))
            self._clientes.discard(ws)


BARRAMENTO = web.AppKey("barramento", Barramento)


async def dpbus(request: web.Request) -> web.StreamResponse:
    return await request.app[BARRAMENTO].atender(request)


async def subir_barramento(app: web.Application) -> None:
    await app[BARRAMENTO].iniciar()


async def baixar_barramento(app: web.Application) -> None:
    await app[BARRAMENTO].parar()


def _objeto(mensagem: object) -> object:
    """One frame as the object it carries, or None for anything that is not JSON text.

    Um quadro como o objeto que ele carrega, ou None para o que não for JSON em texto.
    """
    if getattr(mensagem, "type", None) is not WSMsgType.TEXT:
        return None
    try:
        return json.loads(mensagem.data)
    except ValueError:
        return None


def _texto(quadro: dict) -> str:
    # Why: ensure_ascii would write an accented letter of a track title as six bytes, and the
    # frame is UTF-8 all the way; the compact separators keep a report small on a busy bus.
    # Por que: o ensure_ascii escreveria uma letra acentuada de um título de faixa em seis
    # bytes, e o quadro é UTF-8 do começo ao fim; os separadores compactos mantêm um report
    # pequeno num barramento movimentado.
    return json.dumps(quadro, ensure_ascii=False, separators=(",", ":"))


def _causa(erro: BaseException) -> str:
    return str(erro) or type(erro).__name__

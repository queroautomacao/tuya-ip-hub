# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 8 and 9: the WebSocket of the DP-bus, thin over the map, the protocol and the
numbers of every licence.

What a frame means was decided in protocolo.py and where a set lands was decided by the
module that owns the numbers and the scenes, so this file holds only what needs a socket: the
handshake, the first frame, who is listening for which licence, and when a report goes out.
Everything it touches of the installation arrives as a function, which is why every rule
below is tested without a speaker and without a route.

Section 8, word by word, and each word is a test that attacks it. The FIRST frame is
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

The reports of section 8, which is what keeps the daily count of the platform low:

- only what CHANGED against the last published value, never a repeat;
- class A (power, level, temperature, mode, fan, group, online) waits a window of 2 s per
  data point, and the last value of the window wins;
- class B (inputs, modes, muted) waits 10 s;
- class C: profiles and names only move when the registration does, and the titles are
  NEVER pushed, they only answer a consulta;
- the reports of the day are counted per licence: at 250 the class B stops and the class A
  window opens to 30 s, with a warning in the log, so the cloud never gets to throttle.

Section 9 holds here exactly as it holds for /api/*: the Host rule, the Origin rule and the
four headers are the same middlewares of the gate, and the handshake response passes through
them like any other response. Nothing this module sends ever carries the api_token back.

Seções 8 e 9: o WebSocket do DP-bus, fino sobre o mapa, o protocolo e os números de cada
licença.

O que um quadro significa foi decidido no protocolo.py e onde um set cai foi decidido pelo
módulo dono dos números e das cenas, então este arquivo guarda só o que precisa de socket: o
aperto de mão, o primeiro quadro, quem está escutando por qual licença, e quando um report
sai. Tudo que ele toca da instalação chega como função, e é por isso que toda regra abaixo é
testada sem caixa e sem rota.

A seção 8, palavra por palavra, e cada palavra é um teste que a ataca. O PRIMEIRO quadro é
{"t":"auth","token":"<api_token>","licenca":"<id>"} e o token NUNCA viaja na URL, porque uma
query string é escrita em todo log de acesso e no histórico de quem a colou; sem esse quadro
em cinco segundos, com um token que não casa, ou com uma licença que este hub não tem, o
socket fecha com 4401 e não responde mais nada. Depois disso NÃO há rajada: a ponte pergunta
com um quadro consulta e recebe o snapshot da licença dela, que não conta como report. Entram
sets, saem acks e reports. Um comando reporta OTIMISTA e relê cerca de um segundo e meio
depois, que é a cadência que a bancada mediu, então a ponte vê o nível que pediu na hora e a
verdade logo em seguida; um comando novo para o mesmo data point cancela a verificação
pendente, porque a antiga publicaria um estado sobre o qual o cliente já mudou de ideia.

Os reports da seção 8, que é o que mantém baixa a contagem diária da plataforma:

- só o que MUDOU em relação ao último valor publicado, nunca uma repetição;
- classe A (ligado, nível, temperatura, modo, vento, grupo, online) espera uma janela de 2 s
  por data point, e o último valor da janela vence;
- classe B (entradas, modos, mudos) espera 10 s;
- classe C: perfis e nomes só se movem quando o cadastro se move, e os títulos NUNCA são
  empurrados, só respondem a uma consulta;
- os reports do dia são contados por licença: em 250 a classe B para e a janela da classe A
  abre para 30 s, com aviso no log, para a nuvem nunca chegar a limitar.

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

from iphub.dpbus import comando, mapa, protocolo
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
# on the single task that publishes every report and reconciles the groups. One stalled
# bridge would freeze the bus of every licence for everybody, and the frames it never took
# would grow without bound in the daemon of an appliance. A frame that does not leave in this
# many seconds means the socket is not taking frames any more, so it goes.
# Por que: um cliente que para de ler enche o buffer do kernel e então o send_str espera para
# sempre, na única tarefa que publica todo report e reconcilia os grupos. Uma ponte travada
# congelaria o barramento de toda licença para todo mundo, e os quadros que ela nunca pegou
# cresceriam sem limite no daemon de um appliance. Um quadro que não sai neste tanto de
# segundos diz que o socket não está mais pegando quadros, então ele vai embora.
ENVIO_S = 2.0

# Why: the largest honest frame of section 8 is a set of the command channel, and the reader
# holds a whole message in memory before anybody looks at it; a client that sends more is
# closed by the library instead of being believed.
# Por que: o maior quadro honesto da seção 8 é um set do canal de comando, e o leitor guarda
# a mensagem inteira na memória antes de alguém olhar; um cliente que manda mais é fechado
# pela biblioteca em vez de ser acreditado.
QUADRO_MAXIMO = 4 * 1024

SEGUNDOS_POR_DIA = 86_400

REQUER_WEBSOCKET = "requer_websocket"

# The code of a defect of ours, which the gate already answers on a 500 and the panel already
# translates; a device that refused answers one of its own, section 6.
# O código de um defeito nosso, que o portão já responde num 500 e o painel já traduz; um
# aparelho que recusou responde um código dele, seção 6.
ERRO_INTERNO = "erro_interno"

# Tells a data point that was never published from one published as a false or as a zero.
# Distingue um data point nunca publicado de um publicado como falso ou como zero.
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
    """The installation hook of a bus that was handed none, so no branch guards a call.

    O gancho de instalação de um barramento que não recebeu nenhum, para nenhum desvio
    guardar uma chamada.
    """


def _nenhuma() -> tuple[str, ...]:
    return ()


class _Canal:
    """Everything the bus keeps about ONE licence: who listens, what was published, what is
    being verified and how many reports the day has cost.

    Tudo que o barramento guarda de UMA licença: quem escuta, o que foi publicado, o que está
    sendo verificado e quantos reports o dia já custou.
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

    Guarda quem está escutando por cada licença, o que já foi publicado e as verificações
    pendentes.
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
        """Boot: the zombie groups of section 14 go down before anything is published.

        Boot: os grupos zumbis da seção 14 caem antes de qualquer coisa ser publicada.
        """
        await self._sanear()
        self._laco = asyncio.create_task(self._rodar(), name="dpbus:laco")

    async def parar(self) -> None:
        """Takes the loop, the verifications and the sockets off the wire, in that order.

        Tira o laço, as verificações e os sockets do fio, nessa ordem.
        """
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
                # Why: a client parked on receive holds the handler task of its own request,
                # and the application would wait for it forever while the loop is being
                # closed.
                # Por que: um cliente parado no receive segura a tarefa do handler do pedido
                # dele, e a aplicação esperaria por ela para sempre enquanto o laço está
                # sendo fechado.
                with contextlib.suppress(Exception):
                    await cliente.close()
            canal.clientes.clear()

    async def revogar(self) -> None:
        """Section 9: the api_token was rotated, so every socket it authenticated is over.

        Why: a socket authenticates on its first frame and is never asked again, so without
        this the documented remediation for a leaked machine credential remediates nothing:
        whoever holds the old token keeps every number of every licence for as long as the
        daemon runs, and a bridge socket is long lived by design, so it never has to
        reconnect.

        Seção 9: o api_token foi rotacionado, então todo socket que ele autenticou acabou.

        Por que: um socket autentica no primeiro quadro e nunca mais é perguntado, então sem
        isto a remediação documentada de uma credencial de máquina vazada não remedia nada:
        quem tem o token antigo mantém todo número de toda licença enquanto o daemon viver, e
        um socket de ponte é longevo por projeto, então ele nunca precisa reconectar.
        """
        for canal in self._canais.values():
            await self._fechar_clientes(canal)

    async def desligar(self, licenca: str) -> None:
        """A licence left the installation: its sockets close and its books are forgotten.

        Uma licença saiu da instalação: os sockets dela fecham e os livros dela são esquecidos.
        """
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
        # Why: the handler task of each socket is parked reading it, so the peer may see the
        # connection drop instead of this code; what revocation guarantees is that the socket
        # is gone and answers nothing, not which of the two closes reaches the other end.
        # Por que: a tarefa do handler de cada socket está parada lendo, então o outro lado
        # pode ver a conexão cair em vez deste código; o que a revogação garante é que o
        # socket acabou e não responde mais, não qual dos dois fechamentos chega lá.
        for cliente in tuple(canal.clientes):
            with contextlib.suppress(Exception):
                await cliente.close(code=FECHAMENTO_NAO_AUTENTICADO)
        canal.clientes.clear()

    @property
    def ouvintes(self) -> int:
        """How many clients a report goes out to right now, every licence counted.

        Quantos clientes recebem um report agora, toda licença contada.
        """
        # Why: a socket that went away and stayed in the set is a reference the daemon of an
        # appliance never gets back, so how many are listening has to be readable.
        # Por que: um socket que foi embora e ficou no conjunto é uma referência que o daemon
        # de um appliance nunca recupera, então quantos estão escutando precisa ser legível.
        return sum(len(canal.clientes) for canal in self._canais.values())

    def ouvintes_de(self, licenca: str) -> int:
        canal = self._canais.get(licenca)
        return 0 if canal is None else len(canal.clientes)

    def reports_do_dia(self, licenca: str) -> int:
        """How many reports the licence has cost today, which the panel shows.

        Quantos reports a licença já custou hoje, que o painel mostra.
        """
        canal = self._canais.get(licenca)
        if canal is None:
            return 0
        self._virar_dia(canal)
        return canal.reports

    def snapshot(self, licenca: str, identificador: object = None) -> dict:
        """Everything of one licence that may be reported right now, which answers a consulta.

        Tudo de uma licença que pode ser reportado agora, que responde uma consulta.
        """
        produto = self._produto_de(licenca)
        if produto is None:
            return {"t": protocolo.T_SNAPSHOT, "id": identificador, "dps": {}}
        return protocolo.snapshot(produto, self._valores(licenca), identificador)

    async def aplicar(self, licenca: str, dpid: object, valor: object) -> str | None:
        """One set of section 8: done, or a stable code. Nothing raises out of here.

        Um set da seção 8: feito, ou um código estável. Nada estoura daqui.
        """
        try:
            codigo = await self._ajustar(licenca, dpid, valor)
        except asyncio.CancelledError:
            raise
        except Exception as erro:
            # Why: a defect of ours below this line would leave the client waiting for an ack
            # that never comes and take the socket of a whole licence down with it; the code
            # says the daemon failed, which is not the same as the equipment refusing.
            # Por que: um defeito nosso abaixo desta linha deixaria o cliente esperando por um
            # ack que nunca vem e levaria junto o socket de uma licença inteira; o código diz
            # que o daemon falhou, que não é a mesma coisa que o equipamento recusar.
            log.exception("the bus failed to set dp %r of %s: %s", dpid, licenca, _causa(erro))
            return ERRO_INTERNO
        produto = self._produto_de(licenca)
        dp = None if produto is None else mapa.de_dp(produto, dpid)
        if codigo is None and dp is not None and dp.funcao != "cena":
            await self._otimista(licenca, dp, valor)
        return codigo

    async def atender(self, request: web.Request) -> web.StreamResponse:
        """The whole life of one socket: handshake, first frame, then the sets and queries.

        A vida inteira de um socket: aperto de mão, primeiro quadro, depois os sets e as
        consultas.
        """
        ws = web.WebSocketResponse(max_msg_size=QUADRO_MAXIMO)
        if not ws.can_prepare(request):
            # Why: whoever opened this address in a browser gets the stable code of section 11
            # and not the phrase the library raises, which would leave the gate as prose.
            # Por que: quem abriu este endereço num navegador recebe o código estável da seção
            # 11 e não a frase que a biblioteca estoura, que sairia do portão como prosa.
            return resposta_erro(426, REQUER_WEBSOCKET)
        await ws.prepare(request)
        licenca = await self._autenticar(ws)
        if licenca is None:
            log.warning("a dpbus client did not authenticate and was closed")
            await ws.close(code=FECHAMENTO_NAO_AUTENTICADO)
            return ws
        # Why: section 8, there is no burst on the way up: the bridge asks with a consulta
        # and gets the slice of its licence, which is not counted as a report.
        # Por que: seção 8, não há rajada na subida: a ponte pergunta com uma consulta e
        # recebe a fatia da licença dela, que não conta como report.
        canal = self._canal(licenca)
        canal.clientes.add(ws)
        try:
            await self._conversar(ws, licenca)
        finally:
            canal.clientes.discard(ws)
        return ws

    async def publicar(self) -> None:
        """Sends a report for every data point of every licence whose value is not the one
        already published, under the policy of section 8.

        Manda um report para todo data point de toda licença cujo valor não é o que já foi
        publicado, sob a política da seção 8.
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
        # Why: a number whose equipment was removed stops appearing in the values at all, so
        # what was published about it is forgotten; when the number is occupied again its
        # state is new and reports again instead of being taken for the old one.
        # Por que: um número cujo equipamento foi removido deixa de aparecer nos valores, então
        # o que foi publicado sobre ele é esquecido; quando o número é ocupado de novo o estado
        # dele é novo e reporta de novo em vez de ser tomado pelo antigo.
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
                # Why: the value is NOT recorded as published, so the first tick after the
                # window sends the last value of the window instead of losing it.
                # Por que: o valor NÃO é anotado como publicado, então o primeiro tique depois
                # da janela manda o último valor da janela em vez de perdê-lo.
                continue
            await self._reportar(canal, dp, valor)

    def _pode(self, canal: _Canal, dp: mapa.Dp) -> bool:
        """The policy of section 8 for one data point right now: its window, widened or
        closed once the day cost 250 reports.

        A política da seção 8 para um data point agora: a janela dele, alargada ou fechada
        depois de o dia custar 250 reports.
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
        """The licence of a first frame that is an auth carrying the api_token of section 9
        and a licence this hub has, or None.

        A licença de um primeiro quadro que é um auth com o api_token da seção 9 e uma licença
        que este hub tem, ou None.
        """
        auth = protocolo.ler_auth(await self._primeiro(ws))
        esperado = self._obter_token()
        if not auth.token or not esperado:
            return None
        # Why: comparing with == hands whoever measures the answer the length of the common
        # prefix, and this token is the machine credential of the whole bus.
        # Por que: comparar com == entrega a quem mede a resposta o tamanho do prefixo comum, e
        # este token é a credencial de máquina do barramento inteiro.
        if not secrets.compare_digest(auth.token, esperado):
            return None
        if self._produto_de(auth.licenca) is None:
            log.warning("a dpbus client named a licence this hub does not have")
            return None
        return auth.licenca

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

    async def _conversar(self, ws: web.WebSocketResponse, licenca: str) -> None:
        """Every frame after the auth: one answer for each, and never an exception out.

        Todo quadro depois do auth: uma resposta para cada, e nunca uma exceção saindo.
        """
        async for mensagem in ws:
            bruto = _objeto(mensagem)
            produto = self._produto_de(licenca)
            if produto is None:
                # Why: the licence was removed while the socket lived; nothing of it may be
                # answered any more, and the removal already closed this socket.
                # Por que: a licença foi removida com o socket vivo; nada dela pode mais ser
                # respondido, e a remoção já fechou este socket.
                return
            # Why: an unknown frame is answered and the socket lives on, because the other end
            # is whatever bridge somebody implemented from the public contract, and one bad
            # frame must not drop a socket that is carrying a whole licence.
            # Por que: um quadro desconhecido é respondido e o socket segue vivo, porque do
            # outro lado está a ponte que alguém implementou do contrato público, e um quadro
            # ruim não pode derrubar um socket que carrega uma licença inteira.
            leitura = protocolo.ler_quadro(bruto, produto)
            if leitura.consulta:
                log.debug("%s: consulta %s", licenca, leitura.id)
                await self._mandar(ws, self.snapshot(licenca, leitura.id))
                continue
            if leitura.pedido is None:
                log.debug("%s: quadro recusado, %s", licenca, leitura.codigo)
                await self._mandar(ws, protocolo.ack(leitura.id, leitura.codigo))
                continue
            # Why: this line and the ack below are the two halves of what the bridge of the
            # platform asked for, and the diary of the panel is where the integrator reads
            # whether a button of the app of the customer ever reached the hub at all.
            # Por que: esta linha e o ack abaixo são as duas metades do que a ponte da
            # plataforma pediu, e o diário do painel é onde o integrador lê se um botão do app
            # do cliente chegou ao hub.
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
        """The bench cadence: publish what was asked now, reread the truth a moment later.

        A cadência da bancada: publica o que foi pedido agora, relê a verdade logo depois.
        """
        canal = self._canal(licenca)
        if dp.reportavel and dp.empurrado and canal.ultimos.get(dp.dpid, _AUSENTE) != valor:
            # Why: the optimistic report is the one the customer is waiting for with the app
            # open, so it skips the window; the reread only reports if the device diverged. A
            # value already published is never repeated, and once the day cost 250 reports
            # the set waits the widened window like any other change, so a slider dragged
            # all afternoon cannot push the licence past the budget of the platform.
            # Por que: o report otimista é o que o cliente espera com o app aberto, então ele
            # pula a janela; a releitura só reporta se o aparelho divergiu. Um valor já
            # publicado nunca é repetido, e depois de o dia custar 250 reports o set espera a
            # janela alargada como qualquer mudança, então um slider arrastado a tarde inteira
            # não empurra a licença para além do orçamento da plataforma.
            apertado = canal.reports >= mapa.AVISO_DO_DIA
            if not apertado or self._pode(canal, dp):
                await self._reportar(canal, dp, valor)
        chave = self._chave_de_verificacao(dp, valor)
        antiga = canal.verificacoes.pop(chave, None)
        if antiga is not None:
            # Why: a second command for the same data point makes the older verification
            # publish a state the customer already replaced, and the bridge would watch the
            # level bounce back on its own.
            # Por que: um segundo comando para o mesmo data point faz a verificação antiga
            # publicar um estado que o cliente já trocou, e a ponte veria o nível voltar
            # sozinho.
            antiga.cancel()
        tarefa = asyncio.create_task(
            self._reler(licenca, dp.dpid, valor), name=f"dpbus:verifica:{licenca}:{dp.dpid}"
        )
        canal.verificacoes[chave] = tarefa
        tarefa.add_done_callback(functools.partial(self._fim_da_verificacao, canal, chave))

    def _chave_de_verificacao(self, dp: mapa.Dp, valor: object) -> object:
        """One verification per data point, except the command channel, which is one per
        number: two commands to two equipment must not cancel each other's reread.

        Uma verificação por data point, menos o canal de comando, que é uma por número: dois
        comandos a dois equipamentos não podem cancelar a releitura um do outro.
        """
        if dp.funcao != "comando":
            return dp.dpid
        lido = comando.ler(valor, mapa.NUMEROS[dp.produto])
        return (dp.dpid, 0 if lido is None else lido.numero)

    async def _reler(self, licenca: str, dpid: int, valor: object) -> None:
        """Section 14: report what was asked now, ask the device a moment later.

        Seção 14: reporta o que foi pedido agora, pergunta ao aparelho um instante depois.
        """
        await self._dormir(self._releitura_s)
        await self._reler_dp(licenca, dpid, valor)
        await self._publicar(licenca)

    def _fim_da_verificacao(self, canal: _Canal, chave: object, tarefa: asyncio.Task) -> None:
        if canal.verificacoes.get(chave) is tarefa:
            del canal.verificacoes[chave]

    async def _rodar(self) -> None:
        """Reconciles the groups of section 14 and publishes what changed, on one task.

        Reconcilia os grupos da seção 14 e publica o que mudou, numa tarefa só.
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

    async def _reportar(self, canal: _Canal, dp: mapa.Dp, valor: object) -> None:
        try:
            quadro = protocolo.report(dp, valor, self._agora())
        except ValueError as erro:
            log.error("dp %d does not carry that reading: %s", dp.dpid, _causa(erro))
            return
        canal.ultimos[dp.dpid] = valor
        canal.publicados[dp.dpid] = self._agora()
        log.debug("report dp %d = %r para %d ouvinte(s)", dp.dpid, valor, len(canal.clientes))
        # Why: a report nobody is listening to never reaches the cloud, so it does not spend
        # the budget of the day; the books are still written, so a bridge that connects later
        # gets no burst of everything the hub already knew, only what changes from then on.
        # Por que: um report que ninguém escuta nunca chega à nuvem, então não gasta o orçamento
        # do dia; os livros são escritos mesmo assim, para uma ponte que conecta depois não
        # receber uma rajada de tudo que o hub já sabia, só o que muda dali em diante.
        if canal.clientes:
            canal.reports += 1
        if canal.reports >= mapa.AVISO_DO_DIA and not canal.avisado:
            # Why: section 8, the platform throttles a device above 300 reports a day, and
            # this hub never lets it get there: from here on the class B stops and the class
            # A widens, and the operator reads why in the log.
            # Por que: seção 8, a plataforma limita um dispositivo acima de 300 reports por
            # dia, e este hub nunca deixa chegar lá: daqui em diante a classe B para e a
            # classe A alarga, e o operador lê o porquê no log.
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
            # Why: a client that went away between the comparison and the send is a socket
            # that is gone and not a failure of the bus, and the other licences keep
            # publishing.
            # Por que: um cliente que sumiu entre a comparação e o envio é um socket que foi
            # embora e não uma falha do barramento, e as outras licenças seguem publicando.
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
    # Why: a lone surrogate a device answered is the one thing a str holds that UTF-8 cannot
    # write, and a frame that failed to encode dropped the socket of the whole licence.
    # Por que: um surrogado solto que um aparelho respondeu é a única coisa que um str guarda
    # e o UTF-8 não escreve, e um quadro que falhava ao codificar derrubava o socket da licença
    # inteira.
    texto = json.dumps(quadro, ensure_ascii=False, separators=(",", ":"))
    return texto.encode("utf-8", errors="replace").decode("utf-8")


def _causa(erro: BaseException) -> str:
    return str(erro) or type(erro).__name__

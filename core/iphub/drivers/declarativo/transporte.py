# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 7: the three transports behind one interface, each with its deadline and ceiling.

Four things the engine asks of any transport: open, send a command, ask for state, close. A
command is written and never read back, because section 7 only reads what the estado block
asked for; that is what lets a matrix that answers nothing to "PWR ON" work at all.

Every failure leaves here as one stable code of section 6, never as an exception, so the
engine above has nothing to translate.

Seção 7: os três transportes atrás de uma interface, cada um com prazo e teto próprios.

Quatro coisas que o motor pede a qualquer transporte: abrir, mandar um comando, perguntar o
estado, fechar. Um comando é escrito e nunca lido de volta, porque a seção 7 só lê o que o
bloco estado perguntou; é isso que faz funcionar uma matriz que não responde nada a "PWR ON".

Toda falha sai daqui como um código estável da seção 6, nunca como exceção, então o motor
acima não tem o que traduzir.
"""

import asyncio
import logging
from collections.abc import Sequence
from contextlib import suppress

from aiohttp import ClientError, ClientSession, ClientTimeout

from iphub.config import ip_literal
from iphub.drivers.base import Cadastro
from iphub.drivers.declarativo.formato import Cabecalho, Http, Passo, Tcp, Transporte, Udp

log = logging.getLogger("iphub.drivers.declarativo.transporte")

EQ_OFFLINE = "eq_offline"
ERRO_APARELHO = "erro_aparelho"

# Why: a device on the LAN must never be able to make the daemon buffer without bound. A
# state line is a line, a body of a small appliance is not a megabyte, and the previous
# project fixed the body at 64 KB after an amplifier answered its whole web page.
# Por que: um aparelho na LAN nunca pode fazer o daemon acumular sem limite. Uma linha de
# estado é uma linha, o corpo de um aparelho pequeno não é um megabyte, e o projeto anterior
# fixou o corpo em 64 KB depois de um amplificador responder a página inteira dele.
LINHA_MAXIMA = 8 * 1024
CORPO_MAXIMO = 64 * 1024
DATAGRAMA_MAXIMO = 8 * 1024

# Why: a device answering in a loop would fill this queue forever, and the poll reads one
# datagram per question; what does not fit is what nobody asked for.
# Por que: um aparelho respondendo em laço encheria esta fila para sempre, e o poll lê um
# datagrama por pergunta; o que não cabe é o que ninguém pediu.
DATAGRAMAS_NA_FILA = 8

MARCADOR_IP = "{ip}"
MILISSEGUNDO_S = 0.001


class FalhaDeTransporte(Exception):
    """A stable code on the way out of the wire, so no exception escapes the engine.

    Um código estável na saída do fio, para nenhuma exceção escapar do motor.
    """

    def __init__(self, codigo: str) -> None:
        self.codigo = codigo
        super().__init__(codigo)


class Canal:
    """The interface: open, send a command, ask for state, close.

    A interface: abrir, mandar um comando, perguntar o estado, fechar.
    """

    async def abrir(self) -> None:
        """Opens what the transport keeps open, which for a line transport is nothing.

        Abre o que o transporte mantém aberto, que para um transporte de linha é nada.
        """

    async def fechar(self) -> None:
        """Closes what abrir opened; called even when abrir failed.

        Fecha o que o abrir abriu; chamado mesmo quando o abrir falhou.
        """

    async def enviar(self, passos: Sequence[Passo], *, intervalo_ms: int = 0) -> None:
        """Puts every step on the wire, in order, and reads nothing back.

        Põe todo passo no fio, na ordem, e não lê nada de volta.
        """
        raise NotImplementedError

    async def perguntar(self, passos: Sequence[Passo]) -> tuple[str, ...]:
        """One answer per step, in the same order, for the estado block to read.

        Uma resposta por passo, na mesma ordem, para o bloco estado ler.
        """
        raise NotImplementedError


class _Canal(Canal):
    """What the three share: the address, the deadline, the pace and the one session lock.

    O que os três compartilham: o endereço, o prazo, o ritmo e a trava de sessão única.
    """

    def __init__(self, ip: str, timeout_s: float, intervalo_min_ms: int = 0) -> None:
        self._ip = ip
        self._timeout_s = timeout_s
        self._intervalo_min_ms = intervalo_min_ms
        # Why: the bench found the command of the integrator landing inside the poll window,
        # and a matrix or a projector accepts ONE session at a time; whoever gets here second
        # waits instead of being refused by the device.
        # Por que: a bancada achou o comando do integrador caindo dentro da janela do poll, e
        # uma matriz ou um projetor aceita UMA sessão por vez; quem chega aqui em segundo
        # espera em vez de ser recusado pelo aparelho.
        self._trava = asyncio.Lock()
        self._ultimo = 0.0

    def _endereco(self) -> str:
        """Section 9: only an IP literal reaches a device, so the hub is never a resolver.

        Seção 9: só um IP literal alcança um aparelho, então o hub nunca é um resolvedor.
        """
        endereco = ip_literal(self._ip)
        if endereco is None:
            raise FalhaDeTransporte(EQ_OFFLINE)
        return endereco

    async def _ritmo(self, intervalo_ms: int) -> None:
        """Holds the declared minimum between two things on the wire, the iEAST 200 ms rule.

        Segura o mínimo declarado entre duas coisas no fio, a regra dos 200 ms do iEAST.
        """
        espera = max(intervalo_ms, self._intervalo_min_ms) * MILISSEGUNDO_S
        if espera <= 0:
            return
        agora = asyncio.get_running_loop().time()
        atraso = self._ultimo + espera - agora
        if atraso > 0:
            await asyncio.sleep(atraso)

    def _marcar(self) -> None:
        self._ultimo = asyncio.get_running_loop().time()


class CanalTcp(_Canal):
    """One line at a time over TCP, one session at a time, the greeting tolerated when declared.

    Uma linha por vez sobre TCP, uma sessão por vez, a saudação tolerada quando declarada.
    """

    def __init__(self, ip: str, transporte: Tcp) -> None:
        super().__init__(ip, transporte.timeout_s, transporte.intervalo_min_ms)
        self._cfg = transporte
        self._terminador = transporte.terminador.encode("utf-8", errors="ignore")

    async def enviar(self, passos: Sequence[Passo], *, intervalo_ms: int = 0) -> None:
        await self._trocar(passos, intervalo_ms, ler=False)

    async def perguntar(self, passos: Sequence[Passo]) -> tuple[str, ...]:
        return await self._trocar(passos, 0, ler=True)

    async def _trocar(
        self, passos: Sequence[Passo], intervalo_ms: int, *, ler: bool
    ) -> tuple[str, ...]:
        endereco = self._endereco()
        async with self._trava:
            leitor, escritor = await self._conectar(endereco)
            try:
                # Why: the file says the device greets before it answers anything, so the
                # greeting is consumed once per session; reading it as an answer would put
                # every later read one line behind the question that asked for it.
                # Por que: o arquivo diz que o aparelho sauda antes de responder qualquer
                # coisa, então a saudação é consumida uma vez por sessão; lê-la como resposta
                # deixaria toda leitura seguinte uma linha atrás da pergunta que a pediu.
                if self._cfg.saudacao:
                    await self._ler(leitor)
                respostas = []
                for passo in passos:
                    await self._ritmo(intervalo_ms)
                    await self._escrever(escritor, passo)
                    if ler:
                        respostas.append(await self._ler(leitor))
                return tuple(respostas)
            finally:
                await _fechar(escritor)

    async def _conectar(self, endereco: str) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        try:
            async with asyncio.timeout(self._timeout_s):
                return await asyncio.open_connection(endereco, self._cfg.porta, limit=LINHA_MAXIMA)
        except (TimeoutError, OSError) as erro:
            raise FalhaDeTransporte(EQ_OFFLINE) from erro

    async def _escrever(self, escritor: asyncio.StreamWriter, passo: Passo) -> None:
        try:
            async with asyncio.timeout(self._timeout_s):
                escritor.write(_carga(passo, self._cfg.terminador))
                await escritor.drain()
        except (TimeoutError, OSError) as erro:
            raise FalhaDeTransporte(EQ_OFFLINE) from erro
        self._marcar()

    async def _ler(self, leitor: asyncio.StreamReader) -> str:
        if not self._terminador:
            return _texto(await self._ler_ate_o_prazo(leitor))
        try:
            async with asyncio.timeout(self._timeout_s):
                bruto = await leitor.readuntil(self._terminador)
        except (
            TimeoutError,
            OSError,
            asyncio.IncompleteReadError,
            asyncio.LimitOverrunError,
        ) as erro:
            raise FalhaDeTransporte(EQ_OFFLINE) from erro
        return _texto(bruto[: -len(self._terminador)])

    async def _ler_ate_o_prazo(self, leitor: asyncio.StreamReader) -> bytes:
        """The whole answer of a file that declared no terminator: what arrived by the deadline.

        Why: a single read returns the first segment that arrived, so a slow device had its
        answer cut in half and the rest of it was read as the answer to the NEXT question,
        which silently corrupted every reading after the first. With nothing framing the
        answer, the deadline is the only frame there is, and draining until it expires is
        also what leaves no unread byte behind in the session.

        A resposta inteira de um arquivo que não declarou terminador: o que chegou até o prazo.

        Por que: uma leitura só devolve o primeiro segmento que chegou, então um aparelho lento
        tinha a resposta cortada ao meio e o resto dela era lido como resposta da PRÓXIMA
        pergunta, o que corrompia em silêncio toda leitura depois da primeira. Sem nada
        emoldurando a resposta, o prazo é a única moldura que existe, e drenar até ele vencer
        é também o que não deixa byte por ler na sessão.
        """
        bruto = bytearray()
        try:
            async with asyncio.timeout(self._timeout_s):
                while len(bruto) <= LINHA_MAXIMA:
                    pedaco = await leitor.read(LINHA_MAXIMA + 1 - len(bruto))
                    if not pedaco:
                        break
                    bruto += pedaco
        except TimeoutError:
            pass
        except OSError as erro:
            raise FalhaDeTransporte(EQ_OFFLINE) from erro
        if not bruto or len(bruto) > LINHA_MAXIMA:
            # Why: a device that said nothing by its own deadline is offline, and one that
            # said more than the ceiling is the answer the daemon refuses to hold; either way
            # the session is abandoned instead of carrying a piece of it into the next read.
            # Por que: um aparelho que não disse nada até o prazo dele está offline, e um que
            # disse mais que o teto é a resposta que o daemon se recusa a guardar; nos dois
            # casos a sessão é abandonada em vez de levar um pedaço dela para a leitura
            # seguinte.
            raise FalhaDeTransporte(EQ_OFFLINE)
        return bytes(bruto)


class CanalUdp(_Canal):
    """One datagram out, one in, with the deadline; no retransmission, because none is declared.

    Um datagrama para fora, um para dentro, com o prazo; sem retransmissão, porque nenhuma é
    declarada.
    """

    def __init__(self, ip: str, transporte: Udp) -> None:
        super().__init__(ip, transporte.timeout_s, transporte.intervalo_min_ms)
        self._cfg = transporte

    async def enviar(self, passos: Sequence[Passo], *, intervalo_ms: int = 0) -> None:
        await self._trocar(passos, intervalo_ms, ler=False)

    async def perguntar(self, passos: Sequence[Passo]) -> tuple[str, ...]:
        return await self._trocar(passos, 0, ler=True)

    async def _trocar(
        self, passos: Sequence[Passo], intervalo_ms: int, *, ler: bool
    ) -> tuple[str, ...]:
        endereco = self._endereco()
        async with self._trava:
            fila: asyncio.Queue[bytes] = asyncio.Queue(maxsize=DATAGRAMAS_NA_FILA)
            transporte = await self._abrir_soquete(endereco, fila)
            try:
                respostas = []
                for passo in passos:
                    await self._ritmo(intervalo_ms)
                    transporte.sendto(_carga(passo, self._cfg.terminador))
                    self._marcar()
                    if ler:
                        respostas.append(await self._receber(fila))
                return tuple(respostas)
            finally:
                transporte.close()

    async def _abrir_soquete(
        self, endereco: str, fila: asyncio.Queue[bytes]
    ) -> asyncio.DatagramTransport:
        laco = asyncio.get_running_loop()
        try:
            transporte, _protocolo = await laco.create_datagram_endpoint(
                lambda: _ProtocoloUdp(fila), remote_addr=(endereco, self._cfg.porta)
            )
        except OSError as erro:
            raise FalhaDeTransporte(EQ_OFFLINE) from erro
        return transporte

    async def _receber(self, fila: asyncio.Queue[bytes]) -> str:
        try:
            async with asyncio.timeout(self._timeout_s):
                bruto = await fila.get()
        except TimeoutError as erro:
            raise FalhaDeTransporte(EQ_OFFLINE) from erro
        return _texto(bruto, self._cfg.terminador)


class _ProtocoloUdp(asyncio.DatagramProtocol):
    def __init__(self, fila: asyncio.Queue[bytes]) -> None:
        self._fila = fila

    def datagram_received(self, data: bytes, addr: object) -> None:
        if len(data) > DATAGRAMA_MAXIMO:
            return
        with suppress(asyncio.QueueFull):
            self._fila.put_nowait(data)


class CanalHttp(_Canal):
    """The base rendered from the registered address, the body capped, the headers from the
    registration, and a redirect never followed.

    A base montada do endereço cadastrado, o corpo com teto, os cabeçalhos vindos do cadastro,
    e um redirecionamento nunca seguido.
    """

    def __init__(self, cadastro: Cadastro, transporte: Http) -> None:
        super().__init__(cadastro.ip, transporte.timeout_s)
        self._cfg = transporte
        self._cabecalhos = _cabecalhos_de(transporte.cabecalhos, cadastro)
        self._sessao: ClientSession | None = None

    async def abrir(self) -> None:
        if self._sessao is None or self._sessao.closed:
            self._sessao = ClientSession(timeout=ClientTimeout(total=self._timeout_s))

    async def fechar(self) -> None:
        sessao, self._sessao = self._sessao, None
        if sessao is not None:
            await sessao.close()

    async def enviar(self, passos: Sequence[Passo], *, intervalo_ms: int = 0) -> None:
        await self._trocar(passos, intervalo_ms, ler=False)

    async def perguntar(self, passos: Sequence[Passo]) -> tuple[str, ...]:
        return await self._trocar(passos, 0, ler=True)

    async def _trocar(
        self, passos: Sequence[Passo], intervalo_ms: int, *, ler: bool
    ) -> tuple[str, ...]:
        base = self._cfg.base.replace(MARCADOR_IP, _hospedeiro(self._endereco()))
        async with self._trava:
            # Why: a driver driven straight, with no gestor calling iniciar, still has to talk;
            # opening under the lock also keeps two exchanges from building two sessions and
            # leaking the one that lost.
            # Por que: um driver dirigido direto, sem gestor chamando o iniciar, ainda precisa
            # falar; abrir sob a trava também impede duas trocas de montarem duas sessões e
            # vazarem a que perdeu.
            await self.abrir()
            respostas = []
            for passo in passos:
                await self._ritmo(intervalo_ms)
                corpo = await self._pedir(base, passo)
                if ler:
                    respostas.append(corpo)
            return tuple(respostas)

    async def _pedir(self, base: str, passo: Passo) -> str:
        sessao = self._sessao
        if sessao is None:
            raise FalhaDeTransporte(EQ_OFFLINE)
        metodo = passo.metodo or self._cfg.metodo
        dados = passo.corpo.encode("utf-8") if passo.corpo else None
        try:
            async with sessao.request(
                metodo,
                base + passo.envia,
                data=dados,
                headers=self._cabecalhos,
                # Why: a device answering a redirect would send the hub to whatever host it
                # names, which is the LAN proxy that section 9 refuses; the answer of an
                # equipment is data, never an instruction.
                # Por que: um aparelho respondendo redirecionamento mandaria o hub para o host
                # que ele nomear, que é o proxy de LAN que a seção 9 recusa; a resposta de um
                # equipamento é dado, nunca instrução.
                allow_redirects=False,
            ) as resposta:
                bruto = await resposta.content.read(CORPO_MAXIMO)
                estado = resposta.status
        except (TimeoutError, ClientError, OSError, ValueError) as erro:
            raise FalhaDeTransporte(EQ_OFFLINE) from erro
        self._marcar()
        if estado >= 400:
            log.warning("the device answered HTTP %d to %s", estado, passo.envia)
            raise FalhaDeTransporte(ERRO_APARELHO)
        return _texto(bruto)


def canal_de(transporte: Transporte, cadastro: Cadastro) -> Canal:
    """The channel one declaration speaks, built and not yet opened.

    O canal que uma declaração fala, construído e ainda não aberto.
    """
    if isinstance(transporte, Http):
        return CanalHttp(cadastro, transporte)
    if isinstance(transporte, Udp):
        return CanalUdp(cadastro.ip, transporte)
    return CanalTcp(cadastro.ip, transporte)


def _cabecalhos_de(cabecalhos: tuple[Cabecalho, ...], cadastro: Cadastro) -> dict[str, str]:
    """The VALUE of a header is a registration field, so no shared file ever carries a secret.

    O VALOR de um cabeçalho é um campo de cadastro, então nenhum arquivo compartilhado leva
    segredo.
    """
    montados = {}
    for cabecalho in cabecalhos:
        valor = cadastro.segredos.get(cabecalho.campo) or cadastro.campos.get(cabecalho.campo, "")
        # Why: an empty Authorization is worse than none, because the device answers 401 and
        # the integrator hunts the network instead of the field nobody filled.
        # Por que: um Authorization vazio é pior que nenhum, porque o aparelho responde 401 e o
        # integrador caça a rede em vez do campo que ninguém preencheu.
        if valor:
            montados[cabecalho.nome] = valor
    return montados


def _carga(passo: Passo, terminador: str) -> bytes:
    """What goes on the wire: the bytes of a hex literal, or the text plus the terminator.

    O que vai para o fio: os bytes de um literal hex, ou o texto mais o terminador.
    """
    if passo.hex:
        # Why: a hexadecimal literal is a whole frame in bytes, and a terminator appended to
        # it would be one byte the device never expects.
        # Por que: um literal hexadecimal é um quadro inteiro em bytes, e um terminador colado
        # nele seria um byte que o aparelho nunca espera.
        return bytes.fromhex(passo.envia)
    return (passo.envia + terminador).encode("utf-8", errors="ignore")


def _texto(bruto: bytes, terminador: str = "") -> str:
    """What a device answered, as text; a device writes what it likes and never breaks a read.

    O que um aparelho respondeu, como texto; um aparelho escreve o que quiser e nunca quebra
    uma leitura.
    """
    lido = bruto.decode("utf-8", errors="replace")
    if terminador and lido.endswith(terminador):
        return lido[: -len(terminador)]
    return lido


def _hospedeiro(endereco: str) -> str:
    """An IPv6 inside a URL lives in brackets, or the colons of the address read as a port.

    Um IPv6 dentro de uma URL mora entre colchetes, ou os dois pontos do endereço viram porta.
    """
    return f"[{endereco}]" if ":" in endereco else endereco


async def _fechar(escritor: asyncio.StreamWriter) -> None:
    escritor.close()
    # Why: a cancellation of the poll must reach the caller, so only the noise of a peer that
    # already went away is swallowed here.
    # Por que: um cancelamento do poll precisa chegar a quem chamou, então só o ruído de um par
    # que já foi embora é engolido aqui.
    with suppress(OSError, TimeoutError):
        await escritor.wait_closed()

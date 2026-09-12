# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The relay: a rendezvous between a hub that dialled out and a browser that came in.

The whole idea in three sentences. A hub with an open window opens a WebSocket to here and
gets back an ADDRESS of its own, random and good for that window. Whoever holds that address
reaches that hub and no other, through this process, which forwards the request down the
socket the hub already opened. When the hub goes away the address stops existing.

What this service is NOT, on purpose:

- it is not an authority over hubs. No hub is registered here and none is authenticated: the
  address IS the secret, so a stranger who opens a socket only ever receives the traffic
  addressed to the address this gave them, which nobody else has. What that buys is the thing
  the fleet asked for: a hub is sold, plugged in, and works, with no step at the factory;
- it is not a place where a decision about the customer is taken. The window, the code of the
  window, the password and the second factor are all checked by the HUB, at the other end of
  the socket. This one forwards bytes and counts them;
- it is not a general proxy. It reaches exactly the sockets that dialled in, and there is no
  field anywhere in it that names a host.
"""

import asyncio
import ipaddress
import logging
import secrets
from collections.abc import Iterable

from aiohttp import WSMsgType, web

from relay import protocolo
from relay.cadastro import Cadastro, Indisponivel, home_valido

log = logging.getLogger("relay")

# The address of a window: unguessable, and the only thing that reaches one hub.
LETRAS_DO_ENDERECO = 26
PREFIXO = "/h/"

# A hub answers a request of a panel in milliseconds; ten seconds is a hub whose link died
# in the middle, and the browser gets a gateway error instead of hanging.
PRAZO_DA_RESPOSTA_S = 10.0
# A socket that says nothing for this long is a hub that is not there any more. The hub
# pings well inside it.
PRAZO_DO_SOCKET_S = 90.0
# How long a hub has to say which home it belongs to; a real one says it at once.
PRAZO_DO_OLA_S = 10.0
# How many requests one hub may have in flight, which is what keeps one browser reloading a
# panel from turning into unbounded memory here.
EM_VOO_POR_HUB = 24
# How many hubs one address of the internet may register, so nobody uses this as free
# infrastructure of their own. Sixty four and not a handful: the carriers of the country put
# whole neighbourhoods behind one public address, and a fleet in one city shares it.
HUBS_POR_ENDERECO = 64
# How many sockets this process holds at all, so a flood of fake hubs ends in a refusal
# that is counted instead of in a process the kernel kills.
HUBS_MAXIMO = 20_000

CABECALHOS = {
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "frame-ancestors 'none'",
    "Server": "tuya-ip-hub-relay",
}

HUBS = web.AppKey("hubs", dict)
POR_ENDERECO = web.AppKey("por_endereco", dict)
BASE = web.AppKey("base", str)
PROXIES = web.AppKey("proxies", tuple)
# The registry of homes, or None for a relay that serves any hub that dials.
CADASTRO = web.AppKey("cadastro", Cadastro | None)


class Hub:
    """One hub on the other end of a socket, and the requests waiting on it."""

    def __init__(self, socket: web.WebSocketResponse, endereco: str) -> None:
        self.socket = socket
        self.endereco = endereco
        self._proximo = 0
        self._esperando: dict[int, asyncio.Future] = {}

    def em_voo(self) -> int:
        return len(self._esperando)

    def reservar(self) -> tuple[int, asyncio.Future]:
        self._proximo += 1
        espera: asyncio.Future = asyncio.get_running_loop().create_future()
        self._esperando[self._proximo] = espera
        return self._proximo, espera

    def entregar(self, numero: int, quadro: dict) -> None:
        espera = self._esperando.pop(numero, None)
        if espera is not None and not espera.done():
            espera.set_result(quadro)

    def soltar(self, numero: int) -> None:
        self._esperando.pop(numero, None)

    def encerrar(self) -> None:
        for espera in self._esperando.values():
            if not espera.done():
                espera.cancel()
        self._esperando.clear()


def gerar_endereco() -> str:
    return secrets.token_urlsafe(LETRAS_DO_ENDERECO)[:LETRAS_DO_ENDERECO]


def redes_de(texto: str) -> tuple:
    """The proxies in front of this service, as addresses or networks, from one setting."""
    redes = []
    for parte in texto.split(","):
        parte = parte.strip()
        if not parte:
            continue
        # A bare address is a network of one; anything that is neither is a typo that would
        # otherwise trust nobody in silence, so it is refused where it is set.
        redes.append(ipaddress.ip_network(parte, strict=False))
    return tuple(redes)


def _de_proxy(endereco: str, proxies: Iterable) -> bool:
    try:
        ip = ipaddress.ip_address(endereco)
    except ValueError:
        return False
    return any(ip in rede for rede in proxies)


def quem_chegou(request: web.Request, proxies: Iterable) -> str:
    """Where a request really came from: the peer, unless the peer is a declared proxy.

    Behind a reverse proxy every peer is the proxy, and both counts here would collapse into
    one bucket: the whole fleet would share one allowance of hubs, and every browser of the
    internet would share one allowance of failed logins on every hub. The proxy appends the
    address it saw to X-Forwarded-For, so the client is the LAST hop that is not a proxy, and
    everything left of it is text the client wrote.
    """
    par = request.remote or ""
    if not par or not _de_proxy(par, proxies):
        return par
    saltos: list[str] = []
    for linha in request.headers.getall("X-Forwarded-For", []):
        saltos.extend(parte.strip() for parte in linha.split(","))
    for bruto in reversed(saltos):
        try:
            ip = ipaddress.ip_address(bruto)
        except ValueError:
            continue
        if not _de_proxy(str(ip), proxies):
            return str(ip)
    return par


def _com_cabecalhos(resposta: web.StreamResponse) -> web.StreamResponse:
    resposta.headers.update(CABECALHOS)
    return resposta


# What each refusal means to a person. A browser that lands here got a link that stopped
# working, and "sem_hub" on a white page tells them nothing about what to do next.
RECADOS = {
    "sem_hub": (
        "Esta janela está fechada",
        "O acesso remoto dura 24 horas e pode ser fechado antes disso pelo painel ou pelo "
        "aplicativo. Peça uma janela nova a quem abriu esta: o endereço muda a cada uma.",
        "This window is closed",
        "A remote session lasts 24 hours and can be closed earlier from the panel or from "
        "the app. Ask whoever opened this one for a new window: the address changes with it.",
    ),
    "hub_demorou": (
        "O hub não respondeu",
        "A janela está aberta, mas o hub não respondeu a tempo. Pode ser a internet da "
        "instalação; tente de novo em alguns segundos.",
        "The hub did not answer",
        "The window is open, but the hub did not answer in time. It may be the link of the "
        "installation; try again in a few seconds.",
    ),
    "hub_calado": (
        "O hub saiu do ar",
        "A janela está aberta e a conexão com o hub caiu. Ele volta sozinho quando a "
        "internet da instalação voltar.",
        "The hub went quiet",
        "The window is open and the connection to the hub dropped. It comes back on its own "
        "when the link of the installation does.",
    ),
    "pedidos_demais": (
        "Muitos pedidos ao mesmo tempo",
        "Espere um instante e recarregue a página.",
        "Too many requests at once",
        "Wait a moment and reload the page.",
    ),
}
PADRAO_DO_RECADO = (
    "Não foi possível continuar",
    "Tente de novo em alguns instantes.",
    "This did not go through",
    "Try again in a moment.",
)

PAGINA = """<!doctype html><html lang="pt-BR"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,nofollow"><title>{titulo}</title>
<style>
:root{{color-scheme:light dark}}
body{{margin:0;min-height:100vh;display:grid;place-items:center;padding:1.5rem;
 font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;background:#f5f6f8;color:#101418}}
main{{max-width:26rem;background:#fff;border:1px solid #d8dde3;border-radius:14px;padding:1.5rem}}
h1{{margin:0 0 .6rem;font-size:1.25rem}}
p{{margin:0 0 .8rem}}
.en{{color:#5b6672;font-size:.9rem;margin:0}}
@media (prefers-color-scheme:dark){{
 body{{background:#14171a;color:#e8ecef}}
 main{{background:#1d2126;border-color:#2b3138}}
 .en{{color:#9aa4ae}}}}
</style>
<main><h1>{titulo}</h1><p>{texto}</p>
<p class="en"><strong>{titulo_en}</strong> {texto_en}</p></main>
"""


def _quer_pagina(request: web.Request) -> bool:
    """A browser asks for html; anything else gets the code it can act on."""
    return "text/html" in request.headers.get("Accept", "")


def _erro(status: int, codigo: str, request: web.Request | None = None) -> web.Response:
    if request is not None and _quer_pagina(request):
        titulo, texto, titulo_en, texto_en = RECADOS.get(codigo, PADRAO_DO_RECADO)
        return _com_cabecalhos(
            web.Response(
                status=status,
                content_type="text/html",
                charset="utf-8",
                text=PAGINA.format(
                    titulo=titulo, texto=texto, titulo_en=titulo_en, texto_en=texto_en
                ),
            )
        )
    return _com_cabecalhos(web.json_response({"ok": False, "code": codigo}, status=status))


async def saude(request: web.Request) -> web.Response:
    """What a monitor asks, and the only number this service publishes about itself."""
    return _com_cabecalhos(web.json_response({"ok": True, "hubs": len(request.app[HUBS])}))


async def porta_do_hub(request: web.Request) -> web.WebSocketResponse:
    """The socket a hub opens when its window is armed, and the address it gets for it."""
    par = quem_chegou(request, request.app[PROXIES])
    por_endereco = request.app[POR_ENDERECO]
    if len(request.app[HUBS]) >= HUBS_MAXIMO or por_endereco.get(par, 0) >= HUBS_POR_ENDERECO:
        return _erro(429, "hubs_demais")  # type: ignore[return-value]
    socket = web.WebSocketResponse(
        heartbeat=PRAZO_DO_SOCKET_S / 3, max_msg_size=protocolo.QUADRO_MAXIMO
    )
    await socket.prepare(request)
    # Counted from the handshake, proof or no proof, so a flood of sockets that never prove
    # anything is bounded by the same number as the hubs of one address.
    por_endereco[par] = por_endereco.get(par, 0) + 1
    try:
        cadastro = request.app[CADASTRO]
        if cadastro is not None and not await _conferir_home(socket, cadastro):
            return socket
        endereco = gerar_endereco()
        hub = Hub(socket, endereco)
        request.app[HUBS][endereco] = hub
        log.info("a hub dialled in and took an address (%d open)", len(request.app[HUBS]))
        await socket.send_str(
            protocolo.escrever(
                {
                    "tipo": protocolo.PRONTO,
                    "versao": protocolo.VERSAO,
                    "endereco": endereco,
                    "url": f"{request.app[BASE]}{PREFIXO}{endereco}/",
                }
            )
        )
        try:
            await _ouvir(hub, socket)
        finally:
            request.app[HUBS].pop(endereco, None)
            hub.encerrar()
            log.info("a hub went away (%d open)", len(request.app[HUBS]))
    finally:
        por_endereco[par] = max(0, por_endereco.get(par, 1) - 1)
        if not por_endereco[par]:
            por_endereco.pop(par, None)
    return socket


async def _conferir_home(socket: web.WebSocketResponse, cadastro: Cadastro) -> bool:
    """The hub says which home it belongs to, and the registry of the maker says whether
    that home is served. Nothing about the customer is decided here: the rule is whatever
    the maker keeps in its registry."""
    try:
        async with asyncio.timeout(PRAZO_DO_OLA_S):
            mensagem = await socket.receive()
    except TimeoutError:
        await socket.close(code=4408, message=b"ola_demorou")
        return False
    if mensagem.type is not WSMsgType.TEXT:
        return False
    try:
        quadro = protocolo.ler(mensagem.data)
    except protocolo.QuadroInvalido:
        await socket.close(code=1003, message=b"bad frame")
        return False
    home = quadro.get("home")
    if quadro["tipo"] != protocolo.OLA or not home_valido(home):
        await socket.close(code=protocolo.RECUSADO, message=b"home_nao_cadastrado")
        return False
    try:
        cadastrado = await cadastro.cadastrado(home)
    except Indisponivel as erro:
        # Neither yes nor no: the hub dials again in a moment, without the long wait a
        # refusal earns, and the log says why every hub is being turned away.
        log.error("the registry could not be asked: %s", erro)
        await socket.close(code=protocolo.INDISPONIVEL, message=b"cadastro_indisponivel")
        return False
    if not cadastrado:
        # Nothing about which home, on purpose: a stranger probing ids learns only that
        # this one did not open the door.
        log.info("a hub was refused: its home is not registered")
        await socket.close(code=protocolo.RECUSADO, message=b"home_nao_cadastrado")
        return False
    return True


async def _ouvir(hub: Hub, socket: web.WebSocketResponse) -> None:
    """Every frame a hub sends: an answer to something this asked, and nothing else."""
    async for mensagem in socket:
        if mensagem.type is not WSMsgType.TEXT:
            await socket.close(code=1003, message=b"text only")
            return
        try:
            quadro = protocolo.ler(mensagem.data)
            if quadro["tipo"] != protocolo.RESPOSTA:
                continue
            hub.entregar(protocolo.numero_de(quadro.get("id")), quadro)
        except protocolo.QuadroInvalido as erro:
            log.warning("a hub sent a frame this does not speak: %s", erro)
            await socket.close(code=1003, message=b"bad frame")
            return


async def encaminhar(request: web.Request) -> web.StreamResponse:
    """One request of a browser, carried down the socket of the hub that owns this address."""
    endereco = request.match_info.get("endereco", "")
    hub = request.app[HUBS].get(endereco)
    if hub is None:
        # A window that closed and an address nobody ever had look the same from here, on
        # purpose: this answers the same thing to a guess and to a hub that went home.
        return _erro(404, "sem_hub", request)
    # The raw path, so a percent escape reaches the hub as the browser wrote it: decoded
    # here, an encoded slash would become a real one and name a different route there.
    caminho = request.rel_url.raw_path_qs[len(PREFIXO) + len(endereco) :]
    if not caminho.startswith("/"):
        # The address without its closing slash: the browser would then resolve every
        # relative asset of the panel one level up, against this service instead of against
        # the hub, and the page would load with no style and no script.
        return _com_cabecalhos(
            web.Response(status=308, headers={"Location": f"{PREFIXO}{endereco}/{caminho}"})
        )
    if len(caminho) > protocolo.CAMINHO_MAXIMO:
        return _erro(414, "caminho_grande", request)
    if hub.em_voo() >= EM_VOO_POR_HUB:
        return _erro(429, "pedidos_demais", request)
    # The slot is taken BEFORE the body is read, so the bodies in flight for one hub are
    # bounded by the same number as its requests.
    numero, espera = hub.reservar()
    try:
        corpo = await request.content.read(protocolo.PEDIDO_MAXIMO + 1)
    except (ConnectionError, asyncio.CancelledError):
        hub.soltar(numero)
        raise
    if len(corpo) > protocolo.PEDIDO_MAXIMO:
        hub.soltar(numero)
        return _erro(413, "corpo_grande", request)
    quadro = {
        "tipo": protocolo.PEDIDO,
        "id": numero,
        "metodo": request.method,
        "caminho": caminho,
        # Where the browser came from, written by this service and never by the browser:
        # it is what lets the hub count the failed logins of one person instead of putting
        # every request of the relay in one bucket.
        "cabecalhos": {
            **protocolo.cabecalhos_de(dict(request.headers)),
            "X-Iphub-Cliente": quem_chegou(request, request.app[PROXIES]),
        },
        "corpo": protocolo.corpo_para(corpo),
    }
    try:
        await hub.socket.send_str(protocolo.escrever(quadro))
    except (ConnectionError, RuntimeError, ValueError):
        hub.soltar(numero)
        return _erro(502, "hub_calado", request)
    try:
        async with asyncio.timeout(PRAZO_DA_RESPOSTA_S):
            resposta = await espera
    except (TimeoutError, asyncio.CancelledError):
        hub.soltar(numero)
        return _erro(504, "hub_demorou", request)
    return _resposta_do_quadro(resposta)


def _resposta_do_quadro(quadro: dict) -> web.StreamResponse:
    status = quadro.get("status")
    if not isinstance(status, int) or not 100 <= status <= 599:
        return _erro(502, "resposta_invalida")
    try:
        corpo = protocolo.corpo_de(quadro.get("corpo"))
    except protocolo.QuadroInvalido:
        return _erro(502, "resposta_invalida")
    resposta = web.Response(
        status=status, body=corpo, headers=protocolo.cabecalhos_de(quadro.get("cabecalhos"))
    )
    # The hub already answered with its own length and encoding; aiohttp writes them again.
    resposta.headers.popall("Content-Length", None)
    resposta.headers.popall("Transfer-Encoding", None)
    return _com_cabecalhos(resposta)


async def _sem_rota(request: web.Request) -> web.Response:
    """Everything this service does not serve, answered the same way and with the headers."""
    del request
    return _erro(404, "nao_encontrado")


async def _fechar_cadastro(app: web.Application) -> None:
    cadastro = app[CADASTRO]
    if cadastro is not None:
        await cadastro.fechar()


def criar_app(
    base: str = "", proxies: Iterable = (), cadastro: Cadastro | None = None
) -> web.Application:
    app = web.Application()
    app[HUBS] = {}
    app[POR_ENDERECO] = {}
    app[BASE] = base.rstrip("/")
    app[PROXIES] = tuple(proxies)
    app[CADASTRO] = cadastro
    app.on_cleanup.append(_fechar_cadastro)
    app.router.add_get("/saude", saude)
    app.router.add_get("/hub", porta_do_hub)
    app.router.add_route("*", "/h/{endereco}{cauda:.*}", encaminhar)
    app.router.add_route("*", "/{resto:.*}", _sem_rota)
    return app

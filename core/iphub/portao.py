# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Gate in front of every route: Host allowlist, security headers, JSON errors.

Portão na frente de toda rota: lista de Host, cabeçalhos de segurança, erros em JSON.
"""

import ipaddress
import logging
import re
from collections.abc import Awaitable, Callable
from types import MappingProxyType

from aiohttp import web
from aiohttp.http import HttpVersion11

log = logging.getLogger("iphub.portao")

CABECALHOS = MappingProxyType(
    {
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "frame-ancestors 'none'",
    }
)

# Why: the default Server header hands the LAN the exact Python and aiohttp versions.
# Por que: o cabeçalho Server padrão entrega à LAN as versões exatas de Python e aiohttp.
SERVIDOR = "tuya-ip-hub"

CODIGOS_HTTP = MappingProxyType(
    {
        404: "nao_encontrado",
        405: "metodo_nao_permitido",
        421: "host_nao_permitido",
        500: "erro_interno",
    }
)

_PORTA = re.compile(r"^[0-9]{1,5}$")
_CABECALHOS_DO_CORPO = frozenset({"Content-Type", "Content-Length"})

Handler = Callable[[web.Request], Awaitable[web.StreamResponse]]
Middleware = Callable[[web.Request, Handler], Awaitable[web.StreamResponse]]
TrataExpect = Callable[[web.Request], Awaitable[web.StreamResponse | None]]


def _separar_porta(host: str) -> str | None:
    if host.startswith("["):
        fim = host.find("]")
        if fim < 0:
            return None
        nome, resto = host[: fim + 1], host[fim + 1 :]
    elif host.count(":") == 1:
        nome, _, resto = host.partition(":")
        resto = ":" + resto
    else:
        nome, resto = host, ""
    if resto:
        if not (resto.startswith(":") and _PORTA.match(resto[1:])):
            return None
        if not 1 <= int(resto[1:]) <= 65535:
            return None
    return nome or None


def host_permitido(host: str | None, hosts_permitidos: frozenset[str]) -> bool:
    """True for an IP literal, "localhost" or an allowlisted name, each with optional port.

    Verdadeiro para IP literal, "localhost" ou nome da lista, cada um com porta opcional.
    """
    if not host:
        return False
    # Why: only ASCII blanks are trimmed; any other whitespace is not a Host a browser sends.
    # Por que: só brancos ASCII são aparados; outro espaço não é Host que um navegador envia.
    nome = _separar_porta(host.strip(" \t"))
    if nome is None:
        return False
    if nome.startswith("[") and nome.endswith("]"):
        try:
            ipaddress.IPv6Address(nome[1:-1])
        except ValueError:
            return False
        return True
    try:
        ipaddress.IPv4Address(nome)
    except ValueError:
        pass
    else:
        return True
    nome = nome.lower()
    return nome == "localhost" or nome in {h.lower() for h in hosts_permitidos}


def pedido_permitido(request: web.Request, hosts_permitidos: frozenset[str]) -> bool:
    """Both views of the authority are checked: the header and the one aiohttp resolves.

    As duas visões da autoridade são checadas: o cabeçalho e a que o aiohttp resolve.
    """
    # Why: an absolute-form request target sets request.host to its own authority while the
    # Host header stays innocent, so a handler reading request.host would trust the attacker.
    # Por que: um alvo em forma absoluta põe a autoridade dele em request.host enquanto o
    # cabeçalho Host fica inocente, então um handler que lesse request.host confiaria no atacante.
    return host_permitido(request.headers.get("Host"), hosts_permitidos) and host_permitido(
        request.host, hosts_permitidos
    )


def cabecalhos_completos() -> dict[str, str]:
    return {**CABECALHOS, "Server": SERVIDOR}


def _aplicar_cabecalhos(headers) -> None:
    headers.update(CABECALHOS)
    headers["Server"] = SERVIDOR


def resposta_erro(status: int, code: str, headers: dict[str, str] | None = None) -> web.Response:
    return web.json_response({"ok": False, "code": code}, status=status, headers=headers)


def criar_tratar_expect(hosts_permitidos: frozenset[str]) -> TrataExpect:
    """Expect runs before any middleware, so the gate is repeated here, headers included.

    Expect roda antes de qualquer middleware, então o portão é repetido aqui, com cabeçalhos.
    """

    async def tratar_expect(request: web.Request) -> web.StreamResponse | None:
        if not pedido_permitido(request, hosts_permitidos):
            return resposta_erro(421, "host_nao_permitido", cabecalhos_completos())
        if request.version != HttpVersion11:
            return None
        if request.headers.get("Expect", "").lower() == "100-continue":
            await request.writer.write(b"HTTP/1.1 100 Continue\r\n\r\n")
            # Why: the body has not started, so the interim reply must not count as output.
            # Por que: o corpo não começou, então a resposta interina não pode contar como saída.
            request.writer.output_size = 0
            return None
        return resposta_erro(417, "erro_http", cabecalhos_completos())

    return tratar_expect


def rota_get(
    app: web.Application, caminho: str, handler: Handler, tratar_expect: TrataExpect
) -> None:
    """Registers a GET route with the project's Expect handler.

    Registra uma rota GET com o tratador de Expect do projeto.
    """
    app.router.add_get(caminho, handler, expect_handler=tratar_expect)


def registrar_curinga(app: web.Application, tratar_expect: TrataExpect) -> None:
    """Last resource, so no request is ever answered by the router's built in route.

    Último recurso, para nenhuma requisição ser respondida pela rota embutida do roteador.
    """

    async def sem_rota(request: web.Request) -> web.StreamResponse:
        # Why: without this route aiohttp answers Expect and the error itself, outside the
        # middlewares, in plain text and with its own Server banner.
        # Por que: sem esta rota o aiohttp responde o Expect e o próprio erro, fora dos
        # middlewares, em texto puro e com o banner de Server dele.
        permitidos: set[str] = set()
        atual = request.match_info.route.resource
        for recurso in request.app.router.resources():
            if recurso is atual:
                continue
            _, metodos = await recurso.resolve(request)
            permitidos.update(metodos)
        if permitidos:
            raise web.HTTPMethodNotAllowed(request.method, sorted(permitidos))
        raise web.HTTPNotFound()

    # Why: the scoped DOTALL flag is what makes the pattern match a path carrying a newline;
    # a bare ".*" leaves those requests to the router's built in route, outside the gate.
    # Por que: a flag DOTALL escopada é o que faz o padrão casar caminho com quebra de linha;
    # um ".*" pelado deixaria essas requisições na rota embutida do roteador, fora do portão.
    app.router.add_route("*", "/{resto:(?s:.*)}", sem_rota, expect_handler=tratar_expect)


def criar_middleware_host(hosts_permitidos: frozenset[str]) -> Middleware:
    """Middleware answering 421 to any Host outside the allowlist (closes DNS rebinding).

    Middleware que responde 421 a qualquer Host fora da lista (fecha DNS rebinding).
    """

    @web.middleware
    async def middleware_host(request: web.Request, handler: Handler) -> web.StreamResponse:
        if not pedido_permitido(request, hosts_permitidos):
            return resposta_erro(421, "host_nao_permitido")
        return await handler(request)

    return middleware_host


async def gravar_cabecalhos(request: web.Request, resposta: web.StreamResponse) -> None:
    """Last line of defence: the signal fires for every response the application prepares.

    Última linha de defesa: o sinal dispara para toda resposta que a aplicação prepara.
    """
    _aplicar_cabecalhos(resposta.headers)


@web.middleware
async def middleware_cabecalhos(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        resposta = await handler(request)
    except web.HTTPException as exc:
        _aplicar_cabecalhos(exc.headers)
        raise
    except Exception:
        # Why: nothing may leave the gate without the headers, not even a bug in a middleware.
        # Por que: nada sai do portão sem os cabeçalhos, nem mesmo um defeito num middleware.
        log.exception("unhandled error on %s %s", request.method, request.raw_path)
        resposta = resposta_erro(500, "erro_interno")
    _aplicar_cabecalhos(resposta.headers)
    return resposta


@web.middleware
async def middleware_erros_json(request: web.Request, handler: Handler) -> web.StreamResponse:
    try:
        return await handler(request)
    except web.HTTPException as exc:
        if exc.content_type == "application/json":
            raise
        # Why: "Allow" on 405 and "Location" on 3xx carry meaning; the text body does not.
        # Por que: "Allow" no 405 e "Location" no 3xx têm significado; o corpo em texto não.
        extras = {k: v for k, v in exc.headers.items() if k not in _CABECALHOS_DO_CORPO}
        return resposta_erro(exc.status, CODIGOS_HTTP.get(exc.status, "erro_http"), extras)
    except Exception:
        # Why: raw_path keeps the percent encoding, so a newline cannot forge a log line.
        # Por que: raw_path mantém o percent encoding, então uma quebra de linha não forja log.
        log.exception("unhandled error on %s %s", request.method, request.raw_path)
        return resposta_erro(500, "erro_interno")

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The frames between a hub and the relay, and the rules that keep them cheap to read.

One WebSocket per hub, JSON frames, one request in flight identified by a number. The body
travels in base64 because a frame is text and a panel serves images and fonts; the ceiling is
here and not in the handler, so a hub that went mad cannot spend the memory of the relay.

What this protocol deliberately does NOT carry: streaming. A response is one frame. The panel
of the hub is JSON and a bundle of a few hundred kilobytes, and a chunked protocol would be
three times this code to serve the same thing.
"""

import base64
import binascii
import json
from typing import Any

VERSAO = 1

# The first frame of a hub says which home of the app it belongs to; the relay answers with
# the address, or closes. Then requests go down and answers come back.
OLA = "ola"
PRONTO = "pronto"
PEDIDO = "pedido"
RESPOSTA = "resposta"

# The close code a hub reads as "your home is not one this relay serves", after which it
# waits a good while; every other close is a link that fell, and it dials again soon.
RECUSADO = 4403
INDISPONIVEL = 4503

# What a browser sends a hub is a login or a form, and the largest thing a hub accepts is a
# scene of a few kilobytes; a megabyte leaves room and keeps a flood of bodies from being a
# flood of memory here. What a hub sends back is at most the bundle of its panel.
PEDIDO_MAXIMO = 1024 * 1024
CORPO_MAXIMO = 8 * 1024 * 1024
QUADRO_MAXIMO = 12 * 1024 * 1024
CAMINHO_MAXIMO = 2048
CABECALHOS_MAXIMO = 64

# What never travels from the relay to the hub, because the hub decides them itself and a
# forged one would be a way to dress a request of the internet as a request of the machine.
CABECALHOS_PROIBIDOS = frozenset(
    {
        "host",
        "x-iphub-relay",
        "x-iphub-cliente",
        "x-forwarded-for",
        "x-forwarded-host",
        "x-forwarded-proto",
        "cf-access-jwt-assertion",
        "cf-ray",
        "cf-connecting-ip",
    }
)


class QuadroInvalido(ValueError):
    """The frame is not what this protocol says; the socket that sent it is closed."""


def escrever(quadro: dict[str, Any]) -> str:
    return json.dumps(quadro, separators=(",", ":"))


def ler(bruto: str) -> dict[str, Any]:
    if len(bruto) > QUADRO_MAXIMO:
        raise QuadroInvalido("frame over the ceiling")
    try:
        quadro = json.loads(bruto)
    except (ValueError, RecursionError) as erro:
        raise QuadroInvalido("frame is not json") from erro
    if not isinstance(quadro, dict) or not isinstance(quadro.get("tipo"), str):
        raise QuadroInvalido("frame carries no tipo")
    return quadro


def corpo_para(bruto: bytes) -> str:
    return base64.b64encode(bruto).decode("ascii")


def corpo_de(bruto: object) -> bytes:
    if bruto in (None, ""):
        return b""
    if not isinstance(bruto, str):
        raise QuadroInvalido("body is not text")
    try:
        corpo = base64.b64decode(bruto, validate=True)
    except (binascii.Error, ValueError) as erro:
        raise QuadroInvalido("body is not base64") from erro
    if len(corpo) > CORPO_MAXIMO:
        raise QuadroInvalido("body over the ceiling")
    return corpo


def cabecalhos_de(bruto: object) -> dict[str, str]:
    """The headers of a frame, without the ones the other side is not allowed to choose."""
    if not isinstance(bruto, dict):
        return {}
    limpos: dict[str, str] = {}
    for nome, valor in list(bruto.items())[:CABECALHOS_MAXIMO]:
        if not isinstance(nome, str) or not isinstance(valor, str):
            continue
        if nome.lower() in CABECALHOS_PROIBIDOS:
            continue
        # A newline in a header is a second header, which is how a response is forged.
        if "\n" in valor or "\r" in valor or "\n" in nome or "\r" in nome:
            continue
        limpos[nome] = valor
    return limpos


def numero_de(bruto: object) -> int:
    if not isinstance(bruto, int) or isinstance(bruto, bool) or bruto < 0:
        raise QuadroInvalido("id is not a number")
    return bruto

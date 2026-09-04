# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Reading the whole answer of a device, up to a ceiling.

Section 6 says a driver reads what the device answered. One call to read(n) on an aiohttp
stream returns only what the buffer already holds, so an answer that arrived in more than one
TCP segment came out truncated: a status object cut in half stops being json and the device
reads as broken while it is answering perfectly. The ceiling is still enforced, because the
answer of a device on the customer LAN is never allowed to size the memory of the hub.

Ler a resposta inteira de um aparelho, até um teto.

A seção 6 diz que um driver lê o que o aparelho respondeu. Uma chamada de read(n) num fluxo
do aiohttp devolve só o que o buffer já tem, então uma resposta que chegou em mais de um
segmento TCP saía truncada: um objeto de estado cortado ao meio deixa de ser json e o
aparelho lê como quebrado enquanto responde perfeitamente. O teto continua valendo, porque a
resposta de um aparelho na LAN do cliente nunca pode dimensionar a memória do hub.
"""

from typing import Protocol


class Fluxo(Protocol):
    """What this module needs of a response body, and nothing more.

    O que este módulo precisa de um corpo de resposta, e nada mais.
    """

    async def read(self, n: int = -1) -> bytes: ...


async def inteiro(fluxo: Fluxo, maximo: int) -> bytes:
    """Every byte the device answered, stopping at maximo.

    Cada byte que o aparelho respondeu, parando em maximo.
    """
    bruto = bytearray()
    while len(bruto) < maximo:
        pedaco = await fluxo.read(maximo - len(bruto))
        if not pedaco:
            break
        bruto += pedaco
    return bytes(bruto)

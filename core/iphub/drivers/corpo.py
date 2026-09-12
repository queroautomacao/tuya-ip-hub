# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Reading the whole answer of a device, up to a ceiling.

 says a driver reads what the device answered. One call to read(n) on an aiohttp
stream returns only what the buffer already holds, so an answer that arrived in more than one
TCP segment came out truncated: a status object cut in half stops being json and the device
reads as broken while it is answering perfectly. The ceiling is still enforced, because the
answer of a device on the customer LAN is never allowed to size the memory of the hub.
"""

from typing import Protocol


class Fluxo(Protocol):
    """What this module needs of a response body, and nothing more."""

    async def read(self, n: int = -1) -> bytes: ...


async def inteiro(fluxo: Fluxo, maximo: int) -> bytes:
    """Every byte the device answered, stopping at maximo."""
    bruto = bytearray()
    while len(bruto) < maximo:
        pedaco = await fluxo.read(maximo - len(bruto))
        if not pedaco:
            break
        bruto += pedaco
    return bytes(bruto)

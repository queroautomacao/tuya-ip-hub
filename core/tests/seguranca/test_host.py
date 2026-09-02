# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9: a Host outside the allowlist gets 421 (closes DNS rebinding from outside the LAN).

Seção 9: Host fora da lista recebe 421 (fecha DNS rebinding de fora da LAN).
"""

import asyncio

import pytest

from iphub.app import criar_app

RECUSADO = {"ok": False, "code": "host_nao_permitido"}


@pytest.mark.parametrize("host", ["hub.local", "evil.example.com", "evil.example.com:8080", ""])
async def test_host_fora_da_lista_e_421(cliente, host):
    resposta = await cliente.get("/health", headers={"Host": host})
    assert resposta.status == 421
    assert await resposta.json() == RECUSADO


@pytest.mark.parametrize(
    "host", ["192.0.2.10:8080", "192.0.2.10", "[::1]:8080", "localhost", "LOCALHOST:8080"]
)
async def test_ip_literal_e_localhost_passam(cliente, host):
    resposta = await cliente.get("/health", headers={"Host": host})
    assert resposta.status == 200


async def test_nome_da_lista_passa(aiohttp_client, amb):
    cliente = await aiohttp_client(criar_app(amb, hosts_permitidos=frozenset({"hub.local"})))
    assert (await cliente.get("/health", headers={"Host": "hub.local"})).status == 200
    assert (await cliente.get("/health", headers={"Host": "hub.local:8080"})).status == 200
    assert (await cliente.get("/health", headers={"Host": "evil.example.com"})).status == 421


async def test_host_ausente_e_421(cliente):
    # Why: the client library always adds Host, so the bare request goes over a raw socket.
    # Por que: a biblioteca cliente sempre adiciona Host, então a requisição crua vai por socket.
    leitor, escritor = await asyncio.open_connection(cliente.host, cliente.port)
    escritor.write(b"GET /health HTTP/1.0\r\n\r\n")
    await escritor.drain()
    bruto = await asyncio.wait_for(leitor.read(), timeout=5)
    escritor.close()
    await escritor.wait_closed()

    linha_de_status, _, resto = bruto.partition(b"\r\n")
    assert linha_de_status.split(b" ")[1] == b"421"
    cabecalhos, _, corpo = resto.partition(b"\r\n\r\n")
    assert b"X-Frame-Options: DENY" in cabecalhos
    assert b'"host_nao_permitido"' in corpo


async def test_421_vale_antes_de_qualquer_rota(cliente):
    for caminho in ("/", "/assets/app.js", "/nao-existe", "/api/setup"):
        resposta = await cliente.get(caminho, headers={"Host": "evil.example.com"})
        assert resposta.status == 421, caminho
        assert await resposta.json() == RECUSADO


async def test_alvo_em_forma_absoluta_com_autoridade_hostil_e_421(cliente):
    # Why: an absolute-form target sets request.host to its own authority while the Host
    # header stays innocent, so the gate has to look at both.
    # Por que: um alvo em forma absoluta põe a autoridade dele em request.host enquanto o
    # cabeçalho Host fica inocente, então o portão precisa olhar os dois.
    leitor, escritor = await asyncio.open_connection(cliente.host, cliente.port)
    escritor.write(
        b"GET http://evil.example.com/health HTTP/1.1\r\n"
        b"Host: 127.0.0.1\r\nConnection: close\r\n\r\n"
    )
    await escritor.drain()
    bruto = await asyncio.wait_for(leitor.read(), timeout=5)
    escritor.close()
    await escritor.wait_closed()
    assert bruto.split(b" ")[1] == b"421"
    assert b'"host_nao_permitido"' in bruto

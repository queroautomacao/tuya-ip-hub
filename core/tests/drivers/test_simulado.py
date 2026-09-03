# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 12: the fake devices themselves, so a driver test never debugs its own bench.

Seção 12: os próprios aparelhos falsos, para um teste de driver nunca depurar a bancada.
"""

import asyncio
import socket
from contextlib import suppress

import pytest
from aiohttp import ClientSession

from iphub.drivers.simulado import (
    PRAZO_PARADA_S,
    RespondedorSsdp,
    ServidorHttp,
    ServidorLinha,
)

ESPERA_CURTA_S = 0.2
# Why: the teardown has a deadline of its own, so the test waits longer than it to tell a
# slow stop from one that never returns.
# Por que: a parada tem prazo próprio, então o teste espera mais que ele para separar uma
# parada lenta de uma que nunca volta.
PRAZO_TESTE_S = PRAZO_PARADA_S * 2
SAUDACAO = b"PJLINK 0\r"
USN = "uuid:0000-simulado::upnp:rootdevice"


def _url(aparelho: ServidorHttp) -> str:
    return f"http://{aparelho.endereco[0]}:{aparelho.endereco[1]}"


async def _perguntar(endereco: tuple[str, int], st: str, timeout_s: float) -> list[bytes]:
    """An M-SEARCH from a plain socket, so the responder is tested without the discovery.

    Um M-SEARCH de um socket puro, para o respondedor ser testado sem a descoberta.
    """
    laco = asyncio.get_running_loop()
    soquete = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    soquete.setblocking(False)
    soquete.bind(("127.0.0.1", 0))
    pedido = f'M-SEARCH * HTTP/1.1\r\nMAN: "ssdp:discover"\r\nST: {st}\r\n\r\n'
    respostas: list[bytes] = []
    try:
        await laco.sock_sendto(soquete, pedido.encode("ascii"), endereco)
        fim = laco.time() + timeout_s
        while laco.time() < fim:
            try:
                dados, _ = await asyncio.wait_for(
                    laco.sock_recvfrom(soquete, 4096), fim - laco.time()
                )
            except TimeoutError:
                break
            respostas.append(dados)
    finally:
        soquete.close()
    return respostas


async def test_servidor_de_linha_sauda_e_responde_a_linha_conhecida():
    async with ServidorLinha({b"%1POWR ?": b"%1POWR=1\r"}, saudacao=SAUDACAO) as aparelho:
        leitor, escritor = await asyncio.open_connection(*aparelho.endereco)
        assert await leitor.readuntil(b"\r") == SAUDACAO
        escritor.write(b"%1POWR ?\r")
        await escritor.drain()
        assert await leitor.readuntil(b"\r") == b"%1POWR=1\r"
        escritor.close()
        await escritor.wait_closed()
        assert aparelho.recebidas == [b"%1POWR ?"]


async def test_servidor_de_linha_ignora_a_linha_desconhecida():
    """A device that ignores a command is what makes a timeout in a driver testable.

    Um aparelho que ignora um comando é o que torna testável um tempo esgotado no driver.
    """
    async with ServidorLinha({}) as aparelho:
        leitor, escritor = await asyncio.open_connection(*aparelho.endereco)
        escritor.write(b"NAO EXISTE\r")
        await escritor.drain()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(leitor.readuntil(b"\r"), ESPERA_CURTA_S)
        assert aparelho.recebidas == [b"NAO EXISTE"]
        escritor.close()
        await escritor.wait_closed()


async def test_servidor_de_linha_atrasa_a_resposta():
    async with ServidorLinha({b"OI": b"OK\r"}, atraso_s=ESPERA_CURTA_S * 2) as aparelho:
        leitor, escritor = await asyncio.open_connection(*aparelho.endereco)
        escritor.write(b"OI\r")
        await escritor.drain()
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(leitor.readuntil(b"\r"), ESPERA_CURTA_S)
        escritor.close()
        await escritor.wait_closed()


async def test_dois_servidores_de_linha_convivem():
    async with ServidorLinha({b"A": b"a\r"}) as um, ServidorLinha({b"A": b"b\r"}) as outro:
        assert um.endereco[1] != outro.endereco[1]
        for aparelho, esperado in ((um, b"a\r"), (outro, b"b\r")):
            leitor, escritor = await asyncio.open_connection(*aparelho.endereco)
            escritor.write(b"A\r")
            await escritor.drain()
            assert await leitor.readuntil(b"\r") == esperado
            escritor.close()
            await escritor.wait_closed()


async def test_servidor_http_responde_a_rota_com_query():
    rotas = {"/httpapi.asp?command=getStatusEx": (200, '{"uuid": "abc"}')}
    async with ServidorHttp(rotas) as aparelho:
        async with ClientSession() as sessao:
            url = _url(aparelho)
            async with sessao.get(url + "/httpapi.asp?command=getStatusEx") as resposta:
                assert resposta.status == 200
                assert await resposta.text() == '{"uuid": "abc"}'
            async with sessao.get(url + "/nao-existe") as resposta:
                assert resposta.status == 404
        assert [p.caminho for p in aparelho.pedidos] == [
            "/httpapi.asp?command=getStatusEx",
            "/nao-existe",
        ]


async def test_servidor_http_guarda_metodo_corpo_e_cabecalho():
    async with ServidorHttp({"/comando": (200, "ok")}) as aparelho:
        async with ClientSession() as sessao:
            url = _url(aparelho) + "/comando"
            async with sessao.post(url, data="PWR ON", headers={"X-Teste": "1"}) as resposta:
                assert resposta.status == 200
        pedido = aparelho.pedidos[0]
        assert pedido.metodo == "POST"
        assert pedido.corpo == "PWR ON"
        assert pedido.cabecalhos["X-Teste"] == "1"


async def test_respondedor_ssdp_responde_ao_proprio_st():
    resposta = {"st": "urn:exemplo:1", "usn": USN, "server": "Simulado/1.0"}
    async with RespondedorSsdp((resposta,)) as aparelho:
        recebidas = await _perguntar(aparelho.endereco, "urn:exemplo:1", ESPERA_CURTA_S)
        assert len(recebidas) == 1
        texto = recebidas[0].decode("ascii")
        assert texto.startswith("HTTP/1.1 200 OK\r\n")
        assert f"USN: {USN}\r\n" in texto
        assert "SERVER: Simulado/1.0\r\n" in texto
        assert aparelho.pedidos and b"M-SEARCH" in aparelho.pedidos[0]


async def test_respondedor_ssdp_cala_para_outro_st():
    resposta = {"st": "urn:exemplo:1", "usn": USN, "server": "Simulado/1.0"}
    async with RespondedorSsdp((resposta,)) as aparelho:
        assert await _perguntar(aparelho.endereco, "urn:outro:1", ESPERA_CURTA_S) == []
        assert aparelho.pedidos


async def test_respondedor_ssdp_responde_a_busca_por_tudo():
    respostas = (
        {"st": "urn:exemplo:1", "usn": USN, "server": "Simulado/1.0"},
        {"st": "upnp:rootdevice", "usn": USN, "server": "Simulado/1.0"},
    )
    async with RespondedorSsdp(respostas) as aparelho:
        recebidas = await _perguntar(aparelho.endereco, "ssdp:all", ESPERA_CURTA_S)
        assert len(recebidas) == 2


async def test_parar_com_conexao_aberta_retorna_rapido():
    """A bench that hangs teaches nothing: a test that forgot a connection has to fail.

    Uma bancada que trava não ensina nada: um teste que esqueceu uma conexão tem de falhar.
    """
    aparelho = ServidorLinha({b"A": b"a\r"})
    await aparelho.iniciar()
    leitor, escritor = await asyncio.open_connection(*aparelho.endereco)
    escritor.write(b"A\r")
    await escritor.drain()
    assert await leitor.readuntil(b"\r") == b"a\r"
    await asyncio.wait_for(aparelho.parar(), PRAZO_TESTE_S)
    assert aparelho.conexoes == 1
    escritor.close()
    with suppress(OSError):
        await escritor.wait_closed()


async def test_o_gerenciador_de_contexto_nao_trava_com_conexao_aberta():
    """The shape a driver test uses, with the connection left open on purpose.

    O formato que um teste de driver usa, com a conexão deixada aberta de propósito.
    """
    async with asyncio.timeout(PRAZO_TESTE_S):
        async with ServidorLinha({b"A": b"a\r"}) as aparelho:
            leitor, escritor = await asyncio.open_connection(*aparelho.endereco)
            escritor.write(b"A\r")
            await escritor.drain()
            assert await leitor.readuntil(b"\r") == b"a\r"
    escritor.close()
    with suppress(OSError):
        await escritor.wait_closed()


async def test_parar_o_servidor_http_com_conexao_aberta_retorna_rapido():
    async with ClientSession() as sessao:
        aparelho = ServidorHttp({"/x": (200, "ok")})
        await aparelho.iniciar()
        async with sessao.get(_url(aparelho) + "/x") as resposta:
            assert resposta.status == 200
        await asyncio.wait_for(aparelho.parar(), PRAZO_TESTE_S)

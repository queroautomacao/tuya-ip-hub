# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Presence and naming of an address without a driver, against responders on loopback."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import replace

from iphub.drivers import descoberta, presenca
from iphub.drivers.presenca import Sondas
from iphub.drivers.simulado import ServidorHttp

IP = "127.0.0.1"
MUDAS = Sondas(tcp=(), http=(), ssdp=None, mdns=None, netbios=None, dns=False, timeout_s=0.5)


class _Respondedor(asyncio.DatagramProtocol):
    def __init__(self, resposta: bytes) -> None:
        self.resposta = resposta
        self.transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transporte) -> None:
        self.transporte = transporte

    def datagram_received(self, dados: bytes, remetente) -> None:
        assert self.transporte is not None
        self.transporte.sendto(self.resposta, remetente)


@asynccontextmanager
async def _udp(resposta: bytes) -> AsyncIterator[int]:
    laco = asyncio.get_running_loop()
    transporte, _ = await laco.create_datagram_endpoint(
        lambda: _Respondedor(resposta), local_addr=(IP, 0)
    )
    try:
        yield transporte.get_extra_info("sockname")[1]
    finally:
        transporte.close()


@asynccontextmanager
async def _tcp() -> AsyncIterator[int]:
    async def atender(_leitor, escritor) -> None:
        escritor.close()

    servidor = await asyncio.start_server(atender, IP, 0)
    try:
        yield servidor.sockets[0].getsockname()[1]
    finally:
        servidor.close()
        await servidor.wait_closed()


def _porta_fechada() -> int:
    with socket.socket() as soquete:
        soquete.bind((IP, 0))
        return soquete.getsockname()[1]


def _resposta_dns(pergunta: str, alvo: str) -> bytes:
    nome = descoberta._nome_em_bytes(pergunta)
    dado = descoberta._nome_em_bytes(alvo)
    assert nome is not None and dado is not None
    cabecalho = b"\x00\x00\x84\x00\x00\x00\x00\x01\x00\x00\x00\x00"
    return (
        cabecalho + nome + b"\x00\x0c\x80\x01\x00\x00\x00\x78" + len(dado).to_bytes(2, "big") + dado
    )


def _resposta_netbios(*nomes: tuple[str, int, int]) -> bytes:
    cabecalho = b"\x00\x00\x84\x00\x00\x00\x00\x01\x00\x00\x00\x00"
    pergunta = b"\x20CKAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA\x00\x00\x21\x00\x01\x00\x00\x00\x00"
    entradas = b"".join(
        nome.ljust(15).encode("ascii") + bytes([sufixo]) + bandeiras.to_bytes(2, "big")
        for nome, sufixo, bandeiras in nomes
    )
    dados = bytes([len(nomes)]) + entradas + b"\x00" * 6
    return cabecalho + pergunta + len(dados).to_bytes(2, "big") + dados


async def test_uma_porta_aberta_e_presenca_e_a_descricao_a_nomeia():
    async with _tcp() as porta:
        vivo = await presenca.sondar(IP, replace(MUDAS, tcp=(porta, _porta_fechada())))
    assert vivo is not None
    assert vivo.portas == (porta,)
    assert vivo.descricao() == f"TCP {porta}"
    assert vivo.fontes == ("tcp",)


async def test_um_endereco_que_nada_responde_e_ninguem():
    assert await presenca.sondar(IP, replace(MUDAS, tcp=(_porta_fechada(),))) is None


async def test_o_anuncio_ssdp_da_nome_fabricante_e_modelo():
    xml = (
        '<root xmlns="urn:schemas-upnp-org:device-1-0"><device>'
        "<friendlyName> TV da Sala </friendlyName>"
        "<manufacturer>LG</manufacturer><modelName>OLED65</modelName></device></root>"
    )
    async with ServidorHttp({"/desc.xml": (200, xml)}) as servidor:
        local = f"http://{IP}:{servidor.endereco[1]}/desc.xml"
        anuncio = f"HTTP/1.1 200 OK\r\nSERVER: Linux UPnP/1.0 Teste/1\r\nLOCATION: {local}\r\n\r\n"
        async with _udp(anuncio.encode()) as porta:
            vivo = await presenca.sondar(IP, replace(MUDAS, ssdp=porta))
    assert vivo is not None
    assert (vivo.nome, vivo.fabricante, vivo.modelo) == ("TV da Sala", "LG", "OLED65")
    assert vivo.descricao() == "LG OLED65"
    assert vivo.servidor == "Linux UPnP/1.0 Teste/1"


async def test_a_descricao_de_outro_host_nunca_e_buscada():
    anuncio = b"HTTP/1.1 200 OK\r\nSERVER: Teste/1\r\nLOCATION: http://192.0.2.9/desc.xml\r\n\r\n"
    async with _udp(anuncio) as porta:
        vivo = await presenca.sondar(IP, replace(MUDAS, ssdp=porta))
    assert vivo is not None
    assert (vivo.nome, vivo.fabricante, vivo.modelo) == ("", "", "")
    assert vivo.descricao() == "Teste/1"


async def test_uma_descricao_com_dtd_e_ignorada():
    xml = (
        '<!DOCTYPE r [<!ENTITY a "x">]>'
        "<root><device><friendlyName>&a;</friendlyName></device></root>"
    )
    async with ServidorHttp({"/desc.xml": (200, xml)}) as servidor:
        local = f"http://{IP}:{servidor.endereco[1]}/desc.xml"
        async with _udp(f"HTTP/1.1 200 OK\r\nLOCATION: {local}\r\n\r\n".encode()) as porta:
            vivo = await presenca.sondar(IP, replace(MUDAS, ssdp=porta))
    assert vivo is not None
    assert vivo.nome == ""


async def test_o_nome_reverso_por_mdns_vem_do_proprio_host():
    resposta = _resposta_dns("1.0.0.127.in-addr.arpa", "tv-sala.local")
    async with _udp(resposta) as porta:
        vivo = await presenca.sondar(IP, replace(MUDAS, mdns=porta))
    assert vivo is not None
    assert vivo.nome == "tv-sala.local"
    assert vivo.fontes == ("mdns",)


async def test_o_nome_netbios_pula_grupos_e_pega_o_host():
    resposta = _resposta_netbios(("WORKGROUP", 0x00, 0x8400), ("ESCRITORIO", 0x00, 0x0400))
    async with _udp(resposta) as porta:
        vivo = await presenca.sondar(IP, replace(MUDAS, netbios=porta))
    assert vivo is not None
    assert vivo.nome == "ESCRITORIO"


async def test_o_titulo_da_pagina_nomeia_quando_nada_mais_nomeia():
    pagina = "<html><head><title> Roteador &amp;\n Casa </title></head></html>"
    async with ServidorHttp({"/": (200, pagina)}) as servidor:
        porta = servidor.endereco[1]
        vivo = await presenca.sondar(IP, replace(MUDAS, tcp=(porta,), http=(porta,)))
    assert vivo is not None
    assert vivo.nome == "Roteador & Casa"
    assert vivo.fontes == ("tcp", "http")


def test_uma_resposta_netbios_curta_ou_torta_nao_derruba_nada():
    assert presenca.nome_netbios(b"") == ""
    assert presenca.nome_netbios(b"\x00" * 20) == ""
    assert presenca.nome_netbios(_resposta_netbios(("BAD NAME!", 0x00, 0x0400))) == ""


def test_a_tabela_arp_da_os_resolvidos_e_pula_os_vazios(tmp_path):
    texto = (
        "IP address       HW type     Flags       HW address            Mask     Device\n"
        "192.0.2.7        0x1         0x2         d4:a6:51:0a:0b:0c     *        eth0\n"
        "192.0.2.8        0x1         0x0         00:00:00:00:00:00     *        eth0\n"
        "192.0.2.9        0x1         0x2         AA:BB:CC:DD:EE:FF     *        eth0\n"
        "torto\n"
    )
    assert presenca.ler_arp(texto) == {
        "192.0.2.7": "d4:a6:51:0a:0b:0c",
        "192.0.2.9": "aa:bb:cc:dd:ee:ff",
    }
    arquivo = tmp_path / "arp"
    arquivo.write_text(texto, encoding="ascii")
    assert presenca.tabela_arp(str(arquivo)) == presenca.ler_arp(texto)
    assert presenca.tabela_arp(str(tmp_path / "nao-existe")) == {}


async def test_as_portas_ja_sabidas_nao_sao_varridas_de_novo():
    """The sweep finds the live addresses first and hands the ports it found to the naming
    pass, which must take them instead of scanning the address a second time."""
    vivo = await presenca.sondar(IP, MUDAS, (1234, 5678))
    assert vivo is not None
    assert vivo.portas == (1234, 5678)
    assert vivo.descricao() == "TCP 1234, 5678"
    assert await presenca.sondar(IP, MUDAS) is None

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The hub answering for its own name on mDNS, and for nothing else."""

import socket

from iphub import anuncio
from iphub.drivers.descoberta import _nome, _registros, nome_em_bytes

NOMES = ("iphub.local", "iphub-ab12.local")
IP = "192.0.2.10"
TIPO_AAAA = 28


def _pergunta(nome: str, tipo: int, unicast: bool = False) -> bytes:
    classe = anuncio.CLASSE_IN | (anuncio.BIT_UNICAST if unicast else 0)
    cabecalho = b"\x00\x00\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
    return cabecalho + nome_em_bytes(nome) + tipo.to_bytes(2, "big") + classe.to_bytes(2, "big")


def _respostas(mensagem: bytes) -> dict[tuple[str, int], bytes]:
    registros = _registros(mensagem)
    assert registros is not None
    saida = {}
    for rotulos, tipo, inicio, tamanho in registros:
        nome = ".".join(r.decode() for r in rotulos)
        saida[(nome, tipo)] = mensagem[inicio : inicio + tamanho]
    return saida


def test_os_nomes_sao_o_da_frota_e_o_desta_caixa():
    assert anuncio.nomes_de("iphub", "ab12") == NOMES
    assert anuncio.nomes_de(" QA-Hub. ", "0f0f") == ("qa-hub.local", "qa-hub-0f0f.local")


def test_responde_o_endereco_para_um_nome_seu_e_cala_para_os_outros():
    resposta = anuncio.responder(
        _pergunta("IPHUB.local", anuncio.TIPO_A), NOMES, IP, 8080, anuncio.SERVICO_HTTP
    )
    assert resposta is not None
    mensagem, unicast = resposta
    assert unicast is False
    # Answered with the name as this hub spells it, whatever case the question came in.
    assert _respostas(mensagem)[("iphub.local", anuncio.TIPO_A)] == socket.inet_aton(IP)
    assert (
        anuncio.responder(
            _pergunta("outro.local", anuncio.TIPO_A), NOMES, IP, 8080, anuncio.SERVICO_HTTP
        )
        is None
    )
    # A type this hub does not have on a name it owns is answered with what the name HAS,
    # and never with the address of another type.
    outro_tipo = anuncio.responder(
        _pergunta("iphub.local", anuncio.TIPO_PTR), NOMES, IP, 8080, anuncio.SERVICO_HTTP
    )
    assert outro_tipo is not None
    assert ("iphub.local", anuncio.TIPO_A) not in _respostas(outro_tipo[0])


def test_uma_pergunta_que_pede_unicast_e_respondida_unicast():
    resposta = anuncio.responder(
        _pergunta("iphub-ab12.local", anuncio.TIPO_A, unicast=True),
        NOMES,
        IP,
        8080,
        anuncio.SERVICO_HTTP,
    )
    assert resposta is not None and resposta[1] is True


def test_o_servico_do_painel_leva_o_nome_e_a_porta():
    resposta = anuncio.responder(
        _pergunta(anuncio.SERVICO_HTTP, anuncio.TIPO_PTR), NOMES, IP, 8080, anuncio.SERVICO_HTTP
    )
    assert resposta is not None
    lidas = _respostas(resposta[0])
    instancia = "iphub-ab12._http._tcp.local"
    assert lidas[(anuncio.SERVICO_HTTP, anuncio.TIPO_PTR)] == nome_em_bytes(instancia)
    srv = lidas[(instancia, anuncio.TIPO_SRV)]
    assert int.from_bytes(srv[4:6], "big") == 8080
    assert srv[6:] == nome_em_bytes("iphub-ab12.local")
    assert lidas[("iphub-ab12.local", anuncio.TIPO_A)] == socket.inet_aton(IP)
    dns_sd = anuncio.responder(
        _pergunta(anuncio.SERVICOS_DNS_SD, anuncio.TIPO_PTR), NOMES, IP, 8080, anuncio.SERVICO_HTTP
    )
    assert dns_sd is not None
    assert _respostas(dns_sd[0])[(anuncio.SERVICOS_DNS_SD, anuncio.TIPO_PTR)] == nome_em_bytes(
        anuncio.SERVICO_HTTP
    )


def test_uma_resposta_alheia_e_lixo_nao_sao_perguntas():
    resposta = anuncio.anuncio(NOMES, IP, 8080, anuncio.SERVICO_HTTP)
    assert anuncio.perguntas(resposta) is None
    assert anuncio.responder(resposta, NOMES, IP, 8080, anuncio.SERVICO_HTTP) is None
    assert anuncio.responder(b"\x00" * 5, NOMES, IP, 8080, anuncio.SERVICO_HTTP) is None
    assert (
        anuncio.responder(b"\x00" * 12 + b"\xc0\x00", NOMES, IP, 8080, anuncio.SERVICO_HTTP) is None
    )


def test_o_anuncio_traz_todos_os_registros():
    lidas = _respostas(anuncio.anuncio(NOMES, IP, 8080, anuncio.SERVICO_HTTP))
    assert {("iphub.local", 1), ("iphub-ab12.local", 1), (anuncio.SERVICO_HTTP, 12)} <= set(lidas)


def _mapa(dados: bytes) -> set[int]:
    """The types an NSEC says the name has, read back out of its bit map."""
    lido = _nome(dados, 0)
    assert lido is not None
    bits = dados[lido[1] + 2 :]
    return {
        janela * 8 + posicao
        for janela, octeto in enumerate(bits)
        for posicao in range(8)
        if octeto & (0x80 >> posicao)
    }


def test_uma_pergunta_de_ipv6_e_respondida_com_o_que_o_nome_nao_tem():
    """With no answer the resolver waits out its timeout, five seconds on macOS, on every
    lookup; the NSEC of RFC 6762 says the address is IPv4 only and the wait ends."""
    resposta = anuncio.responder(
        _pergunta("iphub.local", TIPO_AAAA), NOMES, IP, 8080, anuncio.SERVICO_HTTP
    )
    assert resposta is not None
    lidas = _respostas(resposta[0])
    assert ("iphub.local", anuncio.TIPO_A) not in lidas
    assert _mapa(lidas[("iphub.local", anuncio.TIPO_NSEC)]) == {anuncio.TIPO_A}


def test_a_resposta_do_endereco_ja_diz_que_nao_ha_ipv6():
    """Beside the address, so the resolver never asks the second question at all."""
    resposta = anuncio.responder(
        _pergunta("iphub.local", anuncio.TIPO_A), NOMES, IP, 8080, anuncio.SERVICO_HTTP
    )
    assert resposta is not None
    lidas = _respostas(resposta[0])
    assert lidas[("iphub.local", anuncio.TIPO_A)] == socket.inet_aton(IP)
    assert _mapa(lidas[("iphub.local", anuncio.TIPO_NSEC)]) == {anuncio.TIPO_A}


def test_um_nome_alheio_continua_no_silencio_mesmo_em_ipv6():
    for tipo in (anuncio.TIPO_A, TIPO_AAAA, anuncio.TIPO_QUALQUER):
        assert (
            anuncio.responder(_pergunta("outro.local", tipo), NOMES, IP, 8080, anuncio.SERVICO_HTTP)
            is None
        )


def test_o_anuncio_tambem_leva_o_nsec_de_cada_nome():
    lidas = _respostas(anuncio.anuncio(NOMES, IP, 8080, anuncio.SERVICO_HTTP))
    for nome in NOMES:
        assert _mapa(lidas[(nome, anuncio.TIPO_NSEC)]) == {anuncio.TIPO_A}

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
import pytest

from iphub.portao import CABECALHOS, SERVIDOR, cabecalhos_completos, host_permitido

NENHUM = frozenset()


@pytest.mark.parametrize(
    "host",
    [
        "192.0.2.10",
        "192.0.2.10:8080",
        "127.0.0.1",
        "[::1]",
        "[::1]:8080",
        "[2001:db8::1]:8080",
        "localhost",
        "LOCALHOST",
        "localhost:8080",
        "Localhost:80",
        "localhost:65535",
        "localhost:1",
        " localhost ",
    ],
)
def test_ip_literal_e_localhost_passam(host):
    assert host_permitido(host, NENHUM)


@pytest.mark.parametrize(
    "host",
    [
        None,
        "",
        "   ",
        "hub.local",
        "hub.local:8080",
        "evil.example.com",
        "evil.example.com:8080",
        "192.0.2.10.example.com",
        "192.0.2.10:abc",
        "192.0.2.10:",
        "192.0.2.10:8080:1",
        "192.000.2.10",
        "::1",
        "[::1",
        "[::1]x",
        "[nao-e-ip]",
        "[192.0.2.10]",
        "localhost.example.com",
        "localhostx",
        "http://192.0.2.10",
        "192.0.2.10/painel",
        "localhost:0",
        "localhost:65536",
        "192.0.2.10:99999",
        "[::1]:99999",
        "\u00a0localhost",
        "localhost\u3000",
        "localhost.",
    ],
)
def test_nome_sem_lista_e_lixo_sao_recusados(host):
    assert not host_permitido(host, NENHUM)


@pytest.mark.parametrize("host", ["hub.local", "hub.local:8080", "HUB.LOCAL", "Hub.Local:80"])
def test_nome_da_lista_passa_com_porta_e_sem_caixa(host):
    assert host_permitido(host, frozenset({"hub.local"}))


def test_nome_da_lista_nao_abre_outros_nomes():
    lista = frozenset({"hub.local"})
    assert not host_permitido("hub.local.example.com", lista)
    assert not host_permitido("outro.local", lista)
    assert not host_permitido("evil.example.com", lista)


def test_cabecalhos_sao_os_quatro_da_secao_9():
    assert dict(CABECALHOS) == {
        "X-Frame-Options": "DENY",
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "no-referrer",
        "Content-Security-Policy": "frame-ancestors 'none'",
    }


def test_cabecalhos_completos_incluem_o_server_neutro():
    completos = cabecalhos_completos()
    assert completos["Server"] == SERVIDOR == "tuya-ip-hub"
    assert {k: v for k, v in completos.items() if k != "Server"} == dict(CABECALHOS)

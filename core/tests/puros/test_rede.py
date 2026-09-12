# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The networks of the host that the panel may offer as the range of the sweep."""

from iphub import rede


def test_as_redes_locais_sao_os_24_privados_sem_o_runtime_e_com_172_por_ultimo():
    enderecos = (
        "127.0.0.1",
        "192.168.65.3",
        "172.17.0.1",
        "172.20.0.5",
        "192.168.1.20",
        "192.168.1.21",
        "10.0.0.5",
        "8.8.8.8",
        "169.254.3.4",
        "fe80::1",
        "torto",
    )
    assert rede.redes_locais(enderecos, em_container=False) == (
        "192.168.1.0/24",
        "10.0.0.0/24",
        "172.20.0.0/24",
    )
    assert rede.redes_locais(enderecos, em_container=True) == ("192.168.1.0/24", "10.0.0.0/24")
    assert rede.redes_locais(()) == ()


def test_os_enderecos_do_host_sao_textos_de_ipv4():
    for endereco in rede.enderecos_do_host():
        assert endereco.count(".") == 3

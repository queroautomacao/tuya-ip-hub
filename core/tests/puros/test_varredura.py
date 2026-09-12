# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Under attack: the range the operator names is the only place this sweep knocks."""

import ipaddress

import pytest

from iphub.drivers import varredura
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Estado, Manifesto


def _manifesto(tipo: str) -> Manifesto:
    return Manifesto(tipo=tipo, rotulo={"pt": tipo, "en": tipo}, categoria="receiver")


class Mudo(Driver):
    """A driver that cannot ask, which is the one of the base and never sweeps."""

    MANIFESTO = _manifesto("mudo")

    def estado(self) -> Estado:
        return Estado(online=False)


class Falante(Driver):
    MANIFESTO = _manifesto("falante")
    RESPONDEM: dict[str, str] = {}
    PERGUNTADOS: list[str] = []

    @classmethod
    async def identificar(cls, ip: str) -> str | None:
        cls.PERGUNTADOS.append(ip)
        return cls.RESPONDEM.get(ip)

    def estado(self) -> Estado:
        return Estado(online=False)


class Outro(Falante):
    MANIFESTO = _manifesto("outro")
    RESPONDEM: dict[str, str] = {}
    PERGUNTADOS: list[str] = []


@pytest.fixture(autouse=True)
def _limpar():
    for classe in (Falante, Outro):
        classe.RESPONDEM = {}
        classe.PERGUNTADOS = []
    yield


@pytest.mark.parametrize(
    ("bruto", "codigo"),
    [
        ("", varredura.FAIXA_INVALIDA),
        ("   ", varredura.FAIXA_INVALIDA),
        (None, varredura.FAIXA_INVALIDA),
        (42, varredura.FAIXA_INVALIDA),
        ("nao-e-rede", varredura.FAIXA_INVALIDA),
        ("2001:db8::/64", varredura.FAIXA_INVALIDA),
        # A public range is somebody pointing this hub at the internet, and the answer is
        # no before the first socket instead of a log line after the last.
        ("8.8.8.0/24", varredura.FAIXA_PUBLICA),
        ("1.1.1.1/32", varredura.FAIXA_PUBLICA),
        ("10.0.0.0/8", varredura.FAIXA_GRANDE),
        ("192.168.0.0/16", varredura.FAIXA_GRANDE),
    ],
)
def test_a_faixa_recusada_diz_qual_regra_quebrou(bruto: object, codigo: str):
    with pytest.raises(varredura.FaixaRecusada) as recusa:
        varredura.ler_faixa(bruto)
    assert recusa.value.codigo == codigo


def test_a_faixa_privada_dentro_do_teto_passa():
    assert varredura.ler_faixa("192.168.15.0/24") == ipaddress.ip_network("192.168.15.0/24")
    assert varredura.ler_faixa(" 10.0.0.0/22 ") == ipaddress.ip_network("10.0.0.0/22")
    # A host bit set is the operator writing the address he knows, not the network.
    assert varredura.ler_faixa("192.168.15.16/24") == ipaddress.ip_network("192.168.15.0/24")
    assert varredura.ler_faixa("169.254.1.0/24").is_link_local


def test_os_enderecos_de_uma_faixa_pulam_rede_e_broadcast():
    rede = varredura.ler_faixa("192.0.2.0/29")
    assert varredura.enderecos(rede) == tuple(f"192.0.2.{n}" for n in range(1, 7))
    # A single address is the address, because there is no network and no broadcast to skip.
    assert varredura.enderecos(varredura.ler_faixa("192.0.2.5/32")) == ("192.0.2.5",)


async def test_a_varredura_pergunta_cada_endereco_e_devolve_quem_se_nomeou():
    Falante.RESPONDEM = {"192.0.2.2": "uuid-2", "192.0.2.5": "uuid-5"}
    rede = varredura.ler_faixa("192.0.2.0/29")
    achados = await varredura.procurar(rede, {"falante": Falante, "mudo": Mudo})
    assert [(a.tipo, a.identidade, a.ip) for a in achados] == [
        ("falante", "uuid-2", "192.0.2.2"),
        ("falante", "uuid-5", "192.0.2.5"),
    ]
    assert Falante.PERGUNTADOS == [f"192.0.2.{n}" for n in range(1, 7)]


async def test_o_driver_da_base_nunca_e_perguntado():
    """A driver that answers None to everything is a thousand awaits that can only fail."""
    rede = varredura.ler_faixa("192.0.2.0/29")
    assert await varredura.procurar(rede, {"mudo": Mudo}) == ()


async def test_o_primeiro_que_nomeia_fica_com_o_endereco_e_a_ordem_e_do_catalogo():
    """Two drivers that both answer must not race for one address,."""
    Falante.RESPONDEM = {"192.0.2.1": "do-falante"}
    Outro.RESPONDEM = {"192.0.2.1": "do-outro"}
    rede = varredura.ler_faixa("192.0.2.1/32")
    (achado,) = await varredura.procurar(rede, {"zz_falante": Falante, "aa_outro": Outro})
    # Alphabetical by type, so the same segment answers the same thing on every hub.
    assert (achado.tipo, achado.identidade) == ("aa_outro", "do-outro")
    # Both were asked, because asking them one after the other is what made a /24 need
    # a hundred and forty seconds; the order decides the winner, not who was asked.
    assert Falante.PERGUNTADOS == ["192.0.2.1"]


async def test_um_driver_que_estoura_nao_derruba_a_varredura():
    class Explode(Falante):
        MANIFESTO = _manifesto("explode")

        @classmethod
        async def identificar(cls, ip: str) -> str | None:
            raise OSError("no route to host")

    Falante.RESPONDEM = {"192.0.2.2": "uuid-2"}
    rede = varredura.ler_faixa("192.0.2.0/29")
    achados = await varredura.procurar(rede, {"aa_explode": Explode, "zz_falante": Falante})
    assert [(a.tipo, a.ip) for a in achados] == [("zz_falante", "192.0.2.2")]


async def test_um_endereco_sem_driver_mas_vivo_entra_na_lista_sem_tipo():
    """The operator sees the whole segment: an address that answers a port is listed with an
    empty tipo, and one a driver names carries the name the network gave it.
    """
    from dataclasses import replace

    from iphub.drivers.presenca import Sondas
    from iphub.drivers.simulado import ServidorHttp

    mudas = Sondas(tcp=(), http=(), ssdp=None, mdns=None, netbios=None, dns=False, timeout_s=0.5)
    pagina = "<html><head><title>Receiver da sala</title></head></html>"
    async with ServidorHttp({"/": (200, pagina)}) as servidor:
        porta = servidor.endereco[1]
        sondas = replace(mudas, tcp=(porta,), http=(porta,))
        rede = ipaddress.ip_network("127.0.0.1/32")
        (sem_driver,) = await varredura.procurar(rede, {"mudo": Mudo}, sondas=sondas)
        Falante.RESPONDEM = {"127.0.0.1": "id-1"}
        (com_driver,) = await varredura.procurar(rede, {"falante": Falante}, sondas=sondas)
    assert (sem_driver.tipo, sem_driver.identidade, sem_driver.ip) == ("", "", "127.0.0.1")
    assert sem_driver.nome == "Receiver da sala"
    assert sem_driver.descricao == f"TCP {porta}"
    assert (com_driver.tipo, com_driver.nome) == ("falante", "Receiver da sala")
    assert await varredura.procurar(rede, {"mudo": Mudo}, sondas=None) == ()


async def test_um_endereco_sem_porta_aberta_nao_e_perguntado_a_driver_nenhum():
    """Asking an address who it is is the expensive half of a sweep, so it is asked only where
    a port answered: a range is mostly empty and every driver probe there is time the operator
    waits for nothing."""
    import socket

    from iphub.drivers.presenca import Sondas

    with socket.socket() as soquete:
        soquete.bind(("127.0.0.1", 0))
        fechada = soquete.getsockname()[1]
    sondas = Sondas(tcp=(fechada,), http=(), ssdp=None, mdns=None, netbios=None, dns=False)
    Falante.RESPONDEM = {"127.0.0.1": "id-1"}
    rede = ipaddress.ip_network("127.0.0.1/32")
    assert await varredura.procurar(rede, {"falante": Falante}, sondas=sondas) == ()
    assert Falante.PERGUNTADOS == []

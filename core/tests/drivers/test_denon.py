# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Denon and Marantz receiver over HTTP, against a simulated receiver.

Section 14: this driver never opens a telnet connection, because the receiver accepts one at
a time and fights with any other controller that wants it. Every test here proves the wire is
HTTP and nothing else.

O receiver Denon e Marantz por HTTP, contra um receiver simulado.

Seção 14: este driver nunca abre conexão telnet, porque o receiver aceita uma por vez e briga
com qualquer outro controlador que a queira. Todo teste aqui prova que o fio é HTTP e mais nada.
"""

from dataclasses import dataclass, field

import pytest

from iphub.drivers.base import CODIGOS
from iphub.drivers.manifesto import Auth, por_lista, validar
from iphub.drivers.nativos import denon
from iphub.drivers.nativos.denon import Denon
from iphub.drivers.simulado import ServidorHttp

ESTADO = "/goform/formMainZone_MainZoneXmlStatusLite.xml"
APARELHO = "/goform/Deviceinfo.xml"
COMANDO = "/goform/formiPhoneAppDirect.xml"

MAC = "0005CD123456"


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = MAC
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


def _estado(
    energia: str = "ON",
    fonte: str = "BD",
    volume: str = "-40.0",
    mudo: str = "off",
    modo: str = "STEREO",
) -> str:
    return (
        "<?xml version='1.0' encoding='utf-8'?><item>"
        f"<Power><value>{energia}</value></Power>"
        f"<InputFuncSelect><value>{fonte}</value></InputFuncSelect>"
        f"<MasterVolume><value>{volume}</value></MasterVolume>"
        f"<Mute><value>{mudo}</value></Mute>"
        f"<SurrMode><value>{modo}</value></SurrMode>"
        "</item>"
    )


def _rotas(**extras: str) -> dict[str, tuple[int, str]]:
    rotas = {
        ESTADO: (200, _estado()),
        APARELHO: (200, f"<item><MacAddress><value>{MAC}</value></MacAddress></item>"),
        COMANDO: (200, ""),
    }
    rotas.update({caminho: (200, texto) for caminho, texto in extras.items()})
    return rotas


@pytest.fixture
async def receiver(monkeypatch):
    """A simulated receiver plus a driver aimed at its port, closed when the test ends.

    Um receiver simulado mais um driver apontado para a porta dele, fechado no fim do teste.
    """
    criados: list[Denon] = []

    def montar(servidor: ServidorHttp | None = None, *, portas: tuple[int, ...] | None = None):
        if servidor is not None:
            monkeypatch.setattr(denon, "PORTAS", portas or (servidor.endereco[1],))
        driver = Denon(_Cadastro())
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


def _comandos(servidor: ServidorHttp) -> list[str]:
    return [
        pedido.caminho.split("?", 1)[1]
        for pedido in servidor.pedidos
        if pedido.caminho.startswith(f"{COMANDO}?")
    ]


def test_o_manifesto_e_o_de_um_receiver_e_nao_promete_transporte():
    """Section 6: a receiver switches, sets a level, picks an input and a sound mode; the
    transport of what plays belongs to the source and not to it.

    Seção 6: um receiver liga, ajusta nível, escolhe entrada e modo de som; o transporte do
    que toca é da fonte e não dele.
    """
    manifesto = Denon.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.categoria == "receiver"
    assert manifesto.auth is Auth.NENHUMA
    assert manifesto.nuvem is False
    assert manifesto.capacidades == (
        "ligar",
        "desligar",
        "volume",
        "mudo",
        "fonte",
        "modo",
        "atalho",
        "comando_extra",
    )
    assert "tocar" not in manifesto.capacidades
    assert manifesto.descoberta.ssdp_fabricantes == ("denon", "marantz")
    # The driver suggests the words of the chart nobody memorises, for both lists it reads.
    # O driver sugere as palavras da tabela que ninguém decora, para as duas listas que ele lê.
    sugeridas = por_lista(manifesto)
    assert set(sugeridas) == {"entradas", "modos"}
    assert [item.valor for item in sugeridas["modos"]][:2] == ["MOVIE", "MUSIC"]


async def test_um_poll_le_energia_entrada_volume_mudo_e_modo(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.fonte, estado.mudo, estado.modo) == (True, "BD", False, "STEREO")
    # Why: the status answers dB from -80.0 and the contract of section 6 is 0 to 100.
    # Por que: o estado responde dB a partir de -80,0 e o contrato da seção 6 é 0 a 100.
    assert estado.volume == 41


@pytest.mark.parametrize(
    ("db", "esperado"),
    [("-80.0", 0), ("-40.0", 41), ("0.0", 82), ("18.0", 100), ("--", None), ("", None)],
)
async def test_o_volume_do_receiver_vira_a_escala_da_secao_6(receiver, db, esperado):
    """A receiver that answers -- has no readable volume, which is not zero volume.

    Um receiver que responde -- não tem volume legível, que não é volume zero.
    """
    async with ServidorHttp(_rotas(**{ESTADO: _estado(volume=db)})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    assert driver.estado().volume == esperado


async def test_ligar_desligar_e_o_mudo_falam_a_tabela_da_denon(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("ligar") is None
        assert await driver.executar("desligar") is None
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
    assert _comandos(servidor) == ["PWON", "PWSTANDBY", "MUON", "MUOFF"]
    assert driver.estado().mudo is False


async def test_o_volume_vai_como_os_dois_digitos_da_tabela(receiver):
    """The chart takes 00 to 98, so the 0 to 100 of section 6 is converted before the wire.

    A tabela recebe 00 a 98, então o 0 a 100 da seção 6 é convertido antes do fio.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("volume", 0) is None
        assert await driver.executar("volume", 50) is None
        assert await driver.executar("volume", 100) is None
    assert _comandos(servidor) == ["MV00", "MV49", "MV98"]
    assert driver.estado().volume == 100


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_ao_fio(receiver, valor):
    """True is an int in Python: a mute arriving where a volume fits would silence a room.

    True é int em Python: um mudo chegando onde cabe volume emudeceria uma sala.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("volume", valor) == "invalid_value"
    assert _comandos(servidor) == []


async def test_a_entrada_e_o_modo_vao_atras_do_prefixo_da_tabela(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("fonte", "mplay") is None
        assert await driver.executar("modo", "movie") is None
    assert _comandos(servidor) == ["SIMPLAY", "MSMOVIE"]
    assert driver.estado().fonte == "MPLAY"
    assert driver.estado().modo == "MOVIE"


async def test_um_atalho_e_um_comando_extra_vao_inteiros(receiver):
    """A shortcut is any command of the chart, written whole; a space travels encoded.

    Um atalho é qualquer comando da tabela, escrito inteiro; um espaço viaja codificado.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("atalho", "PSBAS UP") is None
        assert await driver.executar("comando_extra", "NS9A") is None
    assert _comandos(servidor) == ["PSBAS%20UP", "NS9A"]


@pytest.mark.parametrize(
    "valor",
    ["SI?MV99", "SIBD&PWON", "", "x" * 40, 7, None, "SI\nPWON"],
)
async def test_um_valor_que_fecharia_a_query_nunca_chega_ao_fio(receiver, valor):
    """Section 9: the value lands in the query string of the receiver, so a separator in it
    would write a second command that nobody wrote in this file.

    Seção 9: o valor cai na query string do receiver, então um separador nele escreveria um
    segundo comando que ninguém escreveu neste arquivo.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar("fonte", valor) == "invalid_value"
        assert await driver.executar("atalho", valor) == "invalid_value"
    assert _comandos(servidor) == []


async def test_a_porta_certa_e_achada_e_guardada(receiver):
    """The AVR-X of 2016 answers on 8080 and the older ones on 80, and no field of the
    registration tells them apart; the one that answered is kept for the next exchange.

    O AVR-X de 2016 responde na 8080 e os mais antigos na 80, e campo nenhum do cadastro os
    distingue; a que respondeu fica guardada para a troca seguinte.
    """
    async with ServidorHttp(_rotas()) as servidor:
        porta = servidor.endereco[1]
        # A port nobody listens on comes first, exactly as 8080 does on an old receiver.
        # Uma porta em que ninguém escuta vem primeiro, como a 8080 num receiver antigo.
        driver = receiver(servidor, portas=(9, porta))
        await driver.atualizar()
        assert driver.estado().online is True
        antes = len(servidor.pedidos)
        assert await driver.executar("ligar") is None
    assert len(servidor.pedidos) == antes + 1, "the port that answered is not probed again"


async def test_um_receiver_que_nao_responde_e_offline_depois_de_dois_polls(receiver):
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.rotas.clear()
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(receiver, monkeypatch):
    """Section 6: the address is where the device answered today, and a registration without
    one is a registration nothing can be dialled from.

    Seção 6: o endereço é onde o aparelho respondeu hoje, e um cadastro sem ele é um cadastro
    de onde nada pode ser discado.
    """
    async with ServidorHttp(_rotas()) as servidor:
        monkeypatch.setattr(denon, "PORTAS", (servidor.endereco[1],))
        driver = Denon(_Cadastro(ip=""))
        assert await driver.executar("ligar") == "eq_offline"
        await driver.atualizar()
        await driver.parar()
    assert servidor.pedidos == []
    assert driver.estado().online is False


async def test_a_identidade_e_o_mac_que_o_receiver_responde(receiver, monkeypatch):
    """Section 6: the identity is a MAC or a serial and never the address, so the sweep turns
    a finding into a registration nobody has to type.

    Seção 6: a identidade é um MAC ou um serial e nunca o endereço, então a varredura vira um
    cadastro que ninguém precisa digitar.
    """
    async with ServidorHttp(_rotas()) as servidor:
        monkeypatch.setattr(denon, "PORTAS", (servidor.endereco[1],))
        assert await Denon.identificar("127.0.0.1") == MAC
        assert await Denon.identificar("receiver.local") is None
    async with ServidorHttp(
        {APARELHO: (200, "<item><MacAddress><value>x</value></MacAddress>")}
    ) as outro:
        monkeypatch.setattr(denon, "PORTAS", (outro.endereco[1],))
        assert await Denon.identificar("127.0.0.1") is None


@pytest.mark.parametrize("acao", ["tocar", "pausar", "tecla", "temperatura", "agrupar"])
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(receiver, acao):
    """Section 6: the driver never implements a method only to refuse, and never dials out.

    Seção 6: o driver nunca implementa método só para recusar, e nunca disca para fora.
    """
    async with ServidorHttp(_rotas()) as servidor:
        driver = receiver(servidor)
        assert await driver.executar(acao, 50) == "nao_suportado"
    assert servidor.pedidos == []


async def test_uma_resposta_gigante_ou_sem_os_campos_nao_derruba_o_poll(receiver):
    enorme = "<item>" + "<lixo>x</lixo>" * 20_000 + "</item>"
    async with ServidorHttp(_rotas(**{ESTADO: enorme})) as servidor:
        driver = receiver(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.volume, estado.mudo, estado.fonte) == (None, None, None, None)


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """Section 6: five stable codes and nothing else ever leaves a driver.

    Seção 6: cinco códigos estáveis e mais nada sai de um driver.
    """
    assert {denon.EQ_OFFLINE, denon.INVALID_VALUE, denon.ERRO_APARELHO} <= set(CODIGOS)

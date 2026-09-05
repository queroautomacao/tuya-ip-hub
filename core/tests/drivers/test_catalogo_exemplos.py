# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The exit gate of milestone 3: the three embedded examples against a simulated device.

Each one is loaded from the catalog exactly as it ships, and only the port is rewritten,
because a simulated device listens on the port the operating system handed it. What the
tests assert is the whole path of section 7: the file, the loader, the engine, the wire, and
the typed Estado of section 6 coming back.

O portão de saída do marco 3: os três exemplos embarcados contra um aparelho simulado.

Cada um é carregado do catálogo exatamente como embarca, e só a porta é reescrita, porque um
aparelho simulado escuta na porta que o sistema operacional entregou. O que os testes afirmam
é o caminho inteiro da seção 7: o arquivo, o carregador, o motor, o fio, e o Estado tipado da
seção 6 voltando.
"""

import asyncio
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path

import pytest

from iphub.config import Cadastro
from iphub.drivers import catalogo
from iphub.drivers.base import Driver
from iphub.drivers.declarativo.formato import Definicao, Http
from iphub.drivers.declarativo.motor import construir
from iphub.drivers.simulado import ServidorDatagrama, ServidorHttp, ServidorLinha

# Why: the three examples of milestone 3 prove the engine and never ship, so they live with
# the tests and not in the catalogue of the image.
# Por que: os três exemplos do marco 3 provam o motor e nunca embarcam, então vivem com os
# testes e não no catálogo da imagem.
EXEMPLOS = Path(__file__).resolve().parent / "exemplos"

TIPO_TCP = "matriz_hdmi_ascii"
TIPO_HTTP = "rele_http"
TIPO_UDP = "amplificador_udp"
TIPO_AR = "ar_condicionado_tcp"

CHAVE = "chave-do-integrador"

# The device speaks 0 to 79 and section 6 fixes the contract at 0 to 100: 40 of the amplifier
# is 51 of the hub, and 50 of the hub is 40 of the amplifier.
# O aparelho fala 0 a 79 e a seção 6 fixa o contrato em 0 a 100: 40 do amplificador é 51 do
# hub, e 50 do hub é 40 do amplificador.
VOLUME_DO_APARELHO = 40
VOLUME_DO_CONTRATO = 51


async def _ate(condicao: Callable[[], bool], prazo_s: float = 2.0) -> None:
    """Waits for the simulated device to have handled what the driver sent.

    Why: a command writes and closes, and the device reads in a task of its own, so asserting
    the instant the driver returned is a test that passes on a quiet machine and fails on a
    busy one.

    Espera o aparelho simulado ter tratado o que o driver mandou.

    Por que: um comando escreve e fecha, e o aparelho lê numa tarefa própria, então afirmar no
    instante em que o driver voltou é um teste que passa em máquina quieta e falha em máquina
    cheia.
    """
    laco = asyncio.get_running_loop()
    limite = laco.time() + prazo_s
    while not condicao():
        assert laco.time() < limite, "the simulated device never saw it"
        await asyncio.sleep(0.005)


def _definicao(tipo: str) -> Definicao:
    """The example loaded the way the image would load an embedded file.

    O exemplo carregado do jeito que a imagem carregaria um arquivo embarcado.
    """
    montado = catalogo.Catalogo(pasta_embarcada=EXEMPLOS)
    assert montado.recusados == ()
    declarativo = montado.declarativos[tipo]
    assert declarativo.origem == catalogo.ORIGEM_IMAGEM
    return declarativo.definicao


def _driver(tipo: str, endereco: tuple[str, int], **segredos: str) -> Driver:
    definicao = _definicao(tipo)
    transporte = definicao.transporte
    if isinstance(transporte, Http):
        transporte = replace(transporte, base=f"http://{{ip}}:{endereco[1]}")
    else:
        transporte = replace(transporte, porta=endereco[1])
    classe = construir(replace(definicao, transporte=transporte))
    return classe(
        Cadastro(identidade=f"id-{tipo}", tipo=tipo, ip=endereco[0], segredos=dict(segredos))
    )


@pytest.fixture
def matriz():
    return ServidorLinha(
        {
            b"GET POWER": b"POWER ON\r\n",
            b"GET OUT1 VS": b"OUT1 VS IN2\r\n",
        },
        terminador=b"\r\n",
    )


async def test_a_matriz_tcp_le_o_estado_e_troca_a_entrada(matriz: ServidorLinha):
    async with matriz:
        driver = _driver(TIPO_TCP, matriz.endereco)
        await driver.iniciar()
        try:
            await driver.atualizar()
            estado = driver.estado()
            assert estado.online is True
            assert estado.ligado is True
            # The wire says IN2 and the panel offered "HDMI 2": what comes back is the label.
            # O fio diz IN2 e o painel ofereceu "HDMI 2": o que volta é o rótulo.
            assert estado.fonte == "HDMI 2"
            assert estado.fontes == ("HDMI 1", "HDMI 2", "HDMI 3", "HDMI 4")
            assert await driver.executar("fonte", "HDMI 3") is None
            await _ate(lambda: b"SET OUT1 VS IN3" in matriz.recebidas)
        finally:
            await driver.parar()


async def test_a_matriz_tcp_recusa_o_que_nao_esta_no_arquivo(matriz: ServidorLinha):
    """Section 6 and section 7: the map is the vocabulary, and what it does not carry is
    refused instead of being written raw onto the wire.

    Seções 6 e 7: o mapa é o vocabulário, e o que ele não leva é recusado em vez de ser
    escrito cru no fio.
    """
    async with matriz:
        driver = _driver(TIPO_TCP, matriz.endereco)
        await driver.iniciar()
        try:
            assert await driver.executar("fonte", "HDMI 9") == "invalid_value"
            assert await driver.executar("volume", 30) == "nao_suportado"
            assert not [linha for linha in matriz.recebidas if linha.startswith(b"SET")]
        finally:
            await driver.parar()


async def test_o_comando_extra_da_matriz_nunca_vira_dois_comandos(matriz: ServidorLinha):
    """The bench defect: a value carrying a carriage return became TWO commands on the wire.

    O defeito da bancada: um valor levando um retorno de carro virava DOIS comandos no fio.
    """
    async with matriz:
        driver = _driver(TIPO_TCP, matriz.endereco)
        await driver.iniciar()
        try:
            assert await driver.executar("comando_extra", "GET OUT2 VS\r\nSET POWER OFF") is None
            await _ate(lambda: matriz.recebidas == [b"GET OUT2 VSSET POWER OFF"])
        finally:
            await driver.parar()


async def test_a_matriz_tcp_offline_responde_o_codigo_estavel(matriz: ServidorLinha):
    async with matriz:
        endereco = matriz.endereco
    driver = _driver(TIPO_TCP, endereco)
    await driver.iniciar()
    try:
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"
        assert await driver.executar("ligar") == "eq_offline"
    finally:
        await driver.parar()


async def test_o_rele_http_le_o_json_e_manda_a_chave_do_cadastro():
    """Section 7: the VALUE of a header comes from the registration, never from the file.

    Seção 7: o VALOR de um cabeçalho vem do cadastro, nunca do arquivo.
    """
    servidor = ServidorHttp(
        {
            "/status.json": (200, '{"rele1": "on", "energia": 4.2}'),
            "/relay/1/off": (200, "ok"),
        }
    )
    async with servidor:
        driver = _driver(TIPO_HTTP, servidor.endereco, chave=CHAVE)
        await driver.iniciar()
        try:
            await driver.atualizar()
            assert driver.estado().online is True
            assert driver.estado().ligado is True
            assert await driver.executar("desligar") is None
            caminhos = [pedido.caminho for pedido in servidor.pedidos]
            assert caminhos == ["/status.json", "/relay/1/off"]
            assert all(pedido.cabecalhos["X-Api-Key"] == CHAVE for pedido in servidor.pedidos)
        finally:
            await driver.parar()


async def test_o_rele_http_que_responde_lixo_nao_inventa_estado():
    servidor = ServidorHttp({"/status.json": (200, "<html>relay board</html>")})
    async with servidor:
        driver = _driver(TIPO_HTTP, servidor.endereco, chave=CHAVE)
        await driver.iniciar()
        try:
            await driver.atualizar()
        finally:
            await driver.parar()
    estado = driver.estado()
    assert estado.online is True
    # Why: the device answered, so it is online; what it said carries no reading, and the
    # panel is told nothing instead of being told the relay is off.
    # Por que: o aparelho respondeu, então está online; o que ele disse não leva leitura, e o
    # painel não é informado de nada em vez de ser informado de que o relé está desligado.
    assert estado.ligado is None


async def test_o_amplificador_udp_converte_a_escala_nos_dois_sentidos():
    servidor = ServidorDatagrama(
        {
            b"PWR?\r": b"PWR ON\r",
            b"VOL?\r": f"VOL {VOLUME_DO_APARELHO}\r".encode("ascii"),
            b"SRC?\r": b"SRC BT\r",
        }
    )
    async with servidor:
        driver = _driver(TIPO_UDP, servidor.endereco)
        await driver.iniciar()
        try:
            await driver.atualizar()
            estado = driver.estado()
            assert estado.online is True
            assert estado.ligado is True
            assert estado.volume == VOLUME_DO_CONTRATO
            assert estado.fonte == "Bluetooth"
            assert await driver.executar("volume", 50) is None
            await _ate(lambda: b"VOL 40\r" in servidor.recebidos)
            assert await driver.executar("volume", 140) == "invalid_value"
            assert not [dado for dado in servidor.recebidos if dado.startswith(b"VOL 1")]
        finally:
            await driver.parar()


async def test_o_amplificador_udp_calado_reporta_offline():
    servidor = ServidorDatagrama({})
    async with servidor:
        driver = _driver(TIPO_UDP, servidor.endereco)
        await driver.iniciar()
        try:
            await driver.atualizar()
        finally:
            await driver.parar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


@pytest.fixture
def ar():
    return ServidorLinha(
        {b"GET STATUS": b"POWER ON TEMP 23 MODE COOL FAN LOW\r\n"},
        terminador=b"\r\n",
    )


async def test_o_ar_condicionado_tcp_le_o_setpoint_o_modo_e_o_vento_como_palavras(
    ar: ServidorLinha,
):
    """Section 7: a file reads temperatura, modo and vento the way it reads fonte, and what
    comes back is the word of section 6 the numbers module publishes, never the wire value.

    Seção 7: um arquivo lê temperatura, modo e vento do jeito que lê fonte, e o que volta é a
    palavra da seção 6 que o módulo dos números publica, nunca o valor de fio.
    """
    async with ar:
        driver = _driver(TIPO_AR, ar.endereco)
        await driver.iniciar()
        try:
            await driver.atualizar()
            estado = driver.estado()
            assert estado.online is True
            assert estado.ligado is True
            assert estado.temperatura == 23
            assert estado.modo == "frio"
            assert estado.vento == "baixo"
            assert await driver.executar("modo", "quente") is None
            await _ate(lambda: b"SET MODE HEAT" in ar.recebidas)
            assert await driver.executar("temperatura", 21) is None
            await _ate(lambda: b"SET TEMP 21" in ar.recebidas)
            assert await driver.executar("vento", "alto") is None
            await _ate(lambda: b"SET FAN HIGH" in ar.recebidas)
        finally:
            await driver.parar()


async def test_o_ar_condicionado_tcp_recusa_o_que_esta_fora_da_faixa_e_do_vocabulario(
    ar: ServidorLinha,
):
    """Section 6: the setpoint is whole degrees from 16 to 30 and a mode is a word of the
    file, so nothing else reaches the wire of a compressor.

    Seção 6: o setpoint são graus inteiros de 16 a 30 e um modo é uma palavra do arquivo,
    então nada além disso chega ao fio de um compressor.
    """
    async with ar:
        driver = _driver(TIPO_AR, ar.endereco)
        await driver.iniciar()
        try:
            assert await driver.executar("temperatura", 40) == "invalid_value"
            assert await driver.executar("temperatura", "21") == "invalid_value"
            assert await driver.executar("modo", "vento") == "invalid_value"
            assert await driver.executar("vento", "turbo") == "invalid_value"
            assert await driver.executar("volume", 10) == "nao_suportado"
            await asyncio.sleep(0.05)
            assert not [linha for linha in ar.recebidas if linha.startswith(b"SET")]
        finally:
            await driver.parar()


def test_o_manifesto_do_ar_condicionado_declara_as_palavras_que_manda():
    """Section 6: the words a driver sends are declared in the manifest, so the gestor and the
    panel know them before the first command.

    Seção 6: as palavras que um driver manda são declaradas no manifesto, então o gestor e o
    painel as conhecem antes do primeiro comando.
    """
    manifesto = construir(_definicao(TIPO_AR)).MANIFESTO
    assert manifesto.categoria == "ar_condicionado"
    assert manifesto.modos == ("auto", "frio", "quente", "seco")
    assert manifesto.ventos == ("auto", "baixo", "medio", "alto")
    assert manifesto.teclas == ()

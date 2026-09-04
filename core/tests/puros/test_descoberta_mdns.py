# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6: the mDNS query is generated from the manifests, like the SSDP sweep beside it.

Section 9 in spirit, and this is what the tests attack: an answer is data. A compression
pointer that walks in circles must not hang the daemon, an A record that names another host
must not move the address of a device, and one talkative device on the segment must not
decide how much this daemon reads.

Seção 6: a consulta mDNS nasce dos manifestos, como a varredura SSDP ao lado dela.

Seção 9 em espírito, e é isto que os testes atacam: uma resposta é dado. Um ponteiro de
compressão que anda em círculo não pode travar o daemon, um registro A que nomeia outro host
não pode mover o endereço de um aparelho, e um aparelho falante do segmento não pode decidir
quanto este daemon lê.
"""

import asyncio
import logging

import pytest

from iphub.drivers import descoberta, simulado
from iphub.drivers.descoberta import (
    ACHADOS_MAXIMOS,
    INSTANCIAS_MAXIMAS,
    REGISTROS_MAXIMOS,
    Plano,
    montar,
    procurar_mdns,
)
from iphub.drivers.manifesto import Descoberta, Manifesto
from iphub.drivers.simulado import RespondedorMdns, quadro_mdns

LOCAL = "127.0.0.1"
TIMEOUT_TESTE_S = 0.3
TIMEOUT_INUNDACAO_S = 2.0
SERVICO = "_linkplay._tcp"
NOME_DO_SERVICO = "_linkplay._tcp.local"
OUTRO_SERVICO = "_outro._tcp"
TIPO = "caixa_exemplo"
OUTRO_TIPO = "matriz_exemplo"
INSTANCIA = "Sala"
HOST = "caixa.local"
PORTA = 80
VITIMA = "192.0.2.10"
IMPOSTOR = "192.0.2.99"
LOGGER = "iphub.drivers.descoberta"
SERVICOS = {NOME_DO_SERVICO: TIPO}


def _manifesto(tipo: str, *servicos: str) -> Manifesto:
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": tipo, "en": tipo},
        categoria="multiroom",
        descoberta=Descoberta(mdns_servicos=servicos),
        textos={"pt": {"descricao": tipo}, "en": {"descricao": tipo}},
    )


def _plano_de_um_servico() -> Plano:
    return montar([_manifesto(TIPO, SERVICO)])


def _entrada(**mudancas) -> dict:
    entrada = {
        "servico": SERVICO,
        "instancia": INSTANCIA,
        "host": HOST,
        "ip": LOCAL,
        "porta": PORTA,
    }
    entrada.update(mudancas)
    return entrada


async def _consultar(plano: Plano, endereco: tuple[str, int], timeout_s: float = TIMEOUT_TESTE_S):
    return await procurar_mdns(plano, destino=endereco, timeout_s=timeout_s, bind=(LOCAL, 0))


def _avisos(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


class _Rude(asyncio.DatagramProtocol):
    """A device on the segment that answers exactly the bytes the test handed it.

    Um aparelho no segmento que responde exatamente os bytes que o teste lhe deu.
    """

    def __init__(self, respostas: tuple[bytes, ...]) -> None:
        self.respostas = respostas
        self.transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transporte = transport

    def datagram_received(self, data: bytes, addr) -> None:
        for resposta in self.respostas:
            self.transporte.sendto(resposta, addr)


@pytest.fixture
async def rude():
    """Opens a device that answers raw bytes, so an answer can be as hostile as needed.

    Abre um aparelho que responde bytes crus, para uma resposta ser tão hostil quanto preciso.
    """
    transportes = []

    async def abrir(*respostas: bytes) -> tuple[str, int]:
        laco = asyncio.get_running_loop()
        transporte, _protocolo = await laco.create_datagram_endpoint(
            lambda: _Rude(respostas), local_addr=(LOCAL, 0)
        )
        transportes.append(transporte)
        return transporte.get_extra_info("sockname")[:2]

    yield abrir
    for transporte in transportes:
        transporte.close()


async def test_acha_o_aparelho_simulado_com_o_srv_e_o_a_da_secao_adicional():
    """A real speaker answers the PTR and hands the SRV and the A in the ADDITIONAL section,
    with the cache flush bit on their class; both have to be read or the finding has no
    address and no port.

    Uma caixa real responde o PTR e entrega o SRV e o A na seção ADICIONAL, com o bit de
    limpeza de cache na classe deles; os dois precisam ser lidos ou o achado fica sem
    endereço e sem porta.
    """
    plano = montar([_manifesto(TIPO, SERVICO)])
    async with RespondedorMdns((_entrada(),)) as aparelho:
        achados = await _consultar(plano, aparelho.endereco)
    assert len(achados) == 1
    assert achados[0].tipo == TIPO
    assert achados[0].ip == LOCAL
    assert achados[0].porta == PORTA
    assert achados[0].descricao == INSTANCIA


async def test_a_consulta_e_um_ptr_por_servico_declarado():
    plano = montar([_manifesto(TIPO, SERVICO), _manifesto(OUTRO_TIPO, OUTRO_SERVICO)])
    async with RespondedorMdns(()) as aparelho:
        await _consultar(plano, aparelho.endereco)
    perguntadas = [simulado._questao_mdns(pedido) for pedido in aparelho.pedidos]
    assert sorted(nome for nome, _tipo, _fim in perguntadas) == [
        "_linkplay._tcp.local",
        "_outro._tcp.local",
    ]
    assert {tipo for _nome, tipo, _fim in perguntadas} == {simulado.TIPO_PTR}


async def test_o_plano_sem_servico_mdns_nao_pergunta_nada():
    """The hub works with no driver that declares a service; the query is then a no op.

    O hub funciona sem driver que declare serviço; a consulta é então uma não ação.
    """
    plano = montar(
        [
            Manifesto(
                tipo=TIPO,
                rotulo={"pt": TIPO, "en": TIPO},
                categoria="multiroom",
                descoberta=Descoberta(ssdp_st=("urn:exemplo:1",)),
                textos={"pt": {"descricao": TIPO}, "en": {"descricao": TIPO}},
            )
        ]
    )
    async with RespondedorMdns((_entrada(),)) as aparelho:
        assert await _consultar(plano, aparelho.endereco) == ()
        assert aparelho.pedidos == []


async def test_um_aparelho_de_outro_servico_fica_calado():
    plano = montar([_manifesto(TIPO, SERVICO)])
    async with RespondedorMdns((_entrada(servico=OUTRO_SERVICO),)) as aparelho:
        assert await _consultar(plano, aparelho.endereco) == ()
        assert aparelho.pedidos


def test_uma_resposta_de_servico_que_ninguem_pediu_e_ignorada():
    """The answer of a device nobody asked about is noise, and noise is not a finding.

    A resposta de um aparelho que ninguém perguntou é ruído, e ruído não é achado.
    """
    quadro = quadro_mdns((_entrada(servico=OUTRO_SERVICO),))
    assert descoberta._ler_mdns(quadro, LOCAL, SERVICOS) == ()


def test_o_ip_vem_do_registro_a_da_resposta():
    quadro = quadro_mdns((_entrada(ip=VITIMA),))
    ((_nome, achado),) = descoberta._ler_mdns(quadro, VITIMA, SERVICOS)
    assert achado.ip == VITIMA
    assert achado.porta == PORTA


def test_o_registro_a_que_nomeia_outro_host_nao_move_o_endereco(caplog):
    """The attack of section 9: an answer that points at another host would have this hub
    talking to a machine nobody registered, so what it says about that host is dropped.

    O ataque da seção 9: uma resposta que aponta para outro host poria este hub falando com
    uma máquina que ninguém cadastrou, então o que ela diz daquele host é descartado.
    """
    quadro = quadro_mdns((_entrada(ip=IMPOSTOR),))
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        ((_nome, achado),) = descoberta._ler_mdns(quadro, VITIMA, SERVICOS)
    assert achado.ip == VITIMA
    assert achado.porta is None
    assert any(IMPOSTOR in aviso and VITIMA in aviso for aviso in _avisos(caplog))


def test_uma_resposta_sem_registro_a_fica_no_endereco_do_remetente():
    quadro = quadro_mdns((_entrada(registros=("ptr", "srv")),))
    ((_nome, achado),) = descoberta._ler_mdns(quadro, VITIMA, SERVICOS)
    assert (achado.ip, achado.porta) == (VITIMA, PORTA)


def test_a_identidade_de_uma_resposta_mdns_fica_vazia():
    """Section 6: an identity is a uuid, a mac or a serial, and an instance name is a name
    the customer typed; the driver reads the real identity when the equipment is registered.

    Seção 6: uma identidade é um uuid, um mac ou um serial, e um nome de instância é um nome
    que o cliente digitou; o driver lê a identidade real quando o equipamento é cadastrado.
    """
    quadro = quadro_mdns((_entrada(),))
    ((_nome, achado),) = descoberta._ler_mdns(quadro, LOCAL, SERVICOS)
    assert achado.identidade == ""


def _cabecalho(respostas: int) -> bytes:
    return b"\x00\x00\x84\x00\x00\x00" + respostas.to_bytes(2, "big") + b"\x00\x00\x00\x00"


PONTEIRO_PARA_SI = _cabecalho(1) + b"\xc0\x0c" + b"\x00\x0c\x00\x01\x00\x00\x00\x78\x00\x00"
PONTEIRO_PARA_FRENTE = _cabecalho(1) + b"\xc0\x20" + b"\x00\x0c\x00\x01\x00\x00\x00\x78\x00\x00"
PONTEIROS_QUE_SE_APONTAM = (
    _cabecalho(1) + b"\xc0\x10\x00\x00" + b"\xc0\x0c" + b"\x00\x0c\x00\x01\x00\x00\x00\x78\x00\x00"
)
ROTULO_E_VOLTA = _cabecalho(1) + b"\x01a\xc0\x0c" + b"\x00\x0c\x00\x01\x00\x00\x00\x78\x00\x00"
NOME_LONGO_DEMAIS = (
    _cabecalho(1)
    + b"".join(bytes([63]) + b"a" * 63 for _ in range(5))
    + b"\x00\x00\x0c\x00\x01\x00\x00\x00\x78\x00\x00"
)


@pytest.mark.parametrize(
    "quadro",
    [PONTEIRO_PARA_SI, PONTEIRO_PARA_FRENTE, PONTEIROS_QUE_SE_APONTAM, ROTULO_E_VOLTA],
)
def test_um_ponteiro_em_laco_e_recusado_em_vez_de_travar(quadro):
    """A compression pointer that does not go strictly backwards makes a naive reader walk
    forever, and a daemon walking forever is a daemon that is down.

    Um ponteiro de compressão que não anda estritamente para trás faz um leitor ingênuo andar
    para sempre, e um daemon andando para sempre é um daemon fora do ar.
    """
    assert descoberta._ler_mdns(quadro, LOCAL, SERVICOS) == ()


def test_um_ponteiro_legitimo_e_seguido():
    """The refusal above only means something because a real answer does compress its names:
    the SRV of a speaker points back at the instance the PTR wrote.

    A recusa acima só quer dizer algo porque uma resposta real comprime os nomes dela: o SRV
    de uma caixa aponta de volta para a instância que o PTR escreveu.
    """
    quadro = quadro_mdns((_entrada(),))
    assert bytes([simulado.PONTEIRO_DNS >> 8]) in quadro
    ((_nome, achado),) = descoberta._ler_mdns(quadro, LOCAL, SERVICOS)
    assert (achado.ip, achado.porta) == (LOCAL, PORTA)


def test_uma_mensagem_nao_entrega_mais_registros_que_o_teto():
    """A message that declares a hundred records is read up to the ceiling and no further.

    Uma mensagem que declara cem registros é lida até o teto e não além.
    """
    respostas = tuple(
        _entrada(instancia=f"caixa{n}", registros=("ptr",)) for n in range(REGISTROS_MAXIMOS + 36)
    )
    assert len(descoberta._registros(quadro_mdns(respostas))) == REGISTROS_MAXIMOS


async def test_a_consulta_sobrevive_a_um_ponteiro_em_laco(rude):
    plano = montar([_manifesto(TIPO, SERVICO)])
    endereco = await rude(PONTEIRO_PARA_SI, PONTEIROS_QUE_SE_APONTAM)
    assert await _consultar(plano, endereco) == ()


def _cortado() -> bytes:
    return quadro_mdns((_entrada(),))[:-5]


def _mentindo_a_contagem() -> bytes:
    quadro = bytearray(quadro_mdns((_entrada(),)))
    quadro[6:8] = (9).to_bytes(2, "big")
    return bytes(quadro)


@pytest.mark.parametrize(
    "lixo",
    [
        b"",
        b"\x00\x03",
        b"\x00\xff nem de longe uma resposta",
        _cortado(),
        _mentindo_a_contagem(),
        NOME_LONGO_DEMAIS,
        descoberta._pergunta_mdns(NOME_DO_SERVICO),
    ],
)
def test_datagrama_truncado_ou_lixo_e_ignorado(lixo):
    """A neighbour spraying the group, and the query itself coming back, are not answers.

    Um vizinho borrifando o grupo, e a própria consulta voltando, não são respostas.
    """
    assert descoberta._ler_mdns(lixo, LOCAL, SERVICOS) == ()


async def test_duas_respostas_da_mesma_instancia_dobram():
    """A device answers the PTR in one datagram and the SRV with the A in the next, which is
    one speaker and has to read as one.

    Um aparelho responde o PTR num datagrama e o SRV com o A no seguinte, que é uma caixa só
    e precisa ser lido como uma.
    """
    respostas = (_entrada(registros=("ptr",)), _entrada(registros=("srv", "a")))
    async with RespondedorMdns(respostas) as aparelho:
        achados = await _consultar(_plano_de_um_servico(), aparelho.endereco)
    assert len(achados) == 1
    assert (achados[0].tipo, achados[0].porta) == (TIPO, PORTA)


def test_a_mesma_instancia_de_dois_remetentes_nao_dobra():
    """The attack of section 9 on the fold: answering with the instance name of the speaker
    in the next room must not take over its entry.

    O ataque da seção 9 sobre a dobra: responder com o nome de instância da caixa da sala ao
    lado não pode tomar a entrada dela.
    """
    coleta = descoberta._ColetaMdns(SERVICOS)
    coleta.absorver(quadro_mdns((_entrada(ip=VITIMA),)), VITIMA)
    coleta.absorver(quadro_mdns((_entrada(ip=IMPOSTOR),)), IMPOSTOR)
    assert [achado.ip for achado in coleta.resultado()] == [VITIMA, IMPOSTOR]


def test_a_porta_zero_do_srv_nao_vira_porta():
    quadro = quadro_mdns((_entrada(porta=0),))
    ((_nome, achado),) = descoberta._ler_mdns(quadro, LOCAL, SERVICOS)
    assert achado.porta is None


def test_a_descricao_e_o_rotulo_da_instancia_sem_caractere_de_controle():
    quadro = quadro_mdns((_entrada(instancia="\x01\x02Sala"),))
    ((_nome, achado),) = descoberta._ler_mdns(quadro, LOCAL, SERVICOS)
    assert achado.descricao == INSTANCIA


def test_uma_mensagem_nao_declara_mais_instancias_que_o_teto():
    """One device answering with sixty instances in one datagram must not decide how much
    this daemon keeps.

    Um aparelho respondendo com sessenta instâncias num datagrama só não pode decidir quanto
    este daemon guarda.
    """
    respostas = tuple(
        _entrada(instancia=f"caixa{n}", registros=("ptr",)) for n in range(INSTANCIAS_MAXIMAS + 28)
    )
    quadro = quadro_mdns(respostas)
    assert len(descoberta._ler_mdns(quadro, LOCAL, SERVICOS)) == INSTANCIAS_MAXIMAS


async def test_o_teto_de_achados_corta_a_consulta(monkeypatch, caplog):
    monkeypatch.setattr(descoberta, "ACHADOS_MAXIMOS", 5)
    respostas = tuple(_entrada(instancia=f"caixa{n}") for n in range(60))
    async with RespondedorMdns(respostas) as aparelho:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            achados = await _consultar(
                _plano_de_um_servico(), aparelho.endereco, TIMEOUT_INUNDACAO_S
            )
    assert len(achados) == 5
    assert any("ACHADOS_MAXIMOS" in aviso for aviso in _avisos(caplog))


async def test_o_teto_de_datagramas_corta_a_consulta(monkeypatch, caplog):
    """A device repeating one instance folds into one entry and still cannot spin the loop.

    Um aparelho repetindo uma instância dobra em uma entrada e ainda assim não gira o laço.
    """
    monkeypatch.setattr(descoberta, "DATAGRAMAS_MAXIMOS", 5)
    async with RespondedorMdns(tuple(_entrada() for _ in range(60))) as aparelho:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            achados = await _consultar(
                _plano_de_um_servico(), aparelho.endereco, TIMEOUT_INUNDACAO_S
            )
    assert len(achados) == 1
    assert any("DATAGRAMAS_MAXIMOS" in aviso for aviso in _avisos(caplog))


async def test_a_consulta_normal_nao_e_cortada_nem_avisa(caplog):
    respostas = tuple(_entrada(instancia=f"caixa{n}") for n in range(3))
    async with RespondedorMdns(respostas) as aparelho:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            achados = await _consultar(_plano_de_um_servico(), aparelho.endereco)
    assert len(achados) == 3
    assert _avisos(caplog) == []
    assert ACHADOS_MAXIMOS > 3


@pytest.mark.parametrize("torto", ["_" + "a" * 70 + "._tcp", "."])
async def test_o_servico_torto_do_manifesto_nao_vira_pergunta(torto, caplog):
    """A manifest is data on disk, and data that does not fit the wire must not go out as a
    question no device can answer, nor take the query of the service beside it.

    Um manifesto é dado em disco, e dado que não cabe no fio não pode sair como pergunta que
    aparelho nenhum responde, nem levar junto a consulta do serviço ao lado.
    """
    plano = montar([_manifesto(TIPO, SERVICO, torto)])
    async with RespondedorMdns((_entrada(),)) as aparelho:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            achados = await _consultar(plano, aparelho.endereco)
    assert len(aparelho.pedidos) == 1
    assert [achado.tipo for achado in achados] == [TIPO]
    assert any(torto in aviso for aviso in _avisos(caplog))


async def test_o_servico_do_manifesto_e_normalizado_antes_de_ir_ao_fio():
    """DNS compares names without case, so a manifest that writes the service with capitals
    and a trailing dot still finds the device.

    O DNS compara nomes sem caixa, então um manifesto que escreve o serviço com maiúsculas e
    ponto no fim ainda acha o aparelho.
    """
    plano = montar([_manifesto(TIPO, "_LinkPlay._TCP.local.")])
    async with RespondedorMdns((_entrada(),)) as aparelho:
        achados = await _consultar(plano, aparelho.endereco)
    assert [achado.tipo for achado in achados] == [TIPO]


async def test_dois_servicos_acham_cada_um_o_seu_tipo():
    plano = montar([_manifesto(TIPO, SERVICO), _manifesto(OUTRO_TIPO, OUTRO_SERVICO)])
    respostas = (
        _entrada(),
        _entrada(servico=OUTRO_SERVICO, instancia="Matriz", host="matriz.local", porta=23),
    )
    async with RespondedorMdns(respostas) as aparelho:
        achados = await _consultar(plano, aparelho.endereco)
    assert sorted((achado.tipo, achado.porta) for achado in achados) == [
        (TIPO, PORTA),
        (OUTRO_TIPO, 23),
    ]


async def test_a_consulta_termina_sem_ninguem_do_outro_lado(rude):
    endereco = await rude()
    assert await _consultar(_plano_de_um_servico(), endereco) == ()

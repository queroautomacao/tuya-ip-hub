# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6: the sweep plan is generated from the manifests, and two types that claim the
same signature are an error of this suite, never a decision taken in runtime.

Section 9 in spirit: an answer is data. The address of a device is where the datagram came
from, and the tests below attack the answer that tries to point somewhere else.

Seção 6: o plano de varredura nasce dos manifestos, e dois tipos que reivindicam a mesma
assinatura são erro desta suíte, nunca decisão tomada em runtime.

Seção 9 em espírito: uma resposta é dado. O endereço de um aparelho é de onde o datagrama
veio, e os testes abaixo atacam a resposta que tenta apontar para outro lugar.
"""

import asyncio
import logging

import pytest

from iphub.drivers import catalogo, descoberta
from iphub.drivers.descoberta import (
    ACHADOS_MAXIMOS,
    DATAGRAMAS_MAXIMOS,
    DESCRICAO_MAXIMA,
    Achado,
    Plano,
    PlanoAmbiguo,
    montar,
    procurar,
)
from iphub.drivers.manifesto import Descoberta, Manifesto
from iphub.drivers.simulado import RespondedorSsdp

LOCAL = "127.0.0.1"
TIMEOUT_TESTE_S = 0.3
TIMEOUT_INUNDACAO_S = 2.0
UUID = "5f9ec1b3-ff59-4e3a-9d1d-000000000001"
OUTRO_UUID = "5f9ec1b3-ff59-4e3a-9d1d-000000000002"
ST_EXEMPLO = "urn:schemas-exemplo:device:Projetor:1"
ST_RAIZ = "upnp:rootdevice"
LOGGER = "iphub.drivers.descoberta"
VITIMA = "192.0.2.10"
IMPOSTOR = "192.0.2.99"


def _manifesto(tipo: str, **assinaturas: tuple[str, ...]) -> Manifesto:
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": tipo, "en": tipo},
        categoria="outro",
        descoberta=Descoberta(**assinaturas),
        textos={"pt": {"descricao": tipo}, "en": {"descricao": tipo}},
    )


def _resposta(*cabecalhos: tuple[str, str], estado: str = "HTTP/1.1 200 OK") -> bytes:
    linhas = [estado, *(f"{nome}: {valor}" for nome, valor in cabecalhos)]
    return "\r\n".join([*linhas, "", ""]).encode("utf-8")


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


class _Inundador(asyncio.DatagramProtocol):
    """A device that answers in a loop, one datagram per turn, so none is lost in the queue.

    Um aparelho que responde em laço, um datagrama por volta, para nenhum se perder na fila.
    """

    def __init__(self, quantas: int, *, mesmo_uuid: bool) -> None:
        self.quantas = quantas
        self.mesmo_uuid = mesmo_uuid
        self.enviadas = 0
        self.transporte: asyncio.DatagramTransport | None = None

    def connection_made(self, transport: asyncio.BaseTransport) -> None:
        self.transporte = transport

    def datagram_received(self, data: bytes, addr) -> None:
        self._enviar(addr, 0)

    def _enviar(self, addr, indice: int) -> None:
        if self.transporte is None or indice >= self.quantas:
            return
        uuid = UUID if self.mesmo_uuid else f"{UUID}-{indice}"
        self.transporte.sendto(
            _resposta(("ST", ST_EXEMPLO), ("USN", f"uuid:{uuid}::{ST_EXEMPLO}")), addr
        )
        self.enviadas += 1
        asyncio.get_running_loop().call_soon(self._enviar, addr, indice + 1)


@pytest.fixture
async def inundador():
    """Opens the device of the attack of section 9: one that answers without stopping.

    Abre o aparelho do ataque da seção 9: um que responde sem parar.
    """
    transportes = []

    async def abrir(quantas: int, *, mesmo_uuid: bool = False) -> tuple[str, int]:
        laco = asyncio.get_running_loop()
        transporte, _protocolo = await laco.create_datagram_endpoint(
            lambda: _Inundador(quantas, mesmo_uuid=mesmo_uuid), local_addr=(LOCAL, 0)
        )
        transportes.append(transporte)
        return transporte.get_extra_info("sockname")[:2]

    yield abrir
    for transporte in transportes:
        transporte.close()


async def _varrer(plano: Plano, endereco: tuple[str, int], timeout_s: float = TIMEOUT_TESTE_S):
    return await procurar(plano, destino=endereco, timeout_s=timeout_s, bind=(LOCAL, 0))


def test_o_plano_nasce_dos_manifestos():
    plano = montar(
        [
            _manifesto("projetor_exemplo", ssdp_st=(ST_EXEMPLO,), ssdp_fabricantes=("Exemplo",)),
            _manifesto("caixa_exemplo", mdns_servicos=("_linkplay._tcp",)),
        ]
    )
    assert plano.sts == (ST_EXEMPLO,)
    assert plano.por_st == {ST_EXEMPLO: "projetor_exemplo"}
    assert plano.fabricantes == (("exemplo", "projetor_exemplo"),)
    assert plano.mdns == {"_linkplay._tcp": "caixa_exemplo"}


def test_o_plano_ordena_os_sts():
    plano = montar([_manifesto("um", ssdp_st=("b", "a")), _manifesto("dois", ssdp_st=("c",))])
    assert plano.sts == ("a", "b", "c")


def test_dois_tipos_no_mesmo_st_e_erro_desta_suite():
    manifestos = [
        _manifesto("um", ssdp_st=(ST_EXEMPLO,)),
        _manifesto("dois", ssdp_st=(ST_EXEMPLO,)),
    ]
    with pytest.raises(PlanoAmbiguo) as erro:
        montar(manifestos)
    assert ST_EXEMPLO in str(erro.value)
    assert "'um'" in str(erro.value) and "'dois'" in str(erro.value)


def test_dois_tipos_no_mesmo_fabricante_e_erro_desta_suite():
    # Why: the substring is compared in lower case, so a difference of case is the same claim.
    # Por que: o trecho é comparado em minúsculas, então diferença de caixa é a mesma posse.
    manifestos = [
        _manifesto("um", ssdp_fabricantes=("Exemplo",)),
        _manifesto("dois", ssdp_fabricantes=("exemplo",)),
    ]
    with pytest.raises(PlanoAmbiguo) as erro:
        montar(manifestos)
    assert "ssdp_fabricantes" in str(erro.value)


def test_dois_tipos_no_mesmo_servico_mdns_e_erro_desta_suite():
    manifestos = [
        _manifesto("um", mdns_servicos=("_linkplay._tcp",)),
        _manifesto("dois", mdns_servicos=("_linkplay._tcp",)),
    ]
    with pytest.raises(PlanoAmbiguo) as erro:
        montar(manifestos)
    assert "mdns_servicos" in str(erro.value)


def test_o_mesmo_tipo_pode_repetir_a_assinatura():
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO, ST_EXEMPLO))])
    assert plano.por_st == {ST_EXEMPLO: "um"}


def test_a_mensagem_lista_todos_os_conflitos():
    """One pass fixes every clash, the same promise the manifest validator makes.

    Uma passada conserta todo choque, a mesma promessa que o validador de manifesto faz.
    """
    manifestos = [
        _manifesto(
            "um", ssdp_st=(ST_EXEMPLO,), ssdp_fabricantes=("exemplo",), mdns_servicos=("_x._tcp",)
        ),
        _manifesto(
            "dois", ssdp_st=(ST_EXEMPLO,), ssdp_fabricantes=("exemplo",), mdns_servicos=("_x._tcp",)
        ),
    ]
    with pytest.raises(PlanoAmbiguo) as erro:
        montar(manifestos)
    mensagem = str(erro.value)
    assert mensagem.count("is claimed by") == 3


def test_fabricante_vazio_nao_reivindica_nada():
    """An empty substring is inside every answer, so it would take over the whole segment.

    Um trecho vazio está dentro de toda resposta, então ele tomaria o segmento inteiro.
    """
    plano = montar([_manifesto("um", ssdp_fabricantes=("", "  "))])
    assert plano.fabricantes == ()


def test_o_plano_sem_manifesto_e_vazio():
    assert montar([]) == Plano()


def _embarcados() -> list[Manifesto]:
    return [classe.MANIFESTO for classe in catalogo.carregar().values()]


def test_o_plano_do_catalogo_embarcado_leva_o_que_a_imagem_declara():
    """The rule of section 6 aimed at what actually ships, not only at hand built manifests.

    A regra da seção 6 apontada para o que de fato embarca, não só para manifestos de mão.
    """
    embarcados = _embarcados()
    assert embarcados, "the image ships no driver"
    plano = montar(embarcados)
    assert plano.por_st == {st: m.tipo for m in embarcados for st in m.descoberta.ssdp_st}
    assert plano.mdns == {
        s.strip(): m.tipo for m in embarcados for s in m.descoberta.mdns_servicos if s.strip()
    }
    assert dict(plano.fabricantes) == {
        f.strip().lower(): m.tipo
        for m in embarcados
        for f in m.descoberta.ssdp_fabricantes
        if f.strip()
    }


def test_um_driver_novo_que_repete_assinatura_do_catalogo_embarcado_e_ambiguo():
    """Section 6 pointed at the image plus one driver more, which is how a clash arrives.

    Seção 6 apontada para a imagem mais um driver, que é como um choque chega.
    """
    embarcados = _embarcados()
    sts_embarcados = tuple(st for m in embarcados for st in m.descoberta.ssdp_st)
    # Why: the image ships no SSDP signature today, so vizinho gives the shipped set one to
    # be taken; the day a driver declares its own, rival claims that one too and the test
    # keeps saying what it means. Por que: a imagem não embarca assinatura SSDP hoje, então o
    # vizinho dá uma ao conjunto embarcado; no dia em que um driver declarar a sua, o rival
    # reivindica essa também e o teste continua dizendo o que quer dizer.
    vizinho = _manifesto("vizinho_embarcado", ssdp_st=(ST_EXEMPLO,))
    rival = _manifesto("rival_do_embarcado", ssdp_st=(ST_EXEMPLO, *sts_embarcados))
    with pytest.raises(PlanoAmbiguo) as erro:
        montar([*embarcados, vizinho, rival])
    mensagem = str(erro.value)
    assert "'vizinho_embarcado'" in mensagem and "'rival_do_embarcado'" in mensagem
    for st in sts_embarcados:
        assert repr(st) in mensagem


async def test_acha_pelo_st():
    plano = montar([_manifesto("projetor_exemplo", ssdp_st=(ST_EXEMPLO,))])
    resposta = {
        "st": ST_EXEMPLO,
        "usn": f"uuid:{UUID}::{ST_EXEMPLO}",
        "server": "Linux/5.0 UPnP/1.0 Exemplo/2.0",
    }
    async with RespondedorSsdp((resposta,)) as aparelho:
        achados = await _varrer(plano, aparelho.endereco)
    assert len(achados) == 1
    assert achados[0].tipo == "projetor_exemplo"
    assert achados[0].identidade == UUID
    assert achados[0].ip == LOCAL
    assert achados[0].descricao == "Linux/5.0 UPnP/1.0 Exemplo/2.0"


async def test_acha_pelo_fabricante():
    """A manufacturer is not a target to ask for, it reads an answer that already arrived.

    Um fabricante não é alvo a pedir, ele lê uma resposta que já chegou.
    """
    plano = montar([_manifesto("caixa_exemplo", ssdp_fabricantes=("exemplo",))])
    resposta = {
        "st": ST_RAIZ,
        "usn": f"uuid:{UUID}::{ST_RAIZ}",
        "server": "Linux/5.0 UPnP/1.0 Exemplo Audio/3.1",
    }
    async with RespondedorSsdp((resposta,)) as aparelho:
        achados = await _varrer(plano, aparelho.endereco)
    assert [a.tipo for a in achados] == ["caixa_exemplo"]


async def test_st_que_ninguem_reivindica_fica_sem_tipo():
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,), ssdp_fabricantes=("exemplo",))])
    resposta = {"st": ST_RAIZ, "usn": f"uuid:{UUID}::{ST_RAIZ}", "server": "Outra Marca/1.0"}
    async with RespondedorSsdp((resposta,)) as aparelho:
        achados = await _varrer(plano, aparelho.endereco)
    assert [(a.tipo, a.identidade) for a in achados] == [("", UUID)]


async def test_o_ip_e_o_remetente_nao_o_que_o_location_aponta(rude):
    """The attack of section 9: an answer that names another host must not move the address.

    O ataque da seção 9: uma resposta que nomeia outro host não pode mover o endereço.
    """
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await rude(
        _resposta(
            ("ST", ST_EXEMPLO),
            ("USN", f"uuid:{UUID}::{ST_EXEMPLO}"),
            ("SERVER", "Exemplo/1.0"),
            ("LOCATION", "http://198.51.100.7:8080/descricao.xml"),
        )
    )
    achados = await _varrer(plano, endereco)
    assert len(achados) == 1
    assert achados[0].ip == LOCAL
    assert achados[0].porta is None


async def test_a_porta_vem_do_location_que_nomeia_o_remetente(rude):
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await rude(
        _resposta(
            ("ST", ST_EXEMPLO),
            ("USN", f"uuid:{UUID}::{ST_EXEMPLO}"),
            ("LOCATION", f"http://{LOCAL}:4352/descricao.xml"),
        )
    )
    achados = await _varrer(plano, endereco)
    assert achados[0].porta == 4352


async def test_duas_respostas_do_mesmo_uuid_dobram_em_um_achado():
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,), ssdp_fabricantes=("exemplo",))])
    respostas = (
        {"st": ST_EXEMPLO, "usn": f"uuid:{UUID}::{ST_EXEMPLO}", "server": "Exemplo/1.0"},
        {"st": ST_RAIZ, "usn": f"uuid:{UUID}::{ST_RAIZ}", "server": "Exemplo/1.0"},
    )
    async with RespondedorSsdp(respostas) as aparelho:
        achados = await _varrer(plano, aparelho.endereco)
    assert len(achados) == 1
    assert achados[0].tipo == "um"


async def test_respostas_sem_uuid_dobram_por_ip_e_tipo():
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    respostas = (
        {"st": ST_EXEMPLO, "usn": ST_EXEMPLO, "server": "Exemplo/1.0"},
        {"st": ST_EXEMPLO, "usn": ST_EXEMPLO, "server": "Exemplo/1.0"},
    )
    async with RespondedorSsdp(respostas) as aparelho:
        achados = await _varrer(plano, aparelho.endereco)
    assert len(achados) == 1
    assert achados[0].identidade == ""


@pytest.mark.parametrize(
    "lixo",
    [
        b"\x00\xff nem de longe uma resposta",
        b"HTTP/1.1 404 Not Found\r\nST: x\r\n\r\n",
        b"NOTIFY * HTTP/1.1\r\nST: x\r\n\r\n",
        b"",
    ],
)
async def test_datagrama_que_nao_e_resposta_ssdp_e_ignorado(rude, lixo):
    """A neighbour spraying the group must not appear in the list nor break the sweep.

    Um vizinho borrifando o grupo não pode aparecer na lista nem quebrar a varredura.
    """
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await rude(lixo)
    assert await _varrer(plano, endereco) == ()


async def test_a_descricao_e_podada_e_limitada(rude):
    """A SERVER header of five thousand characters is text for the panel, not a payload.

    Um cabeçalho SERVER de cinco mil caracteres é texto para o painel, não uma carga.
    """
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await rude(
        _resposta(
            ("ST", ST_EXEMPLO),
            ("USN", f"uuid:{UUID}::{ST_EXEMPLO}"),
            ("SERVER", "\x01\x02" + "A" * 5000),
        )
    )
    achados = await _varrer(plano, endereco)
    assert achados[0].descricao == "A" * DESCRICAO_MAXIMA


async def test_o_cabecalho_repetido_nao_sobrescreve_o_primeiro(rude):
    """A second USN in the same datagram must not rename the device that answered.

    Um segundo USN no mesmo datagrama não pode renomear o aparelho que respondeu.
    """
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await rude(
        _resposta(
            ("ST", ST_EXEMPLO),
            ("USN", f"uuid:{UUID}::{ST_EXEMPLO}"),
            ("USN", f"uuid:{OUTRO_UUID}::{ST_EXEMPLO}"),
        )
    )
    achados = await _varrer(plano, endereco)
    assert achados[0].identidade == UUID


async def test_o_plano_sem_assinatura_nao_envia_nada():
    """The hub works with no driver that declares a signature; a sweep is then a no op.

    O hub funciona sem driver que declare assinatura; uma varredura é então uma não ação.
    """
    plano = montar([_manifesto("um", mdns_servicos=("_linkplay._tcp",))])
    async with RespondedorSsdp(()) as aparelho:
        assert await _varrer(plano, aparelho.endereco) == ()
        assert aparelho.pedidos == []


async def test_o_st_do_manifesto_nao_injeta_cabecalho_no_datagrama():
    """A manifest is data on disk, and data must not write a header of its own on the wire.

    Um manifesto é dado em disco, e dado não pode escrever cabeçalho próprio no fio.
    """
    plano = montar([_manifesto("um", ssdp_st=("urn:exemplo:1\r\nX-Injetado: sim",))])
    async with RespondedorSsdp(()) as aparelho:
        await _varrer(plano, aparelho.endereco)
    assert aparelho.pedidos
    linhas = aparelho.pedidos[0].split(b"\r\n")
    assert not any(linha.startswith(b"X-Injetado") for linha in linhas)
    assert sum(1 for linha in linhas if linha.startswith(b"ST:")) == 1


async def test_a_varredura_termina_sem_ninguem_do_outro_lado(rude):
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await rude()
    assert await _varrer(plano, endereco) == ()


def _avisos(caplog) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.name == LOGGER]


async def test_a_varredura_para_no_teto_de_achados(inundador, monkeypatch, caplog):
    """Section 9: one device on the segment must not decide how big an answer gets.

    Seção 9: um aparelho do segmento não pode decidir o tamanho de uma resposta.
    """
    monkeypatch.setattr(descoberta, "ACHADOS_MAXIMOS", 5)
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await inundador(60)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        achados = await _varrer(plano, endereco, TIMEOUT_INUNDACAO_S)
    assert len(achados) == 5
    assert any("ACHADOS_MAXIMOS" in aviso for aviso in _avisos(caplog))


async def test_a_varredura_para_no_teto_de_datagramas(inundador, monkeypatch, caplog):
    """A flooder repeating one identity folds into one entry and still cannot spin the loop.

    Um inundador repetindo uma identidade dobra em uma entrada e ainda assim não gira o laço.
    """
    monkeypatch.setattr(descoberta, "DATAGRAMAS_MAXIMOS", 5)
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await inundador(60, mesmo_uuid=True)
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        achados = await _varrer(plano, endereco, TIMEOUT_INUNDACAO_S)
    assert len(achados) == 1
    assert any("DATAGRAMAS_MAXIMOS" in aviso for aviso in _avisos(caplog))


async def test_a_resposta_nunca_passa_do_teto_de_achados(inundador):
    """The ceiling that ships, not the one a test lowered, is what the LAN meets.

    O teto que embarca, não o que um teste baixou, é o que a LAN encontra.
    """
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    endereco = await inundador(ACHADOS_MAXIMOS + 60)
    achados = await _varrer(plano, endereco, TIMEOUT_INUNDACAO_S)
    assert len(achados) == ACHADOS_MAXIMOS
    assert DATAGRAMAS_MAXIMOS > ACHADOS_MAXIMOS


async def test_a_varredura_normal_nao_e_cortada_nem_avisa(caplog):
    """Three devices on the segment are three entries, and nothing is logged about them.

    Três aparelhos no segmento são três entradas, e nada é registrado sobre eles.
    """
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    respostas = tuple(
        {"st": ST_EXEMPLO, "usn": f"uuid:{UUID}-{n}::{ST_EXEMPLO}", "server": "Exemplo/1.0"}
        for n in range(3)
    )
    async with RespondedorSsdp(respostas) as aparelho:
        with caplog.at_level(logging.WARNING, logger=LOGGER):
            achados = await _varrer(plano, aparelho.endereco)
    assert len(achados) == 3
    assert _avisos(caplog) == []


@pytest.mark.parametrize("tipo_do_impostor", ["", "um"])
def test_um_uuid_de_dois_enderecos_nao_substitui_o_primeiro(caplog, tipo_do_impostor):
    """The attack of section 9: answering with the uuid of the projector in the next room.

    Loopback hands a test one source address, so the fold is attacked where it decides.

    O ataque da seção 9: responder com o uuid do projetor da sala ao lado.

    O loopback dá um endereço de origem ao teste, então a dobra é atacada onde ela decide.
    """
    vitima = Achado(tipo="", identidade=UUID, ip=VITIMA, porta=4352, descricao="Exemplo/1.0")
    impostor = Achado(
        tipo=tipo_do_impostor, identidade=UUID, ip=IMPOSTOR, porta=None, descricao="Impostor/1.0"
    )
    coleta = descoberta._Coleta()
    with caplog.at_level(logging.WARNING, logger=LOGGER):
        coleta.guardar(vitima)
        coleta.guardar(impostor)
    achados = coleta.resultado()
    assert [(a.ip, a.descricao) for a in achados] == [
        (VITIMA, "Exemplo/1.0"),
        (IMPOSTOR, "Impostor/1.0"),
    ]
    assert achados[0].porta == 4352
    assert achados[1].porta is None
    assert any(UUID in aviso and IMPOSTOR in aviso for aviso in _avisos(caplog))


async def test_a_linha_do_corpo_nao_vira_cabecalho(rude):
    """HTTP ends the headers at the empty line, so a body must not name the device.

    O HTTP encerra os cabeçalhos na linha vazia, então um corpo não pode nomear o aparelho.
    """
    plano = montar([_manifesto("um", ssdp_st=(ST_EXEMPLO,))])
    cabecalhos = _resposta(("ST", ST_EXEMPLO), ("USN", f"uuid:{UUID}::{ST_EXEMPLO}"))
    corpo = f"SERVER: injetado\r\nLOCATION: http://{LOCAL}:4352/x.xml\r\n".encode()
    endereco = await rude(cabecalhos + corpo)
    achados = await _varrer(plano, endereco)
    assert achados[0].identidade == UUID
    assert achados[0].descricao == ""
    assert achados[0].porta is None

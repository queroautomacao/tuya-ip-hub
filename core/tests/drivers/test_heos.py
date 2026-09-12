# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The HEOS multiroom driver against a simulated speaker, no hardware.

There is no request identifier on this wire, so most of what can go wrong here is a line read
as the answer of a command that did not ask for it: an event nobody asked for, an
intermediate answer, the late answer of the exchange before. Each of those has a test, and so
does every value that would write a second command onto the socket.
"""

import asyncio
import json
import logging
from dataclasses import dataclass, field

import pytest

from iphub.dpbus.numeros import MOVIMENTOS
from iphub.drivers import gestor
from iphub.drivers.base import CODIGOS
from iphub.drivers.manifesto import Auth, por_lista, validar
from iphub.drivers.nativos import heos
from iphub.drivers.nativos.heos import Heos
from iphub.drivers.simulado import ServidorLinha

PRAZO_DE_TESTE_S = 0.25
ORCAMENTO_DE_TESTE_S = 1.0
ATRASO_DE_TESTE_S = 0.2

# The deadlines of the driver as the image ships them, read before any fixture shortens them.
PRAZO_REAL_S = heos.TEMPO_LIMITE_S
ORCAMENTO_REAL_S = heos.ORCAMENTO_DO_POLL_S

MEU_IP = "127.0.0.1"
IP_DA_COZINHA = "127.0.0.2"
IP_DO_QUARTO = "127.0.0.3"
SERIAL = "B1A2C3K"
SERIAL_DA_COZINHA = "C2D3E4F"

ACORDAR = "system/register_for_change_events?enable=off"
JOGADORES = "player/get_players"
TRANSPORTE = "player/get_play_state?pid=1"
VOLUME = "player/get_volume?pid=1"
MUDO = "player/get_mute?pid=1"
TOCANDO = "player/get_now_playing_media?pid=1"
GRUPOS = "group/get_groups"


# The player list of the whole system, which is what this protocol answers to everybody: the
# speaker of this registration is the line whose address is ours, and never the first one.
def _jogadores(**trocas: object) -> list:
    minha = {
        "name": "Sala",
        "pid": 1,
        "model": "HEOS Drive",
        "version": "3.34.240",
        "ip": MEU_IP,
        "network": "wired",
        "lineout": 1,
        "serial": SERIAL,
    }
    minha.update(trocas)
    return [
        {
            "name": "Cozinha",
            "pid": 2,
            "model": "HEOS 3",
            "version": "3.34.240",
            "ip": IP_DA_COZINHA,
            "network": "wifi",
            "lineout": 1,
            "serial": SERIAL_DA_COZINHA,
        },
        minha,
        {
            "name": "Quarto",
            "pid": 3,
            "model": "HEOS 1",
            "version": "3.34.240",
            "ip": IP_DO_QUARTO,
            "network": "wifi",
            "lineout": 1,
        },
    ]


TOCANDO_AGORA = {
    "type": "station",
    "song": "Disney Radio",
    "station": "Disney Radio",
    "album": "Album",
    "artist": "Artist",
    "mid": "4256592506324148495",
    "qid": 1,
    "sid": 13,
}


@dataclass(frozen=True)
class _Item:
    rotulo: str
    valor: str


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = SERIAL
    ip: str = MEU_IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


def _pedido(linha: str) -> bytes:
    """The bytes of one command as the speaker reads them, terminator apart."""
    return f"heos://{linha}".encode("ascii")


def _quadro(comando: str, mensagem: str = "", payload: object = None, *, ok: bool = True) -> bytes:
    """One line of the speaker, in the envelope the protocol defines."""
    corpo: dict = {
        "heos": {
            "command": comando,
            "result": "success" if ok else "fail",
            "message": mensagem,
        }
    }
    if payload is not None:
        corpo["payload"] = payload
    return json.dumps(corpo).encode("utf-8") + b"\r\n"


def _resposta_de(linha: str, payload: object = None) -> bytes:
    """The plain answer of a command: the same command back, with its own attributes."""
    comando, _separador, atributos = linha.partition("?")
    return _quadro(comando, atributos, payload)


def _respostas(
    trocas: dict[str, bytes] | None = None, aceita: tuple[str, ...] = ()
) -> dict[bytes, bytes]:
    """A speaker that answers the whole poll, plus the commands a test lets it accept and the
    lines a test wants to change.
    """
    mapa = {
        _pedido(ACORDAR): _resposta_de(ACORDAR),
        _pedido(JOGADORES): _quadro("player/get_players", "", _jogadores()),
        _pedido(TRANSPORTE): _quadro("player/get_play_state", "pid=1&state=play"),
        _pedido(VOLUME): _quadro("player/get_volume", "pid=1&level=36.0"),
        _pedido(MUDO): _quadro("player/get_mute", "pid=1&state=off"),
        _pedido(TOCANDO): _quadro("player/get_now_playing_media", "pid=1", TOCANDO_AGORA),
    }
    for linha in aceita:
        mapa[_pedido(linha)] = _resposta_de(linha)
    for linha, resposta in (trocas or {}).items():
        mapa[_pedido(linha)] = resposta
    return mapa


@pytest.fixture(autouse=True)
def prazo_curto(monkeypatch):
    """A speaker that ignores a line is answered by the deadline, and no suite waits 5 s."""
    monkeypatch.setattr(heos, "TEMPO_LIMITE_S", PRAZO_DE_TESTE_S)
    monkeypatch.setattr(heos, "ORCAMENTO_DO_POLL_S", ORCAMENTO_DE_TESTE_S)


@pytest.fixture
async def caixa(monkeypatch):
    """A simulated speaker plus a driver aimed at its port, closed when the test ends."""
    criados: list[Heos] = []

    def montar(servidor: ServidorLinha, cadastro: _Cadastro | None = None) -> Heos:
        monkeypatch.setattr(heos, "PORTA_CLI", servidor.endereco[1])
        driver = Heos(cadastro or _Cadastro())
        criados.append(driver)
        return driver

    yield montar
    for driver in criados:
        await driver.parar()


def _falante(respostas: dict[bytes, bytes], atraso_s: float = 0.0) -> ServidorLinha:
    return ServidorLinha(respostas, terminador=b"\r\n", atraso_s=atraso_s)


def test_os_prazos_do_driver_cabem_no_prazo_do_gestor():
    """The gestor cuts a call into a driver at half the poll interval, so a deadline of this
    file that is not shorter than that one never fires: what would kill a slow exchange is
    the cancellation of the gestor, and this is the cheapest place that says so.
    """
    limite = gestor.INTERVALO_S * gestor.FRACAO_DO_LIMITE
    assert PRAZO_REAL_S < limite
    assert ORCAMENTO_REAL_S < limite


def test_o_manifesto_e_o_de_uma_caixa_sempre_ligada():
    """This interface has no power command, so the capability is omitted and
    never implemented to refuse; the port is fixed by the protocol and is not a field.
    """
    manifesto = Heos.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "multiroom_heos"
    assert manifesto.categoria == "multiroom"
    assert manifesto.auth is Auth.NENHUMA
    assert manifesto.nuvem is False
    assert manifesto.capacidades == (
        "volume",
        "mudo",
        "fonte",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
        "agrupar",
        "atalho",
        "tecla",
    )
    assert "ligar" not in manifesto.capacidades
    assert "desligar" not in manifesto.capacidades
    assert manifesto.teclas == ("mais", "menos")
    assert manifesto.config_campos == ()


def test_a_descoberta_nao_reivindica_o_fabricante_do_receiver():
    """The manufacturer name of the SSDP answer belongs to the driver of the receiver, and a
    receiver with HEOS built in answers both; two claims of one signature is a test error.
    """
    descoberta = Heos.MANIFESTO.descoberta
    assert descoberta.ssdp_st == ("urn:schemas-denon-com:device:ACT-Denon:1",)
    assert descoberta.mdns_servicos == ("_heos-audio._tcp",)
    assert descoberta.ssdp_fabricantes == ()


def test_o_driver_sugere_as_entradas_do_vocabulario_e_nunca_um_favorito():
    """A favourite is a position in the list of an account of the customer, which changes
    when the owner reorders it; the inputs are a published vocabulary nobody types from memory.
    """
    sugeridas = por_lista(Heos.MANIFESTO)
    assert set(sugeridas) == {"entradas"}
    assert [item.valor for item in sugeridas["entradas"]][:2] == [
        "inputs/aux_in_1",
        "inputs/aux_in_2",
    ]


def test_os_textos_do_painel_dizem_o_mesmo_nos_dois_idiomas():
    textos = Heos.MANIFESTO.textos
    assert set(textos["pt"]) == set(textos["en"])
    assert "descricao" in textos["pt"]
    # A receiver with HEOS built in is two registrations for one box, and the integrator
    # only knows that if the manifest says it where he reads.
    assert "receiver" in textos["pt"]["descricao"].lower()
    assert "receiver" in textos["en"]["descricao"].lower()


def test_os_codigos_do_driver_sao_os_estaveis_do_contrato():
    """Five stable codes and nothing else ever leaves a driver."""
    usados = {
        heos.EQ_OFFLINE,
        heos.INVALID_VALUE,
        heos.AUTH_PENDENTE,
        heos.ERRO_APARELHO,
        heos.NAO_SUPORTADO,
        *heos.CODIGO_POR_ERRO.values(),
    }
    assert usados <= set(CODIGOS)


def test_o_valor_e_escapado_so_nos_tres_bytes_do_protocolo():
    """This escaping is not the one of a URL: a space travels as it is, and the percent goes
    first or the escape of the ampersand would be escaped again.
    """
    assert heos._codificado("Rock & Roll = 100%") == "Rock %26 Roll %3D 100%25"


def test_o_endereco_de_um_fluxo_vai_por_ultimo_e_sem_escape():
    """The published protocol puts the address of a stream last and unescaped, so a URL
    encoder here would hand the speaker an address it cannot fetch.
    """
    linha = heos._linha("browse/play_stream", ((heos.ATRIBUTO_PID, 1),), url="http://a.b/c~d")
    assert linha == b"heos://browse/play_stream?pid=1&url=http://a.b/c~d\r\n"


def test_uma_linha_com_o_terminador_dentro_nunca_e_montada():
    """The terminator of this protocol lives inside the space of possible values,
    so a value carrying one would write a second command; this is the last guard before the
    socket and it holds even for a value every other check let through.
    """
    with pytest.raises(heos._Falha):
        heos._linha("player/set_volume", ((heos.ATRIBUTO_PID, "1\r\nheos://player/play_next"),))


async def test_o_poll_acorda_a_interface_e_le_o_que_a_caixa_respondeu(caixa):
    """The interface runs dormant until a socket connects, so the first line of a connection
    is the one that wakes it up, and only then does anything get asked.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        estado = driver.estado()
    assert servidor.recebidas == [
        _pedido(ACORDAR),
        _pedido(JOGADORES),
        _pedido(TRANSPORTE),
        _pedido(VOLUME),
        _pedido(MUDO),
        _pedido(TOCANDO),
    ]
    assert estado.online is True
    assert (estado.volume, estado.mudo) == (36, False)
    assert (estado.reproduzindo, estado.tocando) == (True, "Disney Radio")
    # There is no power command in this interface, so the speaker never says it is on.
    assert estado.ligado is None
    assert estado.detalhe == ""


async def test_um_cancelamento_de_fora_nao_deixa_o_socket_pendurado(caixa):
    """The gestor kills a poll that runs past its deadline with a cancellation, which is not
    a timeout and is caught by no except of an exchange. A socket left behind still holds the
    answer of the dead poll, and the next one would reuse the connection and read it as its
    own instead of ever reconnecting.
    """
    async with _falante(_respostas(), atraso_s=ATRASO_DE_TESTE_S) as servidor:
        driver = caixa(servidor)
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(ATRASO_DE_TESTE_S / 4):
                await driver.atualizar()
        servidor.atraso_s = 0.0
        await driver.atualizar()
        estado = driver.estado()
    # A session of its own, woken up again: the cancelled one was dropped and never reused.
    assert servidor.conexoes == 2
    assert servidor.recebidas.count(_pedido(ACORDAR)) == 2
    assert (estado.online, estado.volume) == (True, 36)


async def test_um_poll_sem_orcamento_nao_abre_socket(caixa, monkeypatch):
    """The budget of the whole poll is spent by the questions before this one, and a question
    with nothing left is dropped before a socket is used: a speaker that answers slowly holds
    this driver for the budget and never for five times the deadline of one exchange.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        antes = len(servidor.recebidas)
        monkeypatch.setattr(heos, "ORCAMENTO_DO_POLL_S", 0.0)
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    # Nothing was written and no session was opened: the budget was judged before the socket.
    assert servidor.recebidas[antes:] == []
    assert servidor.conexoes == 1
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_a_resposta_sobre_outra_caixa_nunca_e_lida_como_nossa(caixa):
    """The correlation of this wire is the command name AND the player id it was aimed at: a
    firmware update renumbers the id, so the late answer of the same question about another
    number would otherwise publish the volume of a speaker in another room as ours.
    """
    alheia = _quadro("player/get_volume", "pid=9&level=90.0")
    nossa = _quadro("player/get_volume", "pid=1&level=36.0")
    async with _falante(_respostas({VOLUME: alheia + nossa})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().volume == 36


@pytest.mark.parametrize(
    ("nivel", "esperado"),
    [("36.0", 36), ("0", 0), ("100", 100), ("42", 42), ("120", 100), ("", None), ("alto", None)],
)
async def test_o_nivel_com_ponto_decimal_vira_o_inteiro_da_secao_6(caixa, nivel, esperado):
    """This level comes back with a decimal point, and reading it as an integer raises on the
    answer of a healthy speaker; a word where a number belongs is not a volume of zero.
    """
    resposta = _quadro("player/get_volume", f"pid=1&level={nivel}")
    async with _falante(_respostas({VOLUME: resposta})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().volume == esperado


@pytest.mark.parametrize(
    ("fonte", "midia", "esperado"),
    [
        (1027, "inputs/aux_in_1", "inputs/aux_in_1"),
        (1027, "4256592506324148495", None),
        (13, "inputs/aux_in_1", None),
    ],
)
async def test_a_entrada_ativa_so_existe_dentro_da_fonte_auxiliar(caixa, fonte, midia, esperado):
    """There is no input field in what plays: the auxiliary source is one source id and the
    media id under it is literally the name of the input, which the registration list carries.
    """
    payload = {"type": "station", "song": "Linha", "sid": fonte, "mid": midia}
    resposta = _quadro("player/get_now_playing_media", "pid=1", payload)
    async with _falante(_respostas({TOCANDO: resposta})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().fonte == esperado


@pytest.mark.parametrize(
    ("acao", "valor", "linha"),
    [
        ("volume", 30, "player/set_volume?pid=1&level=30"),
        ("mudo", True, "player/set_mute?pid=1&state=on"),
        ("mudo", False, "player/set_mute?pid=1&state=off"),
        ("fonte", "inputs/aux_in_1", "browse/play_input?pid=1&input=inputs/aux_in_1"),
        ("tocar", None, "player/set_play_state?pid=1&state=play"),
        ("pausar", None, "player/set_play_state?pid=1&state=pause"),
        ("parar", None, "player/set_play_state?pid=1&state=stop"),
        ("proxima", None, "player/play_next?pid=1"),
        ("anterior", None, "player/play_previous?pid=1"),
        ("tecla", "mais", "player/volume_up?pid=1&step=5"),
        ("tecla", "menos", "player/volume_down?pid=1&step=5"),
        ("atalho", "2", "browse/play_preset?pid=1&preset=2"),
        (
            "atalho",
            "http://ice1.somafm.com/groovesalad-128-mp3",
            "browse/play_stream?pid=1&url=http://ice1.somafm.com/groovesalad-128-mp3",
        ),
        (
            "tocar",
            "http://ice1.somafm.com/groovesalad-128-mp3",
            "browse/play_stream?pid=1&url=http://ice1.somafm.com/groovesalad-128-mp3",
        ),
    ],
)
async def test_cada_comando_escreve_a_linha_do_protocolo(caixa, acao, valor, linha):
    """Pause and stop are different commands, because a pause on a stream keeps
    the speaker connected to the station and a stop lets go of it.
    """
    async with _falante(_respostas(aceita=(linha,))) as servidor:
        driver = caixa(servidor)
        assert await driver.executar(acao, valor) is None
    # The command is written after the wake up and after the player id is resolved.
    assert servidor.recebidas == [_pedido(ACORDAR), _pedido(JOGADORES), _pedido(linha)]


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_ao_fio(caixa, valor):
    """True is an int in Python: a mute arriving where a volume fits would silence a room."""
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("volume", valor) == "invalid_value"
    # A value that is refused is refused before a socket is even opened.
    assert servidor.recebidas == []


@pytest.mark.parametrize(
    ("acao", "valor"),
    [
        ("fonte", ""),
        ("fonte", "inputs/aux_in_1&pid=9"),
        ("fonte", "inputs/aux_in_1\r\nheos://player/play_next?pid=9"),
        ("fonte", "aux_in_1"),
        ("fonte", 7),
        ("fonte", None),
        ("atalho", "0"),
        ("atalho", "100"),
        ("atalho", "dois"),
        ("atalho", ""),
        ("atalho", 2),
        ("atalho", "http://estacao.exemplo/fluxo?formato=mp3"),
        ("atalho", "http://estacao.exemplo/a&pid=9"),
        ("tocar", "http://estacao.exemplo/fluxo?formato=mp3"),
        ("tocar", "http://estacao.exemplo/a\r\nheos://player/play_next?pid=9"),
        ("tocar", "estacao.exemplo/fluxo"),
        ("tocar", 7),
        ("tecla", "ok"),
        ("tecla", "play_pause"),
        ("tecla", None),
        ("mudo", "on"),
        ("mudo", 1),
    ],
)
async def test_um_valor_que_escreveria_um_segundo_comando_nunca_chega_ao_fio(caixa, acao, valor):
    """The value decides bytes on a socket whose terminator is inside the space of
    possible values, so it is judged before the connection is even opened.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar(acao, valor) == "invalid_value"
    assert servidor.recebidas == []


@pytest.mark.parametrize(
    "acao", ["ligar", "desligar", "modo", "vento", "temperatura", "comando_extra"]
)
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(caixa, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar(acao, 20) == "nao_suportado"
    assert servidor.recebidas == []


async def test_o_atalho_publica_o_rotulo_do_cadastro_enquanto_a_estacao_cala(caixa):
    """A raw stream answers no title until the station sends metadata, which many never do,
    and an empty "now playing" over a speaker that plays is the panel calling itself broken.
    """
    linha = "browse/play_preset?pid=1&preset=3"
    cadastro = _Cadastro(listas={"atalhos": (_Item("Radio Cidade", "3"),)})
    async with _falante(_respostas(aceita=(linha,))) as servidor:
        driver = caixa(servidor, cadastro)
        assert await driver.executar("atalho", "3") is None
    estado = driver.estado()
    assert (estado.tocando, estado.reproduzindo) == ("Radio Cidade", True)


async def test_uma_caixa_sem_controle_de_volume_pela_rede_recusa_o_volume(caixa):
    """A fixed line out driven by anything other than the network accepts the volume command
    and does nothing, and a bar on the panel that moves nothing is worse than a refusal.
    """
    fixa = _quadro("player/get_players", "", _jogadores(lineout=2, control=1))
    async with _falante(_respostas({JOGADORES: fixa})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.executar("volume", 30) == "nao_suportado"
    assert _pedido("player/set_volume?pid=1&level=30") not in servidor.recebidas


async def test_a_caixa_de_saida_fixa_comandada_pela_rede_muda_de_volume(caixa):
    """The control field only means anything on a fixed line out, and a network controlled
    one changes volume like any other speaker.
    """
    linha = "player/set_volume?pid=1&level=30"
    rede = _quadro("player/get_players", "", _jogadores(lineout=2, control=4))
    respostas = _respostas({JOGADORES: rede}, aceita=(linha,))
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.executar("volume", 30) is None
    assert _pedido(linha) in servidor.recebidas


async def test_uma_saida_fixa_que_nao_diz_quem_a_comanda_mantem_o_volume(caixa):
    """A speaker that does not publish the control field said nothing about what drives its
    output, and reading silence as a refusal takes the volume away from a healthy speaker for
    good, with the profile still publishing the level and the bar still refusing
    with nothing anywhere saying why. The refusal is for a speaker that names another one.
    """
    linha = "player/set_volume?pid=1&level=30"
    calada = _quadro("player/get_players", "", _jogadores(lineout=2))
    async with _falante(_respostas({JOGADORES: calada}, aceita=(linha,))) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.executar("volume", 30) is None
    assert _pedido(linha) in servidor.recebidas


async def test_o_volume_de_um_membro_de_saida_fixa_e_recusado_como_o_dele(caixa):
    """The same fact of the player list decides the volume whether it goes to this speaker or
    to a member of the group it leads; deciding it one way here and another way there leaves
    the panel with a bar that moves in silence on the number of the member.
    """
    fixa = _jogadores()
    fixa[0].update({"lineout": 2, "control": 1})
    respostas = _respostas({JOGADORES: _quadro("player/get_players", "", fixa)})
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.volume_de_escravo(IP_DA_COZINHA, 40) == "nao_suportado"
        # The speaker of this registration is not the one with the fixed output, and its own
        # volume is not touched by the refusal of a member.
        assert driver.estado().online is True
    assert not any(linha.startswith(b"heos://player/set_volume") for linha in servidor.recebidas)


async def test_o_evento_e_a_resposta_intermediaria_sao_descartados(caixa):
    """Three lines arrive before the real one: an event nobody asked for, an answer that says
    the command is still being processed, and the late answer of another command. Reading any
    of them as ours is how a poll starts reading the answer of the poll before it.
    """
    ruido = (
        _quadro("event/player_volume_changed", "pid=1&level=10")
        + _quadro("player/get_volume", "command under process&pid=1")
        + _quadro("player/get_mute", "pid=1&state=on")
        + _quadro("player/get_volume", "pid=1&level=36.0")
    )
    async with _falante(_respostas({VOLUME: ruido})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.volume == 36
    # The late answer of the mute question was dropped, and the mute was asked for on its own.
    assert estado.mudo is False
    assert estado.online is True


async def test_uma_caixa_que_so_manda_evento_nao_segura_o_poll(caixa):
    """A speaker that answers only lines nobody asked for runs out of the patience of the
    exchange instead of holding it open until the deadline of the whole poll.
    """
    so_evento = _quadro("event/player_now_playing_changed", "pid=1") * heos.LINHAS_ATE_DESISTIR
    async with _falante(_respostas({VOLUME: so_evento})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


@pytest.mark.parametrize(
    ("mensagem", "codigo"),
    [
        ("eid=1&text=Unrecognized Command", "nao_suportado"),
        ("eid=3&text=Invalid Arguments", "nao_suportado"),
        ("eid=15&text=Option not supported", "nao_suportado"),
        ("eid=9&text=Parameter out of range", "invalid_value"),
        ("eid=8&text=User not logged in", "auth_pendente"),
        ("eid=12&text=System error&syserrno=-1063", "auth_pendente"),
        ("eid=12&text=System error&syserrno=-9", "erro_aparelho"),
        ("eid=11&text=Internal error", "erro_aparelho"),
        ("eid=2&text=ID Not Valid", "eq_offline"),
        ("", "erro_aparelho"),
    ],
)
async def test_a_recusa_da_caixa_vira_um_codigo_estavel(caixa, mensagem, codigo):
    """A transport a streaming service does not accept is refused by design, which is why
    has stop apart from pause; the panel translates the code and never a phrase.
    """
    linha = "player/play_next?pid=1"
    recusa = _quadro("player/play_next", mensagem, ok=False)
    async with _falante(_respostas({linha: recusa})) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("proxima") == codigo


async def test_o_id_do_player_morto_e_relido_e_o_comando_vai_de_novo(caixa):
    """A firmware update renumbers the player id, and the refusal that says so is where the
    driver learns it between two polls: the list is read again and the command goes once
    more, on the FIRST press. Answering offline here would cost the integrator a second press
    on a speaker that is alive and answering, and would cost a scene the step.
    """
    velha = "player/play_next?pid=1"
    nova = "player/play_next?pid=7"
    recusa = _quadro("player/play_next", "eid=2&text=ID Not Valid", ok=False)
    respostas = _respostas({velha: recusa}, aceita=(nova,))
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        servidor.respostas[_pedido(JOGADORES)] = _quadro(
            "player/get_players", "", _jogadores(pid=7)
        )
        antes = len(servidor.recebidas)
        assert await driver.executar("proxima") is None
    assert servidor.recebidas[antes:] == [
        _pedido(velha),
        _pedido(JOGADORES),
        _pedido(nova),
    ]


async def test_o_id_morto_de_uma_caixa_que_sumiu_da_lista_e_offline(caixa):
    """The re-read is what tells a renumbered speaker from one that went away: a speaker the
    list does not carry any more is offline, and it is offline once and not twice.
    """
    linha = "player/play_next?pid=1"
    recusa = _quadro("player/play_next", "eid=2&text=ID Not Valid", ok=False)
    async with _falante(_respostas({linha: recusa})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        servidor.respostas[_pedido(JOGADORES)] = _quadro("player/get_players", "", [])
        assert await driver.executar("proxima") == "eq_offline"
    # The list was asked for again before giving up, and the dead id was never written twice.
    assert servidor.recebidas.count(_pedido(linha)) == 1


async def test_o_id_de_um_membro_do_grupo_nunca_e_trocado_pelo_nosso(caixa):
    """The volume of a member carries the player id of the MEMBER, so a refusal there is not
    a renumbering of this speaker: rewriting that command with our own id would turn up the
    volume of the leader under the name of the member.
    """
    linha = "player/set_volume?pid=2&level=40"
    recusa = _quadro("player/set_volume", "eid=2&text=ID Not Valid", ok=False)
    async with _falante(_respostas({linha: recusa})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert await driver.volume_de_escravo(IP_DA_COZINHA, 40) == "eq_offline"
    assert _pedido("player/set_volume?pid=1&level=40") not in servidor.recebidas


async def test_o_id_do_player_sai_da_linha_do_nosso_endereco_e_nunca_da_primeira(caixa):
    """The player list is the whole system, so the line of this registration is the one whose
    address is ours; a driver that took the first would command the speaker of another room.
    """
    renumerada = _quadro("player/get_players", "", _jogadores(pid=7))
    respostas = _respostas(
        {JOGADORES: renumerada},
        aceita=(
            "player/get_play_state?pid=7",
            "player/get_volume?pid=7",
            "player/get_mute?pid=7",
            "player/get_now_playing_media?pid=7",
        ),
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().online is True
    assert _pedido("player/get_volume?pid=7") in servidor.recebidas


async def test_uma_lista_de_players_vazia_e_uma_releitura_e_nao_uma_caixa_ausente(caixa):
    """The interface wakes up dormant and discovers the speakers of the system by itself, so
    the first list after a connection can be empty; it is asked for again before giving up.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.respostas[_pedido(JOGADORES)] = _quadro("player/get_players", "", [])
        antes = len(servidor.recebidas)
        await driver.atualizar()
    # The list was asked for twice inside one poll, and nothing else was asked at all.
    assert servidor.recebidas[antes:] == [_pedido(JOGADORES), _pedido(JOGADORES)]
    assert driver.estado().online is True, "one lost poll keeps the last state"


async def test_outra_caixa_no_mesmo_endereco_nunca_e_comandada_com_o_nosso_nome(caixa):
    """The identity is the serial and the address is only where it answered today,
    so a different serial at the same address is a neighbour and not this equipment.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.identidade_do_aparelho() == SERIAL
        outra = _quadro("player/get_players", "", _jogadores(serial="OUTRA"))
        servidor.respostas[_pedido(JOGADORES)] = outra
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_uma_caixa_que_nao_responde_fica_offline_depois_de_dois_polls(caixa):
    """One lost poll keeps the last state, two in a row is offline."""
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.respostas.clear()
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_uma_resposta_gigante_nao_derruba_o_daemon(caixa, monkeypatch):
    """A speaker on the LAN must never be able to make this daemon buffer without bound, and
    a line that never ends is cut long before it costs memory.
    """
    monkeypatch.setattr(heos, "LINHA_MAXIMA", 4096)
    enorme = _quadro("player/get_volume", "pid=1&level=36.0&lixo=" + "x" * 8000)
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        assert driver.estado().online is True
        servidor.respostas[_pedido(VOLUME)] = enorme
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_uma_resposta_sem_os_campos_nao_derruba_o_poll(caixa):
    """A speaker that answers no state, no level and nothing playing is a speaker with those
    facts unknown, and an unknown fact is None and never a false or a zero.
    """
    respostas = _respostas(
        {
            JOGADORES: _quadro("player/get_players", "", [1, "lixo", {}, *_jogadores()]),
            TRANSPORTE: _quadro("player/get_play_state", "pid=1"),
            VOLUME: _quadro("player/get_volume", "pid=1"),
            MUDO: _quadro("player/get_mute", "pid=1"),
            TOCANDO: _quadro("player/get_now_playing_media", "pid=1", {}),
        }
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert (estado.volume, estado.mudo, estado.reproduzindo, estado.tocando) == (
        None,
        None,
        None,
        None,
    )


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(caixa):
    """Only an IP literal reaches a speaker, so the hub is never a resolver."""
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor, _Cadastro(ip="caixa.local"))
        assert await driver.executar("proxima") == "eq_offline"
        await driver.atualizar()
        await driver.atualizar()
    assert servidor.recebidas == []
    assert driver.estado().online is False


async def test_a_identidade_e_o_serial_que_a_caixa_publica(caixa, monkeypatch):
    """The identity is a serial and never the address nor the player id, which a
    firmware update renumbers; a model that publishes no serial answers nothing.
    """
    async with _falante(_respostas()) as servidor:
        monkeypatch.setattr(heos, "PORTA_CLI", servidor.endereco[1])
        assert await Heos.identificar(MEU_IP) == SERIAL
        # A name is never dialled, so nothing of it reaches the socket.
        assert await Heos.identificar("caixa.local") is None
    assert servidor.recebidas.count(_pedido(JOGADORES)) == 1
    sem_serial = _quadro("player/get_players", "", _jogadores(serial=""))
    async with _falante(_respostas({JOGADORES: sem_serial})) as outro:
        monkeypatch.setattr(heos, "PORTA_CLI", outro.endereco[1])
        assert await Heos.identificar(MEU_IP) is None


async def test_o_grupo_e_reescrito_inteiro_para_ninguem_ser_expulso(caixa):
    """This command carries the WHOLE list of the group, so joining one that already has
    members means reading it first and writing it back with one more name; a list of two
    would throw everybody else out.
    """
    grupos = [
        {
            "name": "Cozinha + Quarto",
            "gid": 2,
            "players": [
                {"name": "Cozinha", "pid": 2, "role": "leader"},
                {"name": "Quarto", "pid": 3, "role": "member"},
            ],
        }
    ]
    respostas = _respostas(
        {GRUPOS: _quadro("group/get_groups", "", grupos)}, aceita=("group/set_group?pid=2,3,1",)
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("agrupar", IP_DA_COZINHA) is None
    assert servidor.recebidas[-1] == _pedido("group/set_group?pid=2,3,1")


async def test_a_resposta_de_grupo_vale_sob_os_dois_nomes_do_protocolo(caixa):
    """The published protocol prints the answers of the group commands under the player group
    and the speaker writes them under the group one; a driver that matched only one of the
    two would wait for a line that never comes.
    """
    respostas = _respostas(
        {
            GRUPOS: _quadro("player/get_groups", "", []),
            "group/set_group?pid=2,1": _quadro("player/set_group", "gid=2&pid=2,1"),
        }
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("agrupar", IP_DA_COZINHA) is None
    assert servidor.recebidas[-1] == _pedido("group/set_group?pid=2,1")


async def test_um_endereco_que_nao_e_uma_caixa_deste_sistema_e_recusado(caixa):
    """A group only ever exists between speakers of the same kind, so an address
    that is not a speaker of this system is refused instead of written to nobody.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("agrupar", "127.0.0.9") == "invalid_value"
        assert await driver.executar("agrupar", "caixa.local") == "invalid_value"
    assert not any(linha.startswith(b"heos://group/set_group") for linha in servidor.recebidas)


async def test_o_lider_desfaz_o_grupo_ficando_sozinho(caixa):
    """A leader alone is a group of nobody, which is how this protocol dismantles one."""
    grupos = [
        {
            "name": "Sala + Quarto",
            "gid": 1,
            "players": [
                {"name": "Sala", "pid": 1, "role": "leader"},
                {"name": "Quarto", "pid": 3, "role": "member"},
            ],
        }
    ]
    respostas = _respostas(
        {GRUPOS: _quadro("group/get_groups", "", grupos)}, aceita=("group/set_group?pid=1",)
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        assert await driver.executar("agrupar", "") is None
    assert servidor.recebidas[-1] == _pedido("group/set_group?pid=1")


async def test_o_membro_sai_reescrevendo_o_grupo_sem_ele(caixa):
    """A member walks out by writing the group back without itself, so the ones that stay
    keep playing; writing only its own id would dismantle the group of the customer.
    """
    grupos = [
        {
            "name": "Cozinha + Sala + Quarto",
            "gid": 2,
            "players": [
                {"name": "Cozinha", "pid": 2, "role": "leader"},
                {"name": "Sala", "pid": 1, "role": "member"},
                {"name": "Quarto", "pid": 3, "role": "member"},
            ],
        }
    ]
    respostas = _respostas(
        {GRUPOS: _quadro("group/get_groups", "", grupos)}, aceita=("group/set_group?pid=2,3",)
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        assert await driver.desfazer_grupo() is None
    assert servidor.recebidas[-1] == _pedido("group/set_group?pid=2,3")


async def test_o_lider_tira_um_membro_e_o_resto_do_grupo_fica(caixa):
    """Taking one member out cannot mean taking the group down, so the list goes back with
    everybody but that one.
    """
    grupos = [
        {
            "name": "Sala + Cozinha + Quarto",
            "gid": 1,
            "players": [
                {"name": "Sala", "pid": 1, "role": "leader"},
                {"name": "Cozinha", "pid": 2, "role": "member"},
                {"name": "Quarto", "pid": 3, "role": "member"},
            ],
        }
    ]
    respostas = _respostas(
        {GRUPOS: _quadro("group/get_groups", "", grupos)}, aceita=("group/set_group?pid=1,3",)
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        assert await driver.tirar_do_grupo(IP_DA_COZINHA) is None
    assert servidor.recebidas[-1] == _pedido("group/set_group?pid=1,3")


async def test_quem_nao_lidera_o_grupo_nao_tira_ninguem_dele(caixa):
    """Taking a member out is a movement of the leader, and this protocol writes it as the
    list of the group; a speaker that only follows would be writing the group of somebody
    else, so it refuses instead of rewriting a list that is not its to write.
    """
    grupos = [
        {
            "name": "Cozinha + Sala + Quarto",
            "gid": 2,
            "players": [
                {"name": "Cozinha", "pid": 2, "role": "leader"},
                {"name": "Sala", "pid": 1, "role": "member"},
                {"name": "Quarto", "pid": 3, "role": "member"},
            ],
        }
    ]
    respostas = _respostas({GRUPOS: _quadro("group/get_groups", "", grupos)})
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        assert await driver.tirar_do_grupo(IP_DO_QUARTO) == "nao_suportado"
        # A name is never dialled, here as anywhere else a speaker is addressed.
        assert await driver.tirar_do_grupo("quarto.local") == "invalid_value"
    assert not any(linha.startswith(b"heos://group/set_group") for linha in servidor.recebidas)


async def test_o_grupo_sem_lider_na_leitura_nao_derruba_o_driver(caixa):
    """A group in the middle of being formed can arrive with no leader in it, and a driver
    that took a leader for granted would drop the answer of a healthy system.
    """
    grupos = [{"name": "Em formacao", "gid": 9, "players": [{"name": "Sala", "pid": 1}]}]
    respostas = _respostas(
        {GRUPOS: _quadro("group/get_groups", "", grupos)}, aceita=("group/set_group?pid=1",)
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        assert await driver.desfazer_grupo() is None
        assert await driver.ler_grupo() == heos.Grupo()
    assert servidor.recebidas[-2] == _pedido("group/set_group?pid=1")


async def test_o_lider_lista_os_membros_pela_identidade_deles(caixa):
    """The key of a member is its serial and never its address, which is only
    where it answered today.
    """
    grupos = [
        {
            "name": "Sala + Cozinha",
            "gid": 1,
            "players": [
                {"name": "Sala", "pid": 1, "role": "leader"},
                {"name": "Cozinha", "pid": 2, "role": "member"},
            ],
        }
    ]
    respostas = _respostas({GRUPOS: _quadro("group/get_groups", "", grupos)})
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        grupo = await driver.ler_grupo()
    assert grupo == heos.Grupo((heos.Escravo(SERIAL_DA_COZINHA, IP_DA_COZINHA, "Cozinha"),))


def test_o_driver_carrega_todo_movimento_que_um_grupo_e_feito_de():
    """A multiroom driver that does not carry them is a driver the licence refuses to command
    instead of one it breaks, and the number would be dead with nobody saying why.
    """
    assert [movimento for movimento in MOVIMENTOS if not hasattr(Heos, movimento)] == []


async def test_o_lider_muda_o_volume_de_um_membro_do_grupo(caixa):
    """Unlike the other multiroom protocol of this hub, each speaker keeps its own volume
    while it is in a group, so the command is aimed at the member and not at the leader.
    """
    linha = "player/set_volume?pid=2&level=40"
    async with _falante(_respostas(aceita=(linha,))) as servidor:
        driver = caixa(servidor)
        assert await driver.volume_de_escravo(IP_DA_COZINHA, 40) is None
        assert await driver.volume_de_escravo(IP_DA_COZINHA, 101) == "invalid_value"
        assert await driver.volume_de_escravo("cozinha.local", 40) == "invalid_value"
    assert servidor.recebidas[-1] == _pedido(linha)


async def test_o_inicio_resolve_o_id_e_uma_caixa_calada_nao_estoura(caixa):
    """The gestor starts every driver at boot, so a speaker that is unplugged has to leave
    iniciar quietly instead of taking the boot of the hub with it.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        await driver.iniciar()
        assert servidor.recebidas == [_pedido(ACORDAR), _pedido(JOGADORES)]
        servidor.respostas.clear()
        await driver.parar()
        await driver.iniciar()
    assert driver.estado().online is False


async def test_uma_caixa_que_nao_responde_o_grupo_nao_estoura_no_gestor(caixa):
    """The owner of the group logic asks every speaker at boot, and a speaker that says
    nothing is a speaker with an unknown group, never an exception loose in the daemon.
    """
    async with _falante(_respostas()) as servidor:
        driver = caixa(servidor)
        assert await driver.ler_grupo() is None


async def test_o_membro_publica_o_que_o_lider_toca_quando_ele_mesmo_nao_nomeia_nada(caixa):
    """A member of a group plays the audio of its leader and names no title of its own for
    it, so the mirror of the leader fills that in while it is a member and never after.
    """
    respostas = _respostas(
        {
            JOGADORES: _quadro("player/get_players", "", _jogadores(gid=2)),
            TOCANDO: _quadro("player/get_now_playing_media", "pid=1", {}),
        }
    )
    async with _falante(respostas) as servidor:
        driver = caixa(servidor)
        driver.espelhar("Groove Salad", True)
        await driver.atualizar()
        assert driver.e_escravo() is True
        assert driver.estado().tocando == "Groove Salad"
        # The owner of the group says it left, and nothing of the old group stays behind.
        driver.marcar_grupo(False)
        servidor.respostas[_pedido(JOGADORES)] = _quadro("player/get_players", "", _jogadores())
        await driver.atualizar()
        await driver.atualizar()
    assert driver.e_escravo() is False
    assert driver.estado().tocando is None
    # What the owner of the group logic reads to reconcile: it followed a leader and stopped.
    assert driver.saiu_do_grupo() is True
    assert driver.no_grupo() is False


@pytest.mark.parametrize(
    ("versao", "antiga"),
    [
        ("3.34.240", False),
        ("3.30.100", True),
        ("4.0", False),
        ("2", True),
        ("", False),
        ("x", False),
    ],
)
def test_a_versao_do_firmware_e_comparada_por_numero_e_nunca_por_texto(versao, antiga):
    """A firmware older than the published protocol may not carry a command of this file, and
    a version this driver cannot read as numbers is not a claim that it is old.
    """
    assert heos._antiga(versao) is antiga


async def test_o_firmware_antigo_e_avisado_uma_vez_e_a_caixa_segue_lida(caixa, caplog):
    """The warning is a line of the diary and never a refusal: an old speaker still answers
    everything this poll asks, and saying it twice a poll would bury the diary.
    """
    caplog.set_level(logging.WARNING, logger="iphub.drivers.nativos.heos")
    antiga = _quadro("player/get_players", "", _jogadores(version="3.30.100"))
    async with _falante(_respostas({JOGADORES: antiga})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
        await driver.atualizar()
    assert driver.estado().online is True
    assert len([linha for linha in caplog.messages if "firmware" in linha]) == 1


async def test_uma_linha_que_nao_e_do_protocolo_e_pulada(caixa):
    """A line that is not JSON, or that is JSON of another shape, is not an answer of this
    protocol; dropping the whole exchange on it would hand a speaker on the LAN the poll.
    """
    lixo = b"nao sou json\r\n" + b'"nem eu"\r\n' + b'{"outro": {}}\r\n'
    nossa = _quadro("player/get_volume", "pid=1&level=36.0")
    async with _falante(_respostas({VOLUME: lixo + nossa})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.estado().volume == 36
    assert driver.estado().online is True


async def test_identificar_uma_caixa_que_recusa_a_conexao_nao_estoura(caixa, monkeypatch):
    """The sweep asks every address it found, and most of them are not a speaker of this
    system; a refused connection is an answer of nothing and never an exception in the sweep.
    """
    async with _falante(_respostas()) as servidor:
        porta = servidor.endereco[1]
    monkeypatch.setattr(heos, "PORTA_CLI", porta)
    assert await Heos.identificar(MEU_IP) is None


async def test_o_lider_do_grupo_nao_e_lido_como_membro(caixa):
    """The group id of this protocol is the player id of the leader, so a speaker whose group
    is its own id leads it; reading that as a member would route its own volume elsewhere.
    """
    lidera = _quadro("player/get_players", "", _jogadores(gid=1))
    async with _falante(_respostas({JOGADORES: lidera})) as servidor:
        driver = caixa(servidor)
        await driver.atualizar()
    assert driver.e_escravo() is False
    assert driver.estado().online is True

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Onkyo and Integra receiver over eISCP, against a simulated receiver.

Two simulated servers, because the receiver is two protocols on one port number.
The UDP one answers the interview and names the TCP port of control, and the TCP one holds
the long connection state is pushed on. Every test asserts the bytes that landed on the wire,
header included, because a frame of this protocol carries its own length and a driver that
writes the wrong header is a driver the receiver reads as garbage.
"""

import asyncio
import struct
from collections.abc import Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field

import pytest

from iphub.config import Item
from iphub.drivers.base import CODIGOS
from iphub.drivers.manifesto import Auth, por_lista, validar
from iphub.drivers.nativos import onkyo
from iphub.drivers.nativos.onkyo import Onkyo
from iphub.drivers.simulado import ServidorDatagrama, ServidorLinha

MAC = "0009B0123456"
MODELO = "TX-NR609"
FIM = b"\r\n"
FIM_TCP = b"\x1a\r\n"
FIM_UDP = b"\x19\r\n"

# The two questions the discovery asks, one as the unit of Onkyo and one as the Pioneer one.
PEDE_ONKYO = b"!xECNQSTN"
PEDE_PIONEER = b"!pECNQSTN"


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = MAC
    ip: str = "127.0.0.1"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


def _quadro(dado: bytes) -> bytes:
    """One eISCP frame written here by hand, so the test never borrows the code it checks."""
    return struct.pack("! 4s I I B 3s", b"ISCP", 16, len(dado), 1, b"\x00\x00\x00") + dado


def _envio(mensagem: bytes) -> bytes:
    """The frame of a command as the simulated receiver records it, without the terminator
    the server strips from the line.
    """
    return _quadro(b"!1" + mensagem + FIM)[: -len(FIM)]


def _diz(mensagem: bytes, fim: bytes = FIM_TCP) -> bytes:
    """A frame the receiver writes back, with the terminator the firmware felt like using."""
    return _quadro(b"!1" + mensagem + fim)


def _ecn(porta: int, identidade: str = MAC) -> bytes:
    return _quadro(b"!1ECN" + f"{MODELO}/{porta:05d}/DX/{identidade}".encode() + FIM_UDP)


def _respostas(**por_codigo: bytes) -> dict[bytes, bytes]:
    """What the receiver answers to each question of a poll, in one line of test setup."""
    padrao = {
        b"PWR": b"PWR01",
        b"MVL": b"MVL19",
        b"AMT": b"AMT00",
        b"SLI": b"SLI12",
        b"LMD": b"LMD00",
    }
    padrao.update({codigo.encode("ascii"): valor for codigo, valor in por_codigo.items()})
    return {
        _envio(codigo + b"QSTN"): _diz(resposta) for codigo, resposta in padrao.items() if resposta
    }


async def _ate(condicao: Callable[[], bool], prazo_s: float = 2.0) -> None:
    """Waits for the simulated receiver, or for the listener, to have handled a frame.

    A frame is written on a socket and read in a task of its own, so asserting the
    instant the driver returned is a test that passes on a quiet machine and fails on a
    busy one.
    """
    laco = asyncio.get_running_loop()
    limite = laco.time() + prazo_s
    while not condicao():
        assert laco.time() < limite, "the simulated receiver never got there"
        await asyncio.sleep(0.005)


@asynccontextmanager
async def _bancada(
    monkeypatch,
    respostas: dict[bytes, bytes] | None = None,
    *,
    saudacao: bytes = b"",
    identidade: str = MAC,
    descoberta: tuple[bytes, ...] = (PEDE_ONKYO, PEDE_PIONEER),
    campos: dict[str, str] | None = None,
    listas: dict[str, tuple] | None = None,
    ip: str = "127.0.0.1",
):
    """One simulated receiver: the TCP of control and the UDP that names its port."""
    async with ServidorLinha(
        respostas if respostas is not None else _respostas(),
        saudacao=saudacao,
        terminador=FIM,
    ) as tcp:
        anuncio = _ecn(tcp.endereco[1], identidade)
        async with ServidorDatagrama({_quadro(p + FIM): anuncio for p in descoberta}) as udp:
            monkeypatch.setattr(onkyo, "PORTA_PADRAO", udp.endereco[1])
            monkeypatch.setattr(onkyo, "PRAZO_UDP_S", 0.5)
            monkeypatch.setattr(onkyo, "PRAZO_RESPOSTA_S", 0.5)
            monkeypatch.setattr(onkyo, "TEMPO_LIMITE_S", 1.0)
            driver = Onkyo(_Cadastro(ip=ip, campos=campos or {}, listas=listas or {}))
            try:
                yield driver, tcp, udp
            finally:
                await driver.parar()


def test_o_manifesto_e_o_de_um_receiver_que_toca_o_que_esta_na_rede():
    """Power, level, mute, input, sound mode, presets, keys and the transport of
    the network player, which is the only transport this receiver has.
    """
    manifesto = Onkyo.MANIFESTO
    assert validar(manifesto) is None
    assert manifesto.tipo == "receiver_onkyo"
    assert manifesto.categoria == "receiver"
    assert manifesto.marca == "Onkyo"
    # The protocol has no credential of any kind, so the inherited autenticar is the right one.
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
        "tecla",
        "tocar",
        "pausar",
        "parar",
        "proxima",
        "anterior",
        "comando_extra",
    )
    assert "agrupar" not in manifesto.capacidades
    # The only guide command of this protocol is sent to the TV over HDMI and confirms
    # nothing, so declaring the key would put a button on the panel that does not work.
    assert "guia" not in manifesto.teclas
    assert {"cima", "ok", "menu", "digito_0", "play_pause"} <= set(manifesto.teclas)


def test_a_assinatura_de_descoberta_e_so_a_das_duas_marcas():
    """Two manifests claiming the same signature is a test error, so this driver
    claims the two names of its own maker and nothing that belongs to another one.
    """
    descoberta = Onkyo.MANIFESTO.descoberta
    assert descoberta.ssdp_fabricantes == ("onkyo", "integra")
    assert descoberta.ssdp_st == ()
    assert descoberta.mdns_servicos == ()


def test_o_cadastro_pede_o_que_pergunta_nenhuma_responde_e_mais_nada():
    """The two facts of the model that no question of this protocol reveals are
    fields, and nothing else is; the port and the MAC are not, because the receiver answers
    both. The scale is 50, 80, 100 or 200, and which code is a network input changes by model
    exactly like every other input code.
    """
    campos = Onkyo.MANIFESTO.config_campos
    assert [campo.nome for campo in campos] == ["escala_volume", "entradas_de_rede"]
    assert campos[0].padrao == "50"
    assert campos[1].padrao == "2B,29"
    for idioma in ("pt", "en"):
        assert Onkyo.MANIFESTO.textos[idioma]["campo_escala_volume"]
        assert Onkyo.MANIFESTO.textos[idioma]["campo_entradas_de_rede"]


def test_o_driver_sugere_os_codigos_que_ninguem_decora():
    """The value of an input is a hexadecimal pair of the chart, so a registration
    that was just created already carries the usual ones for the integrator to correct.
    """
    sugeridas = por_lista(Onkyo.MANIFESTO)
    assert set(sugeridas) == {"entradas", "atalhos", "modos"}
    assert [item.valor for item in sugeridas["entradas"]][:2] == ["12", "10"]
    assert [item.valor for item in sugeridas["atalhos"]][:2] == ["01", "02"]
    assert [item.valor for item in sugeridas["modos"]][:2] == ["00", "01"]


async def test_a_identidade_e_o_mac_que_a_entrevista_responde(monkeypatch):
    """The identity is a MAC and never the address, and it is asked over UDP with
    no registration at all, which also gives the wake on LAN packet its address.
    """
    async with _bancada(monkeypatch) as (_driver, _tcp, udp):
        assert await Onkyo.identificar("127.0.0.1") == MAC
        # A name is not an address, and the hub never resolves one for a device.
        assert await Onkyo.identificar("receiver.local") is None
    assert udp.recebidos[:2] == [_quadro(PEDE_ONKYO + FIM), _quadro(PEDE_PIONEER + FIM)]


async def test_a_pergunta_da_marca_irma_e_a_que_acha_uma_unidade_dela(monkeypatch):
    """A unit of the sister brand answers only the second packet, so a driver
    that sends one of them loses it in silence.
    """
    async with _bancada(monkeypatch, descoberta=(PEDE_PIONEER,)) as (_driver, _tcp, _udp):
        assert await Onkyo.identificar("127.0.0.1") == MAC


async def test_uma_unidade_sem_mac_na_resposta_nao_vira_cadastro(monkeypatch):
    """An older firmware answers model, port and area and no identifier at all, and a hub that
    invented one would key an equipment by something that is not its identity.
    """
    async with _bancada(monkeypatch, identidade="") as (_driver, _tcp, _udp):
        assert await Onkyo.identificar("127.0.0.1") is None


async def test_a_porta_de_controle_vem_dentro_da_resposta_da_descoberta(monkeypatch):
    """The question goes to the published UDP port and the connection goes to the
    TCP port that answer names, which is the part of this protocol everybody gets wrong.
    """
    async with _bancada(monkeypatch) as (driver, tcp, udp):
        await driver.iniciar()
        await _ate(lambda: tcp.conexoes == 1)
        assert driver.conectado() is True
        assert udp.endereco[1] != tcp.endereco[1]
        assert driver.identidade_do_aparelho() == MAC


async def test_uma_unidade_calada_na_entrevista_cai_na_porta_publicada(monkeypatch):
    """A unit that answers no interview still has to be reachable, so the published port is
    what the connection falls back to instead of the driver giving up on it.
    """
    async with ServidorLinha(_respostas(), terminador=FIM) as tcp:
        # Nobody listens on UDP here, which is a unit with the discovery service off.
        monkeypatch.setattr(onkyo, "PORTA_PADRAO", tcp.endereco[1])
        monkeypatch.setattr(onkyo, "PRAZO_UDP_S", 0.3)
        monkeypatch.setattr(onkyo, "PRAZO_RESPOSTA_S", 0.5)
        driver = Onkyo(_Cadastro())
        try:
            await driver.iniciar()
            await driver.atualizar()
        finally:
            await driver.parar()
    assert driver.estado().online is True
    assert driver.estado().ligado is True
    assert driver.identidade_do_aparelho() is None


@pytest.mark.parametrize("anunciado", ["00-09-B0-12-34-56", "ABCDEFGH1234", "0009B012345"])
async def test_um_identificador_que_nao_e_um_mac_inteiro_nunca_vira_identidade(
    monkeypatch, anunciado
):
    """An identity is a whole MAC, and half of one is worse than none.

    The interview carries a string a firmware wrote, and a reader that took the
    hexadecimal head of it would hand the driver a truncated MAC, which keys nothing and which
    the magic packet of wake on LAN then drops in silence. Without Network Standby that packet
    is the only door a receiver in standby has, and it would close because of a string.
    """
    async with ServidorDatagrama({}) as acordador:
        monkeypatch.setattr(onkyo, "PORTA_WOL", acordador.endereco[1])
        async with _bancada(monkeypatch, identidade=anunciado) as (driver, tcp, _udp):
            await driver.iniciar()
            await _ate(lambda: tcp.conexoes == 1)
            assert driver.identidade_do_aparelho() is None
            assert await Onkyo.identificar("127.0.0.1") is None
            # The receiver drops off the network, which is the only case the packet exists for.
            await tcp.parar()
            await _ate(lambda: not driver.conectado())
            assert await driver.executar("ligar") == "eq_offline"
            await _ate(lambda: acordador.recebidos != [])
    assert acordador.recebidos == [b"\xff" * 6 + bytes.fromhex(MAC) * 16]


async def test_outra_unidade_no_mesmo_endereco_nunca_e_comandada(monkeypatch):
    """The identity is the key and the address is only where it answered today, so
    the interview runs again on every connection and is judged against the identity known.

    A lease hands this address to another receiver, this long connection dies, and a
    driver that redialled a remembered port would command the stranger under the name of this
    registration, with identidade_do_aparelho still answering the MAC of the old unit. That is
    not a lost poll, it is a verdict.
    """
    async with _bancada(monkeypatch) as (driver, tcp, udp):
        await driver.iniciar()
        await _ate(lambda: tcp.conexoes == 1)
        assert driver.identidade_do_aparelho() == MAC
        outra = "0009B0AAAAAA"
        udp.respostas = {pedido: _ecn(tcp.endereco[1], outra) for pedido in udp.respostas}
        await tcp.parar()
        await _ate(lambda: not driver.conectado())
        await driver.atualizar()
        assert await driver.executar("ligar") == "eq_offline"
    assert driver.estado().online is False, "another unit is a verdict, not a lost poll"
    assert driver.estado().detalhe == "eq_offline"
    assert driver.identidade_do_aparelho() == MAC, "the stranger was never adopted"
    assert tcp.recebidas == [], "no command reached the address after the identity changed"


async def test_um_datagrama_que_nao_e_resposta_de_descoberta_e_ignorado(monkeypatch):
    """In spirit: an answer is data, so anything on that port that does not read as
    a frame of this protocol is dropped and the next datagram is still read.
    """
    async with ServidorLinha(_respostas(), terminador=FIM) as tcp:
        lixo = b"HTTP/1.1 200 OK\r\n\r\n"
        respostas = {
            _quadro(PEDE_ONKYO + FIM): lixo,
            _quadro(PEDE_PIONEER + FIM): _ecn(tcp.endereco[1]),
        }
        async with ServidorDatagrama(respostas) as udp:
            monkeypatch.setattr(onkyo, "PORTA_PADRAO", udp.endereco[1])
            monkeypatch.setattr(onkyo, "PRAZO_UDP_S", 0.5)
            assert await Onkyo.identificar("127.0.0.1") == MAC


async def test_um_poll_le_energia_volume_mudo_entrada_e_modo(monkeypatch):
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
    assert tcp.recebidas == [
        _envio(b"PWRQSTN"),
        _envio(b"MVLQSTN"),
        _envio(b"AMTQSTN"),
        _envio(b"SLIQSTN"),
        _envio(b"LMDQSTN"),
    ]
    estado = driver.estado()
    assert estado.online is True
    assert (estado.ligado, estado.mudo, estado.fonte, estado.modo) == (True, False, "12", "00")
    # 0x19 is 25 of the 50 steps of the default scale, which is half of the bar.
    assert estado.volume == 50


async def test_o_poll_so_pergunta_pelo_tocador_numa_entrada_de_rede(monkeypatch):
    """The transport and the title only mean anything on a network input, and a
    model with no network board answers neither, which would cost a deadline of silence.
    """
    respostas = _respostas(SLI=b"SLI2B", NST=b"NSTP--", NTI=b"NTIRadio Livre")
    async with _bancada(monkeypatch, respostas) as (driver, tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
        assert _envio(b"NSTQSTN") not in tcp.recebidas, "the input was unknown on the first poll"
        await driver.atualizar()
    assert _envio(b"NSTQSTN") in tcp.recebidas
    assert _envio(b"NTIQSTN") in tcp.recebidas
    estado = driver.estado()
    assert (estado.fonte, estado.reproduzindo, estado.tocando) == ("2B", True, "Radio Livre")


async def test_a_entrada_de_rede_e_a_do_cadastro_e_nunca_uma_lista_deste_arquivo(monkeypatch):
    """Which code is a network input is a fact of the model, exactly like the code
    of every other input, so it comes from the registration.

    On a model where the network board answers another code, a set written into this file
    would leave reproduzindo and tocando dead forever, with five transport capacities declared
    and DP 148 never reporting a title; and where that same code means another input, the
    driver would claim the receiver plays on something that has no player, which is the defect
     the speakers already paid for.
    """
    respostas = _respostas(SLI=b"SLI80", NST=b"NSTP--", NTI=b"NTIRadio Livre")
    async with _bancada(monkeypatch, respostas, campos={"entradas_de_rede": "80"}) as (
        driver,
        tcp,
        _udp,
    ):
        await driver.iniciar()
        await driver.atualizar()
        await driver.atualizar()
    assert _envio(b"NSTQSTN") in tcp.recebidas
    estado = driver.estado()
    assert (estado.fonte, estado.reproduzindo, estado.tocando) == ("80", True, "Radio Livre")


async def test_um_receiver_sem_placa_de_rede_nunca_afirma_que_toca(monkeypatch):
    """An empty field is a receiver with no network player, and there reproduzindo never
    leaves None instead of being right by accident on the model of the example.
    """
    respostas = _respostas(SLI=b"SLI2B", NST=b"NSTP--")
    async with _bancada(monkeypatch, respostas, campos={"entradas_de_rede": ""}) as (
        driver,
        tcp,
        _udp,
    ):
        await driver.iniciar()
        await driver.atualizar()
        await driver.atualizar()
        assert await driver.executar("tocar") is None
    assert _envio(b"NSTQSTN") not in tcp.recebidas
    assert driver.estado().fonte == "2B"
    assert driver.estado().reproduzindo is None


async def test_a_entrada_e_o_modo_voltam_ao_estado_com_a_grafia_do_cadastro(monkeypatch):
    """Matches the value of the driver against the item of the registration by plain
    equality, so the wire takes upper case and the state keeps the spelling that was saved.

    Normalising only one of the two sides is two decisions. A list saved in lower case
    would command the receiver correctly and never match back, so DP 146 and DP 147 would go
    quiet for that number forever while the command kept working, and the panel of the app
    would show the wrong input or none.
    """
    listas = {"entradas": (Item("Rede", "2b"),), "modos": (Item("Todos canais", "0c"),)}
    async with _bancada(monkeypatch, _respostas(SLI=b"SLI2B", LMD=b"LMD0C"), listas=listas) as (
        driver,
        tcp,
        _udp,
    ):
        await driver.iniciar()
        await driver.atualizar()
        assert (driver.estado().fonte, driver.estado().modo) == ("2b", "0c")
        assert await driver.executar("fonte", "2b") is None
        assert await driver.executar("modo", "0c") is None
        await _ate(lambda: _envio(b"LMD0C") in tcp.recebidas)
        assert (driver.estado().fonte, driver.estado().modo) == ("2b", "0c")
    # The wire of this receiver is upper case hexadecimal whatever the registration saved.
    assert _envio(b"SLI2B") in tcp.recebidas


async def test_o_transporte_e_o_titulo_ficam_em_none_fora_de_uma_entrada_de_rede(monkeypatch):
    """The transport frame reports stop on a receiver that is playing HDMI, and
    reading it there is exactly the defect that made a speaker say paused while it played.
    """
    # The receiver pushes the two frames on its own, after the poll already learned the input:
    # the transport says stop while an HDMI input plays, and the title is the one of the
    # source before this one, which the firmware never clears. The volume at the end is the
    # sentinel saying the listener read all three.
    respostas = _respostas(SLI=b"SLI12")
    respostas[_envio(b"LMDQSTN")] = (
        _diz(b"LMD00") + _diz(b"NSTS--") + _diz(b"NTIsobra da fonte anterior") + _diz(b"MVL05")
    )
    async with _bancada(monkeypatch, respostas) as (driver, tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
        await _ate(lambda: driver.estado().volume == 10)
    assert _envio(b"NSTQSTN") not in tcp.recebidas, "the input is not a network one"
    estado = driver.estado()
    assert estado.fonte == "12"
    assert estado.reproduzindo is None
    assert estado.tocando is None


async def test_o_receiver_empurra_estado_sem_ninguem_perguntar(monkeypatch):
    """The connection is long because the receiver reports a knob turned on its
    own front panel, and the terminator of that frame may come with no SUB in front.
    """
    empurrado = _diz(b"PWR01", FIM) + _diz(b"MVL32", FIM_TCP)
    async with _bancada(monkeypatch, saudacao=empurrado) as (driver, _tcp, _udp):
        await driver.iniciar()
        await _ate(lambda: driver.estado().ligado is True)
    # 0x32 is 50 of the 50 steps of the scale, which is the whole bar.
    assert driver.estado().volume == 100


async def test_ligar_desligar_e_o_mudo_escrevem_o_valor_e_nunca_o_que_alterna(monkeypatch):
    """PWRALL takes down the zones this hub does not control and AMTTG toggles,
    which inverts the result of every report that arrived late.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        assert await driver.executar("ligar") is None
        assert await driver.executar("desligar") is None
        assert await driver.executar("mudo", True) is None
        assert await driver.executar("mudo", False) is None
        await _ate(lambda: len(tcp.recebidas) == 4)
    assert tcp.recebidas == [
        _envio(b"PWR01"),
        _envio(b"PWR00"),
        _envio(b"AMT01"),
        _envio(b"AMT00"),
    ]
    assert driver.estado().mudo is False
    assert driver.estado().ligado is False


@pytest.mark.parametrize(
    ("escala", "no_fio", "lido"),
    [("50", b"MVL32", 100), ("80", b"MVL50", 100), ("100", b"MVL64", 100), ("200", b"MVLC8", 100)],
)
async def test_o_volume_vai_e_volta_na_escala_que_o_cadastro_diz(monkeypatch, escala, no_fio, lido):
    """The scale of the model is not on the wire and nothing asks it, so the
    registration says it; with the wrong one the whole bar of the panel is wrong.
    """
    respostas = _respostas(MVL=no_fio)
    async with _bancada(monkeypatch, respostas, campos={"escala_volume": escala}) as (
        driver,
        tcp,
        _udp,
    ):
        assert await driver.executar("volume", 100) is None
        await _ate(lambda: len(tcp.recebidas) == 1)
        await driver.atualizar()
    assert tcp.recebidas[0] == _envio(no_fio)
    assert driver.estado().volume == lido


async def test_uma_escala_que_nao_existe_no_cadastro_cai_no_padrao(monkeypatch):
    """A field the integrator typed by hand is data, so a scale outside the four of the
    protocol falls back to the published default instead of dividing by it.
    """
    async with _bancada(monkeypatch, campos={"escala_volume": "0"}) as (driver, tcp, _udp):
        assert await driver.executar("volume", 50) is None
        await _ate(lambda: len(tcp.recebidas) == 1)
    assert tcp.recebidas == [_envio(b"MVL19")]


@pytest.mark.parametrize("valor", [101, -1, "50", 50.0, True, None])
async def test_um_volume_fora_do_contrato_nunca_chega_ao_fio(monkeypatch, valor):
    """True is an int in Python: a mute arriving where a volume fits would silence a room."""
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        assert await driver.executar("volume", valor) == "invalid_value"
    assert tcp.recebidas == []


async def test_a_entrada_o_modo_e_o_atalho_vao_atras_do_codigo_de_tres_letras(monkeypatch):
    """The value of a list of the registration is the hexadecimal code of the chart, and the
    driver only writes the three letters in front of it.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        assert await driver.executar("fonte", "2b") is None
        assert await driver.executar("modo", "0c") is None
        assert await driver.executar("atalho", "03") is None
        await _ate(lambda: len(tcp.recebidas) == 3)
    assert tcp.recebidas == [_envio(b"SLI2B"), _envio(b"LMD0C"), _envio(b"PRS03")]
    assert driver.estado().fonte == "2B"
    assert driver.estado().modo == "0C"


@pytest.mark.parametrize(
    "valor",
    ["2B\r\n!1PWR00", "SLI 2B", "", "x" * 20, 7, None, "2B;PWR00"],
)
async def test_um_valor_de_lista_que_escreveria_um_segundo_quadro_nunca_chega_ao_fio(
    monkeypatch, valor
):
    """The value decides bytes on a socket, and one carrying a terminator would
    close the frame and write a second command that nobody wrote in this file.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        assert await driver.executar("fonte", valor) == "invalid_value"
        assert await driver.executar("modo", valor) == "invalid_value"
        assert await driver.executar("atalho", valor) == "invalid_value"
        assert await driver.executar("tecla", valor) == "invalid_value"
    assert tcp.recebidas == []


@pytest.mark.parametrize(
    "valor",
    ["PWR\r\n!1MVL00", "PWR 01", "", "PW", "x" * 40, 7, None, "PWR;01"],
)
async def test_um_comando_extra_fora_do_alfabeto_nunca_chega_ao_fio(monkeypatch, valor):
    """The escape of this driver is still a frame: three letters of code and a parameter of a
    closed alphabet, so what is typed on the spot cannot write a second command either.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        assert await driver.executar("comando_extra", valor) == "invalid_value"
    assert tcp.recebidas == []


@pytest.mark.parametrize(
    ("palavra", "no_fio"),
    [
        ("mais", b"MVLUP"),
        ("menos", b"MVLDOWN"),
        ("cima", b"OSDUP"),
        ("ok", b"OSDENTER"),
        ("menu", b"OSDMENU"),
        ("voltar", b"NTCRETURN"),
        ("info", b"NTCDISPLAY"),
        ("play_pause", b"NTCP/P"),
        ("canal_mais", b"NTCCHUP"),
        ("digito_7", b"NTC7"),
    ],
)
async def test_cada_tecla_do_vocabulario_vai_para_o_subsistema_dela(monkeypatch, palavra, no_fio):
    """The word of the vocabulary is the same for every driver, and this one knows
    that the arrows walk the menu on screen and the digits go to the network player.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        assert await driver.executar("tecla", palavra) is None
        await _ate(lambda: len(tcp.recebidas) == 1)
    assert tcp.recebidas == [_envio(no_fio)]


async def test_o_transporte_vai_para_o_tocador_de_rede(monkeypatch):
    """Parar exists beside pausar because they are different moves, and here they
    are two different words of the network player of the receiver.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        for acao in ("tocar", "pausar", "parar", "proxima", "anterior"):
            assert await driver.executar(acao) is None
        await _ate(lambda: len(tcp.recebidas) == 5)
    assert tcp.recebidas == [
        _envio(b"NTCPLAY"),
        _envio(b"NTCPAUSE"),
        _envio(b"NTCSTOP"),
        _envio(b"NTCTRUP"),
        _envio(b"NTCTRDN"),
    ]


async def test_o_transporte_so_afirma_que_toca_numa_entrada_de_rede(monkeypatch):
    """The optimistic value is written only where the receiver can honour it."""
    async with _bancada(monkeypatch, _respostas(SLI=b"SLI2B")) as (driver, _tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
        assert await driver.executar("tocar") is None
        assert driver.estado().reproduzindo is True
        assert await driver.executar("pausar") is None
        assert driver.estado().reproduzindo is False
        assert await driver.executar("fonte", "12") is None
        # The input left the network, so the transport of the receiver means nothing again.
        assert driver.estado().reproduzindo is None


async def test_um_comando_extra_vai_inteiro_com_o_codigo_e_o_parametro(monkeypatch):
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        assert await driver.executar("comando_extra", "dif01") is None
        assert await driver.executar("comando_extra", "NTCPLAY") is None
        await _ate(lambda: len(tcp.recebidas) == 2)
    assert tcp.recebidas == [_envio(b"DIF01"), _envio(b"NTCPLAY")]


async def test_a_reconsulta_depois_do_ligar_pergunta_de_novo(monkeypatch):
    """The state read right after a power on is stale, so the driver asks again
    once the receiver has woken up, which is what the reread would miss.
    """
    monkeypatch.setattr(onkyo, "ATRASO_APOS_LIGAR_S", 0.02)
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        assert await driver.executar("ligar") is None
        await _ate(lambda: _envio(b"PWRQSTN") in tcp.recebidas)
    assert tcp.recebidas[0] == _envio(b"PWR01")
    assert _envio(b"LMDQSTN") in tcp.recebidas


async def test_o_parametro_de_valor_indisponivel_nao_derruba_o_poll(monkeypatch):
    """N/A is a real value of the protocol: the receiver supports the function and has no
    value for it now, so reading it as a number would raise inside the reader.
    """
    async with _bancada(monkeypatch, _respostas(MVL=b"MVLN/A", LMD=b"LMDN/A")) as (
        driver,
        _tcp,
        _udp,
    ):
        await driver.iniciar()
        await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    assert estado.volume is None
    assert estado.modo is None


async def test_um_quadro_malformado_nunca_apaga_um_fato_que_o_receiver_nao_desmentiu(monkeypatch):
    """A parameter this driver cannot read is a log, never a None written over a fact.

    The panel and DP 101 would lose a power the receiver never said had changed, because
    one frame came malformed. The mute at the end is the sentinel saying the listener read all
    four frames, so the two that say nothing were really applied and dropped.
    """
    saudacao = _diz(b"PWR01") + _diz(b"MVL19") + _diz(b"PWR") + _diz(b"MVLzz") + _diz(b"AMT00")
    async with _bancada(monkeypatch, saudacao=saudacao) as (driver, _tcp, _udp):
        await driver.iniciar()
        await _ate(lambda: driver.estado().mudo is False)
    estado = driver.estado()
    assert estado.ligado is True, "an empty power parameter never erases a True"
    assert estado.volume == 50, "a volume that is not a number never erases a level"


async def test_um_quadro_grande_demais_derruba_a_conexao_e_nao_o_driver(monkeypatch):
    """A receiver on the LAN never sizes the memory of this daemon, and a frame
    that lies about its length leaves the stream with no boundary to resume from.
    """
    mentiroso = struct.pack("! 4s I I B 3s", b"ISCP", 16, 900_000, 1, b"\x00\x00\x00") + b"!1PWR01"
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
        assert driver.estado().online is True
        tcp.respostas[_envio(b"PWRQSTN")] = mentiroso
        await driver.atualizar()
        await _ate(lambda: not driver.conectado())
        assert driver.estado().online is True, "one frame nobody understands is not an offline"
        # The next poll dials again, which is what a long connection that died has to do.
        tcp.respostas[_envio(b"PWRQSTN")] = _diz(b"PWR01")
        await driver.atualizar()
        await _ate(lambda: tcp.conexoes == 2)
    assert driver.estado().online is True
    assert driver.estado().detalhe == ""


async def test_um_cabecalho_que_nao_e_do_protocolo_derruba_a_conexao(monkeypatch):
    """A frame with another magic is not a frame of this protocol, and there is no place in
    the stream to resume reading from.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
        assert driver.estado().online is True
        tcp.respostas[_envio(b"PWRQSTN")] = b"HTTP/1.1 200 OK\r\n\r\n" + b"\x00" * 8
        await driver.atualizar()
        await _ate(lambda: not driver.conectado())
    assert driver.estado().online is True, "one answer nobody understands is not an offline"


async def test_um_receiver_que_so_responde_lixo_fica_offline_dizendo_erro_aparelho(monkeypatch):
    """A receiver whose every answer kills the connection is not an online receiver, and the
    detail says WHICH silence this is: the frames are unreadable, not absent.

    Erro_aparelho is raised inside the reader, which runs in a task of its own and can
    return a code to nobody; the poll that finds the connection gone and no frame read is the
    only observable place that code has, and without it it would be a dead constant.
    """
    lixo = b"HTTP/1.1 200 OK\r\n\r\n" + b"\x00" * 8
    respostas = {chave: lixo for chave in _respostas()}
    async with _bancada(monkeypatch, respostas) as (driver, _tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
        assert driver.estado().online is False, "one lost poll keeps the last state, which is off"
        await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "erro_aparelho"


async def test_um_receiver_que_recusa_a_conexao_e_offline_depois_de_dois_polls(monkeypatch):
    """One lost poll keeps the last state, two in a row is offline."""
    async with _bancada(monkeypatch) as (driver, tcp, udp):
        await driver.iniciar()
        await driver.atualizar()
        assert driver.estado().online is True
        await tcp.parar()
        await udp.parar()
        await _ate(lambda: not driver.conectado())
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
        assert driver.estado().online is False
        assert driver.estado().detalhe == "eq_offline"


async def test_um_receiver_que_atende_e_emudece_nao_congela_online(monkeypatch):
    """The state of a receiver that answered once and went quiet is a photograph, and a hub
    that kept reporting it would tell DP 144 that a dead equipment is online and DPs 101 and
    121 the power and the level it had before it died.

    This receiver keeps ONE long connection, so a firmware that hung, or a wifi that
    dropped with no FIN, writes nothing and closes nothing; watching the socket alone would
    wait for the keepalive of the kernel, which is hours away.
    """
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        await driver.atualizar()
        assert (driver.estado().online, driver.estado().volume) == (True, 50)
        tcp.respostas.clear()
        await driver.atualizar()
        assert driver.estado().online is True, "one lost poll keeps the last state"
        await driver.atualizar()
        assert driver.conectado() is True, "the socket is up: it is the firmware that is quiet"
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_um_receiver_que_aceita_a_conexao_e_nunca_responde_nunca_fica_online(monkeypatch):
    """A receiver that takes the connection and answers nothing at all was never here, and no
    poll of it ever says online.
    """
    async with _bancada(monkeypatch, {}) as (driver, tcp, _udp):
        await driver.iniciar()
        await _ate(lambda: tcp.conexoes == 1)
        await driver.atualizar()
        await driver.atualizar()
        assert tcp.recebidas[:2] == [_envio(b"PWRQSTN"), _envio(b"MVLQSTN")]
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"


async def test_um_modelo_que_nao_tem_uma_funcao_nao_e_um_receiver_calado(monkeypatch):
    """A receiver answers nothing to a code it does not have, and that is not a fault: what a
    poll measures is whether ANY frame came back, never whether every question was answered.
    """
    # Only the power frame comes back; the four other questions fall into silence, the last
    # one included, which is the one the poll waits for.
    async with _bancada(monkeypatch, _respostas(MVL=b"", AMT=b"", SLI=b"", LMD=b"")) as (
        driver,
        _tcp,
        _udp,
    ):
        await driver.iniciar()
        await driver.atualizar()
        await driver.atualizar()
    estado = driver.estado()
    assert (estado.online, estado.ligado, estado.detalhe) == (True, True, "")


async def test_o_pacote_magico_sai_com_o_mac_do_cadastro_quando_o_socket_esta_caido(monkeypatch):
    """A receiver without Network Standby does not answer IP at all, and the
    magic packet is the only door left; the MAC it needs is the identity, so the
    registration asks for nothing extra.
    """
    async with ServidorDatagrama({}) as acordador:
        monkeypatch.setattr(onkyo, "PORTA_WOL", acordador.endereco[1])
        async with _bancada(monkeypatch) as (driver, tcp, _udp):
            assert driver.conectado() is False
            assert await driver.executar("ligar") is None
            await _ate(lambda: acordador.recebidos != [])
            await _ate(lambda: tcp.recebidas == [_envio(b"PWR01")])
    assert acordador.recebidos == [b"\xff" * 6 + bytes.fromhex(MAC) * 16]


async def test_um_receiver_ja_conectado_nao_leva_pacote_magico(monkeypatch):
    """The packet only exists for a receiver that is not answering, so a healthy one never
    gets a datagram it has no reason to receive.
    """
    async with ServidorDatagrama({}) as acordador:
        monkeypatch.setattr(onkyo, "PORTA_WOL", acordador.endereco[1])
        async with _bancada(monkeypatch) as (driver, tcp, _udp):
            await driver.iniciar()
            await _ate(lambda: tcp.conexoes == 1)
            assert await driver.executar("ligar") is None
        assert acordador.recebidos == []


async def test_um_cadastro_sem_ip_nunca_fala_com_ninguem(monkeypatch):
    """The address is validated as an IP literal, so the hub never becomes a proxy
    into the LAN of the client, and a registration without one dials nobody.
    """
    async with _bancada(monkeypatch, ip="receiver.local") as (driver, tcp, udp):
        assert await driver.executar("ligar") == "eq_offline"
        await driver.iniciar()
        await driver.atualizar()
        await driver.atualizar()
    assert tcp.conexoes == 0
    assert udp.recebidos == []
    assert driver.estado().online is False


@pytest.mark.parametrize("acao", ["temperatura", "vento", "agrupar"])
async def test_acao_fora_das_capacidades_nunca_chega_a_rede(monkeypatch, acao):
    """The driver never implements a method only to refuse, and never dials out."""
    async with _bancada(monkeypatch) as (driver, tcp, _udp):
        await driver.iniciar()
        assert await driver.executar(acao, 20) == "nao_suportado"
    assert tcp.recebidas == []


async def test_todo_codigo_que_o_executar_devolve_e_um_estavel_do_contrato(monkeypatch):
    """Executar answers None or a stable code, and nothing else ever leaves here.

    Asserting that the constants of this module belong to the vocabulary is the module
    confirming itself and proves nothing about what a caller receives, so the codes are read
    off the real answers of the three ways this driver refuses: a value outside the contract,
    an action the manifest does not declare, and an equipment that cannot be dialled.
    """
    async with _bancada(monkeypatch, ip="receiver.local") as (driver, tcp, _udp):
        respostas = {
            await driver.executar("volume", 500),
            await driver.executar("fonte", "SLI 2B"),
            await driver.executar("tecla", "guia"),
            await driver.executar("comando_extra", "PWR;01"),
            await driver.executar("temperatura", 20),
            await driver.executar("ligar"),
            await driver.executar("desligar"),
        }
    assert tcp.recebidas == []
    assert None not in respostas
    assert respostas <= set(CODIGOS)
    assert respostas == {onkyo.INVALID_VALUE, onkyo.EQ_OFFLINE, "nao_suportado"}
    # Erro_aparelho never comes back from executar, because it is raised inside the reader of
    # the long connection; where it is observable is the detail of the state, which the test of
    # the receiver that only answers garbage asserts.
    assert onkyo.ERRO_APARELHO in set(CODIGOS)

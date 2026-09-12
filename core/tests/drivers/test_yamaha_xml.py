# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The Yamaha XML driver against a simulated receiver of the generation before MusicCast."""

from dataclasses import dataclass, field

import pytest

from iphub.drivers.manifesto import validar
from iphub.drivers.nativos import yamaha_xml
from iphub.drivers.nativos.yamaha_xml import YamahaXml
from iphub.drivers.simulado import ServidorHttp

IP = "127.0.0.1"
CAMINHO = "/YamahaRemoteControl/ctrl"


def _basico(
    *,
    energia: str = "On",
    entrada: str = "HDMI1",
    mudo: str = "Off",
    decimos: int = -400,
    programa: str = "Adventure",
    direto: str = "Off",
) -> str:
    return (
        '<YAMAHA_AV rsp="GET" RC="0"><Main_Zone><Basic_Status>'
        f"<Power_Control><Power>{energia}</Power></Power_Control>"
        f"<Input><Input_Sel>{entrada}</Input_Sel></Input>"
        f"<Volume><Lvl><Val>{decimos}</Val><Exp>1</Exp><Unit>dB</Unit></Lvl>"
        f"<Mute>{mudo}</Mute></Volume>"
        "<Surround><Program_Sel><Current>"
        f"<Sound_Program>{programa}</Sound_Program><Straight>{direto}</Straight>"
        "</Current></Program_Sel></Surround>"
        "</Basic_Status></Main_Zone></YAMAHA_AV>"
    )


ENTRADAS = (
    '<YAMAHA_AV rsp="GET" RC="0"><Main_Zone><Input><Input_Sel_Item>'
    "<Input_Sel_Item_Info><Param>HDMI1</Param><Title>Blu-ray</Title></Input_Sel_Item_Info>"
    "<Input_Sel_Item_Info><Param>HDMI2</Param><Title>TV</Title></Input_Sel_Item_Info>"
    "<Input_Sel_Item_Info><Param>NET RADIO</Param><Title>Radio</Title></Input_Sel_Item_Info>"
    "</Input_Sel_Item></Input></Main_Zone></YAMAHA_AV>"
)

FEITO = '<YAMAHA_AV rsp="PUT" RC="0"><Main_Zone></Main_Zone></YAMAHA_AV>'
RECUSADO = '<YAMAHA_AV rsp="PUT" RC="3"><Main_Zone></Main_Zone></YAMAHA_AV>'
SISTEMA = (
    '<YAMAHA_AV rsp="GET" RC="0"><System><Config>'
    "<Model_Name>RX-V679</Model_Name><System_ID>0A1B2C3D</System_ID>"
    "</Config></System></YAMAHA_AV>"
)


@dataclass(frozen=True)
class _Cadastro:
    identidade: str = "receiver-da-sala"
    ip: str = IP
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)
    listas: dict[str, tuple] = field(default_factory=dict)


class _Receiver(ServidorHttp):
    """The receiver answers by the COMMAND inside the request, not by the path: everything
    of this protocol goes to one path."""

    def __init__(self, respostas: list[str]) -> None:
        super().__init__({CAMINHO: (200, "")})
        self.respostas = list(respostas)
        self.corpos: list[str] = []

    async def _atender(self, request):
        from aiohttp import web

        bruto = await request.text()
        self.corpos.append(bruto)
        if "Basic_Status" in bruto:
            resposta = self.respostas[0]
        elif "Input_Sel_Item" in bruto:
            resposta = ENTRADAS
        elif "System" in bruto:
            resposta = SISTEMA
        else:
            resposta = self.respostas[1] if len(self.respostas) > 1 else FEITO
        return web.Response(status=200, text=resposta, content_type="text/xml")


@pytest.fixture
async def receiver(monkeypatch):
    """A receiver already listening, with the driver aimed at the port it took."""
    ligados: list[_Receiver] = []

    async def abrir(respostas=None, **campos):
        servidor = _Receiver(respostas or [_basico()])
        await servidor.iniciar()
        ligados.append(servidor)
        monkeypatch.setattr(yamaha_xml, "PORTA", servidor.endereco[1])
        return servidor, YamahaXml(_Cadastro(campos=campos))

    yield abrir
    for servidor in ligados:
        await servidor.parar()


def test_o_manifesto_e_valido_e_nao_reivindica_o_anuncio_do_musiccast():
    """A receiver that answers both is better served by the MusicCast driver, so the
    announcement of a Yamaha belongs to that one and this is found by the sweep."""
    validar(YamahaXml.MANIFESTO)
    assert YamahaXml.MANIFESTO.descoberta.ssdp_fabricantes == ()
    assert YamahaXml.MANIFESTO.categoria == "receiver"


async def test_um_poll_le_a_zona_inteira_numa_pergunta_so(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    estado = driver.estado()
    assert estado.online is True
    assert estado.ligado is True
    # -40.0 dB of a scale that goes from -80.0 to 16.5 is 41 on the contract.
    assert estado.volume == 41
    assert estado.mudo is False
    assert estado.fonte == "HDMI1"
    assert set(estado.fontes) == {"HDMI1", "HDMI2", "NET RADIO"}
    assert estado.modo == "Adventure"
    await driver.parar()


async def test_o_straight_e_a_ausencia_de_programa_e_nao_um_programa(receiver):
    servidor, driver = await receiver([_basico(direto="On")])
    await driver.atualizar()
    assert driver.estado().modo == "Straight"
    assert await driver.executar("modo", "Straight") is None
    assert "<Straight>On</Straight>" in servidor.corpos[-1]
    assert await driver.executar("modo", "Adventure") is None
    assert "<Sound_Program>Adventure</Sound_Program>" in servidor.corpos[-1]
    await driver.parar()


async def test_o_volume_vai_e_volta_em_decimos_de_decibel(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("volume", 50) is None
    # Half of -80.0 to 16.5 is -31.75 dB, and the receiver only takes half decibel steps.
    assert "<Val>-320</Val>" in servidor.corpos[-1]
    assert "<Unit>dB</Unit>" in servidor.corpos[-1]
    assert driver.estado().volume == 50
    assert await driver.executar("volume", 101) == "invalid_value"
    assert await driver.executar("volume", "alto") == "invalid_value"
    await driver.parar()


async def test_um_teto_de_decibel_do_cadastro_muda_a_conversao(receiver):
    servidor, driver = await receiver(db_maximo="0.0")
    await driver.atualizar()
    # -40.0 dB of a scale that ends at 0.0 is 50 on the contract.
    assert driver.estado().volume == 50
    await driver.parar()


async def test_um_teto_torto_no_cadastro_volta_para_o_da_maioria(receiver):
    servidor, driver = await receiver(db_maximo="alto")
    await driver.atualizar()
    assert driver.estado().volume == 41
    await driver.parar()


async def test_ligar_desligar_e_mudo_mandam_a_palavra_do_protocolo(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("ligar") is None
    assert "<Power>On</Power>" in servidor.corpos[-1]
    assert await driver.executar("desligar") is None
    assert "<Power>Standby</Power>" in servidor.corpos[-1]
    assert await driver.executar("mudo", True) is None
    assert "<Mute>On</Mute>" in servidor.corpos[-1]
    assert await driver.executar("mudo", "sim") == "invalid_value"
    await driver.parar()


async def test_a_entrada_viaja_pela_palavra_do_receiver_ou_pelo_nome_do_dono(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("fonte", "HDMI2") is None
    assert "<Input_Sel>HDMI2</Input_Sel>" in servidor.corpos[-1]
    assert await driver.executar("fonte", "Radio") is None
    assert "<Input_Sel>NET RADIO</Input_Sel>" in servidor.corpos[-1]
    assert await driver.executar("fonte", "nao_existe") == "invalid_value"
    assert await driver.executar("fonte", 3) == "invalid_value"
    await driver.parar()


async def test_uma_cena_do_receiver_e_um_atalho(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert await driver.executar("atalho", "Scene 2") is None
    assert "<Scene_Sel>Scene 2</Scene_Sel>" in servidor.corpos[-1]
    await driver.parar()


@pytest.mark.parametrize("valor", ["<Power>On</Power>", "a&b", 'x"y', "linha\nnova", "", "x" * 65])
async def test_uma_palavra_que_fecharia_o_xml_e_recusada_antes_de_viajar(receiver, valor):
    """The word goes inside XML this driver builds, so what could close an element early is
    refused here instead of being sent as something else."""
    servidor, driver = await receiver()
    await driver.atualizar()
    quantos = len(servidor.corpos)
    assert await driver.executar("atalho", valor) == "invalid_value"
    assert len(servidor.corpos) == quantos
    await driver.parar()


async def test_uma_recusa_do_receiver_e_valor_invalido_e_nao_aparelho_quebrado(receiver):
    servidor, driver = await receiver([_basico(), RECUSADO])
    await driver.atualizar()
    assert await driver.executar("atalho", "Scene 9") == "invalid_value"
    assert driver.estado().online is True
    await driver.parar()


async def test_um_receiver_que_nao_responde_e_offline_depois_de_dois_polls(receiver):
    servidor, driver = await receiver()
    await driver.atualizar()
    assert driver.estado().online is True
    await servidor.parar()
    await driver.atualizar()
    assert driver.estado().online is True
    await driver.atualizar()
    assert driver.estado().online is False
    assert driver.estado().detalhe == "eq_offline"
    await driver.parar()


async def test_a_zona_do_cadastro_e_o_elemento_do_pedido(receiver):
    servidor, driver = await receiver(zona="Zone_2")
    await driver.atualizar()
    assert "<Zone_2>" in servidor.corpos[0]
    await driver.parar()


async def test_uma_zona_que_nao_existe_no_cadastro_vira_a_principal(receiver):
    servidor, driver = await receiver(zona="Cozinha")
    await driver.atualizar()
    assert "<Main_Zone>" in servidor.corpos[0]
    await driver.parar()


async def test_o_identificar_da_o_system_id_que_o_receiver_guarda(receiver):
    servidor, driver = await receiver()
    assert await YamahaXml.identificar(IP) == "0A1B2C3D"
    assert await YamahaXml.identificar("nao-e-um-ip") is None
    await driver.parar()

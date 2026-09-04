# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 6, 8 and 14 under attack: the order of the blocks and the group logic.

Every fact the bench paid for is a test that ATTACKS it here, with a fake multiroom driver
and never a speaker: a play on a slave must never reach the slave, a mixed group must never
be offered, the volume of a slave must never reach the slave, a slave that answers stop must
read as playing, a removal must never move the speaker of block 2 into block 1, and two group
operations must never interleave.

The data point numbers are written by hand in this file. A test that asked the map for them
would agree with any change the map made to the contract of section 8, which is exactly what
a contract test exists to catch.

Seções 6, 8 e 14 sob ataque: a ordem dos blocos e a lógica de grupo.

Todo fato que a bancada pagou é aqui um teste que o ATACA, com um driver multiroom falso e
nunca uma caixa: um play num escravo nunca pode chegar ao escravo, um grupo misto nunca pode
ser oferecido, o volume de um escravo nunca pode chegar ao escravo, um escravo que responde
stop precisa ler como tocando, uma remoção nunca pode mover a caixa do bloco 2 para o bloco 1,
e duas operações de grupo nunca podem se cruzar.

Os números de data point são escritos na mão neste arquivo. Um teste que os pedisse ao mapa
concordaria com qualquer mudança que o mapa fizesse no contrato da seção 8, que é exatamente
o que um teste de contrato existe para pegar.
"""

import asyncio
import json
from dataclasses import dataclass

import pytest

from iphub.config import Cadastro, Config, ConfigIncompativel
from iphub.dpbus import blocos as modulo
from iphub.dpbus.blocos import Blocos
from iphub.drivers.base import Driver
from iphub.drivers.gestor import Gestor
from iphub.drivers.manifesto import Manifesto

TIPO = "multiroom_falso"
OUTRO_TIPO = "multiroom_de_outra_marca"
TIPO_DE_PROJETOR = "projetor_falso"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")

# The numbers of section 8, written by hand on purpose.
# Os números da seção 8, escritos na mão de propósito.
VOLUME_1, PLAY_1, PRESET_1, ONLINE_1, TOCANDO_1, ENTRADA_1 = 101, 102, 103, 104, 105, 141
VOLUME_2, PLAY_2, PRESET_2, ONLINE_2, TOCANDO_2, ENTRADA_2 = 106, 107, 108, 109, 110, 142
VOLUME_3 = 111
CENA = 131
GRUPO = 132
NOMES_BLOCOS = 133
NOMES_GRUPOS = 135

IP_1 = "192.0.2.11"
IP_2 = "192.0.2.12"

MUSICA = "Musica 1 - Artista"


@dataclass(frozen=True)
class _Grupo:
    """What a master answers when it is asked which speakers follow it.

    O que um mestre responde quando lhe perguntam que caixas o seguem.
    """

    escravos: tuple = ()


def _manifesto(tipo: str, categoria: str, capacidades: tuple[str, ...]) -> Manifesto:
    textos = {"descricao": "Caixa de teste"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Caixa", "en": "Speaker"},
        categoria=categoria,
        capacidades=capacidades,
        textos={"pt": dict(textos), "en": dict(textos)},
    )


def _fabrica(
    tipo: str = TIPO,
    *,
    categoria: str = "multiroom",
    capacidades: tuple[str, ...] = CAPACIDADES,
    com_movimentos: bool = True,
    eventos: list[str] | None = None,
) -> type[Driver]:
    """A multiroom driver with knobs, so a test breaks it exactly where it wants to attack.

    Um driver multiroom com botões, para um teste quebrá-lo onde ele quer atacar.
    """
    registro: list[str] = [] if eventos is None else eventos

    class Falsa(Driver):
        MANIFESTO = _manifesto(tipo, categoria, capacidades)
        eventos = registro

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.chamadas: list[tuple[str, object]] = []
            self.marcas: list[bool] = []
            self.espelho: str | None = None
            self.recusa: str | None = None
            self.estoura = False
            self.pausa: asyncio.Event | None = None
            self.fora = False
            self.escravo_alheio = False
            self.grupo = _Grupo()
            self._defina(
                online=True, volume=20, fonte="wifi", fontes=("wifi", "line-in"), tocando=None
            )

        async def executar(self, acao: str, valor: object = None) -> str | None:
            return await self._passo(acao, valor)

        def estado_de_teste(self, **campos: object) -> None:
            self._defina(**campos)

        async def _passo(self, nome: str, valor: object) -> str | None:
            self.chamadas.append((nome, valor))
            registro.append(f"{self.cadastro.identidade}:{nome}:inicio")
            if self.pausa is not None:
                await self.pausa.wait()
            registro.append(f"{self.cadastro.identidade}:{nome}:fim")
            if self.estoura:
                raise RuntimeError("quebrei")
            return self.recusa

        if com_movimentos:

            async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
                return await self._passo("entrar_no_grupo", ip_do_mestre)

            async def desfazer_grupo(self) -> str | None:
                return await self._passo("desfazer_grupo", None)

            async def volume_de_escravo(self, ip: object, valor: object) -> str | None:
                return await self._passo("volume_de_escravo", (ip, valor))

            async def ler_grupo(self) -> _Grupo:
                await self._passo("ler_grupo", None)
                return self.grupo

            def marcar_grupo(self, dentro: bool) -> None:
                self.marcas.append(dentro)
                if not dentro:
                    self.espelho = None
                    self.fora = False
                    self._defina(tocando=None)

            def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
                self.espelho = tocando
                self._defina(tocando=tocando, reproduzindo=reproduzindo)

            def e_escravo(self) -> bool:

                return self.escravo_alheio

            def saiu_do_grupo(self) -> bool:
                return self.fora

    return Falsa


def _cadastro(identidade: str, tipo: str = TIPO, ip: str = IP_1, nome: str = "Sala") -> Cadastro:
    return Cadastro(identidade=identidade, tipo=tipo, nome=nome, ip=ip)


async def _nunca(_segundos: float) -> None:
    """The poll loop parks here: this file attacks the blocks, never the schedule.

    O laço de poll estaciona aqui: este arquivo ataca os blocos, nunca o agendamento.
    """
    await asyncio.Event().wait()


@pytest.fixture
async def monta():
    vivos: list[Gestor] = []

    async def criar(catalogo: dict, cadastros=()) -> Gestor:
        gestor = Gestor(catalogo, cadastros, dormir=_nunca)
        vivos.append(gestor)
        await gestor.iniciar()
        return gestor

    yield criar
    for gestor in vivos:
        await gestor.parar()


@pytest.fixture
async def duas(monta):
    """Two speakers of the same kind in blocks 1 and 2, which is the smallest real group.

    Duas caixas do mesmo tipo nos blocos 1 e 2, que é o menor grupo real.
    """
    classe = _fabrica()
    gestor = await monta(
        {TIPO: classe},
        (_cadastro("uuid-1", ip=IP_1, nome="Sala"), _cadastro("uuid-2", ip=IP_2, nome="Cozinha")),
    )
    return gestor, Blocos(gestor, ("uuid-1", "uuid-2"))


def _caixa(gestor: Gestor, identidade: str):
    return gestor.driver(identidade)


def _chamadas(gestor: Gestor, identidade: str) -> list[tuple[str, object]]:
    return _caixa(gestor, identidade).chamadas


async def test_a_ordem_so_aceita_identidade_ja_cadastrada(duas):
    """Section 6: there is no second registry, so a block names an equipment that exists.

    Seção 6: não existe segundo cadastro, então um bloco nomeia um equipamento que existe.
    """
    _gestor, blocos = duas
    with pytest.raises(modulo.OrdemInvalida) as erro:
        await blocos.definir_ordem(["uuid-1", "uuid-que-ninguem-cadastrou"])
    assert erro.value.codigo == "eq_nao_encontrado"
    assert blocos.ordem == ("uuid-1", "uuid-2")


async def test_qualquer_equipamento_cadastrado_ocupa_um_bloco(monta):
    """Section 6: a block is one of the six equipment numbers of the app, and any registered
    equipment may take one; multiroom is a capability of the equipment, not the ticket in.

    Seção 6: um bloco é um dos seis números de equipamento do app, e qualquer equipamento
    cadastrado pode ocupar um; multiroom é capacidade do equipamento, não o ingresso.
    """
    gestor = await monta(
        {TIPO: _fabrica(), TIPO_DE_PROJETOR: _fabrica(TIPO_DE_PROJETOR, categoria="projetor")},
        (_cadastro("uuid-1"), _cadastro("uuid-projetor", tipo=TIPO_DE_PROJETOR, ip=IP_2)),
    )
    blocos = Blocos(gestor)
    assert await blocos.definir_ordem(["uuid-1", "uuid-projetor"]) == ("uuid-1", "uuid-projetor")
    assert blocos.ordem == ("uuid-1", "uuid-projetor")


async def test_o_dp_de_play_de_um_equipamento_sem_transporte_e_a_chave_de_ligar(monta):
    """Section 8: DP 102 is play/pause for a driver with transport and the power switch for
    any other, read from ligado and written as ligar/desligar; a projector in block 2 has
    DP 107 as its power on the app, and a speaker that cannot tell reports nothing.

    Seção 8: o DP 102 é play/pause para um driver com transporte e a chave de ligar para
    qualquer outro, lido de ligado e escrito como ligar/desligar; um projetor no bloco 2 tem
    o DP 107 como o ligar dele no app, e uma caixa que não sabe dizer não reporta nada.
    """
    projetor = _fabrica(
        TIPO_DE_PROJETOR, categoria="projetor", capacidades=("ligar", "desligar", "fonte")
    )
    gestor = await monta(
        {TIPO: _fabrica(), TIPO_DE_PROJETOR: projetor},
        (_cadastro("uuid-1"), _cadastro("uuid-projetor", tipo=TIPO_DE_PROJETOR, ip=IP_2)),
    )
    blocos = Blocos(gestor, ("uuid-1", "uuid-projetor"))
    _caixa(gestor, "uuid-projetor").estado_de_teste(ligado=False)
    _caixa(gestor, "uuid-1").estado_de_teste(ligado=True, reproduzindo=None)
    valores = blocos.valores()
    assert valores[PLAY_2] is False
    assert PLAY_1 not in valores
    assert await blocos.aplicar(PLAY_2, True) is None
    assert await blocos.aplicar(PLAY_2, False) is None
    assert _chamadas(gestor, "uuid-projetor") == [("ligar", None), ("desligar", None)]
    assert _chamadas(gestor, "uuid-1") == []


async def test_multiroom_sem_a_capacidade_de_agrupar_ocupa_um_bloco(monta):
    """The manifest decides what the equipment can do, never whether it has a number.

    O manifesto decide o que o equipamento faz, nunca se ele tem um número.
    """
    gestor = await monta({TIPO: _fabrica(capacidades=("volume", "tocar"))}, (_cadastro("uuid-1"),))
    assert await Blocos(gestor).definir_ordem(["uuid-1"]) == ("uuid-1",)


async def test_um_setimo_bloco_nao_existe(duas):
    """Section 8 numbers six blocks, and a seventh identity names a block the bus has not.

    A seção 8 numera seis blocos, e uma sétima identidade nomeia um bloco que o barramento
    não tem.
    """
    _gestor, blocos = duas
    with pytest.raises(modulo.OrdemInvalida) as erro:
        await blocos.definir_ordem(["uuid-1", "", "", "", "", "", "uuid-2"])
    assert erro.value.codigo == "blocos_demais"


async def test_a_mesma_caixa_em_dois_blocos_e_recusada(duas):
    """One speaker answering the volume of two blocks is a device that contradicts itself.

    Uma caixa respondendo o volume de dois blocos é um aparelho que se contradiz.
    """
    _gestor, blocos = duas
    with pytest.raises(modulo.OrdemInvalida) as erro:
        await blocos.definir_ordem(["uuid-1", "uuid-1"])
    assert erro.value.codigo == "bloco_repetida"


@pytest.mark.parametrize("ordem", [["uuid-1", 2], "uuid-1", [None], [{"identidade": "uuid-1"}]])
async def test_uma_ordem_que_nao_e_lista_de_texto_e_recusada(duas, ordem):
    _gestor, blocos = duas
    with pytest.raises(modulo.OrdemInvalida) as erro:
        await blocos.definir_ordem(ordem)
    assert erro.value.codigo == "identidade_invalida"


async def test_remover_um_equipamento_esvazia_a_vaga_e_nao_move_ninguem(duas):
    """Section 8: the position IS the block, so a removal must never promote block 2.

    A shift would move the speaker of block 2 into block 1 in every automation the customer
    already built, and nothing on the bus would say it happened.

    Seção 8: a posição É o bloco, então uma remoção nunca pode promover o bloco 2.

    Empurrar moveria a caixa do bloco 2 para o bloco 1 em toda automação que o cliente já
    montou, e nada no barramento diria que isso aconteceu.
    """
    gestor, blocos = duas
    await gestor.remover("uuid-1")
    assert await blocos.esquecer("uuid-1") == ("", "uuid-2")
    assert blocos.identidade(2) == "uuid-2"
    assert blocos.bloco("uuid-2") == 2
    valores = blocos.valores()
    assert VOLUME_1 not in valores and ONLINE_1 not in valores
    assert valores[ONLINE_2] is True and valores[VOLUME_2] == 20


async def test_a_numeracao_do_bloco_e_a_da_secao_8(monta):
    """The whole numbering of section 8 for three blocks, written by hand in the test.

    A numeração inteira da seção 8 para três blocos, escrita na mão no teste.
    """
    gestor = await monta(
        {TIPO: _fabrica()},
        tuple(_cadastro(f"uuid-{n}", ip=f"192.0.2.{n}") for n in (1, 2, 3)),
    )
    blocos = Blocos(gestor, ("uuid-1", "uuid-2", "uuid-3"))
    valores = blocos.valores()
    assert valores[VOLUME_1] == 20 and valores[VOLUME_2] == 20 and valores[VOLUME_3] == 20
    assert valores[ONLINE_1] is True and valores[ONLINE_2] is True
    assert valores[ENTRADA_1] == "wifi" and valores[ENTRADA_2] == "wifi"
    # Section 8: the preset is send only and the chip never echoes a data point it received.
    # Seção 8: o preset é só de envio e o chip nunca ecoa um data point que recebeu.
    assert PRESET_1 not in valores and PRESET_2 not in valores
    assert CENA not in valores


async def test_um_bloco_vazio_nao_publica_data_point_nenhum(monta):
    """A bridge reading a false online would show an empty block as a speaker switched off.

    Uma ponte lendo um online falso mostraria um bloco vazio como caixa desligada.
    """
    gestor = await monta({TIPO: _fabrica()}, (_cadastro("uuid-2", ip=IP_2),))
    valores = Blocos(gestor, ("", "uuid-2")).valores()
    assert not any(dpid in valores for dpid in (VOLUME_1, PLAY_1, ONLINE_1, TOCANDO_1, ENTRADA_1))
    assert valores[ONLINE_2] is True


async def test_uma_identidade_pendurada_e_um_bloco_vazio(duas):
    """The file may be edited by hand, and a dangling identity is an empty block, not a 500.

    O arquivo pode ser editado na mão, e uma identidade pendurada é um bloco vazio, não um 500.
    """
    gestor, blocos = duas
    await gestor.remover("uuid-1")
    valores = blocos.valores()
    assert ONLINE_1 not in valores
    assert await blocos.aplicar(VOLUME_1, 30) == "bloco_offline"


async def test_a_entrada_publicada_e_so_uma_que_a_caixa_declara(duas):
    """Section 14: only the inputs the hardware declares exist, so a mode nobody declared
    is not published as the input of that block.

    Seção 14: só as entradas que o hardware declara existem, então um modo que ninguém
    declarou não é publicado como a entrada daquele bloco.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").estado_de_teste(fonte="hdmi3", fontes=("wifi", "line-in"))
    assert ENTRADA_1 not in blocos.valores()


async def test_o_que_a_bloco_toca_vira_o_play_e_o_tocando(duas):
    """Section 8: DP 102 is the transport and DP 105 is the title, read from the two fields
    of section 6 and never one from the other.

    Seção 8: o DP 102 é o transporte e o DP 105 é o título, lidos dos dois campos da seção 6 e
    nunca um do outro.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").estado_de_teste(reproduzindo=True, tocando=MUSICA)
    _caixa(gestor, "uuid-2").estado_de_teste(reproduzindo=False)
    valores = blocos.valores()
    assert valores[PLAY_1] is True and valores[TOCANDO_1] == MUSICA
    assert valores[PLAY_2] is False and valores[TOCANDO_2] == ""


async def test_a_bloco_que_toca_sem_titulo_ainda_reporta_o_play(duas):
    """Section 8: a speaker playing a line input, or a radio with no metadata, is playing.

    Why: DP 102 used to be read from the title, so a speaker playing over bluetooth, over a
    line input, or a radio that names no track, reported paused while it played, and the app
    sent play to what was already playing.

    Seção 8: uma caixa tocando uma entrada de linha, ou um rádio sem metadado, está tocando.

    Por que: o DP 102 era lido do título, então uma caixa tocando por bluetooth, por entrada
    de linha, ou um rádio que não nomeia faixa, reportava pausada enquanto tocava, e o app
    mandava play no que já estava tocando.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").estado_de_teste(reproduzindo=True, tocando=None)
    valores = blocos.valores()
    assert valores[PLAY_1] is True
    assert valores[TOCANDO_1] == ""


async def test_a_bloco_que_nao_sabe_dizer_nao_reporta_o_play(duas):
    """A data point of section 8 is never reported on a guess: a driver that cannot tell
    whether the transport plays leaves DP 102 out of the snapshot.

    Um data point da seção 8 nunca é reportado por palpite: um driver que não sabe dizer se o
    transporte toca deixa o DP 102 fora do snapshot.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").estado_de_teste(reproduzindo=None, tocando=MUSICA)
    valores = blocos.valores()
    assert PLAY_1 not in valores
    assert valores[TOCANDO_1] == MUSICA


async def test_um_grupo_misto_nunca_e_oferecido(monta):
    """Section 14: a group only exists between speakers of the same domain, so a speaker of
    another kind is never even asked to join.

    Seção 14: um grupo só existe entre caixas do mesmo domínio, então uma caixa de outro tipo
    nunca chega a ser convidada.
    """
    gestor = await monta(
        {TIPO: _fabrica(), OUTRO_TIPO: _fabrica(OUTRO_TIPO)},
        (_cadastro("uuid-1", ip=IP_1), _cadastro("uuid-outra", tipo=OUTRO_TIPO, ip=IP_2)),
    )
    blocos = Blocos(gestor, ("uuid-1", "uuid-outra"))
    assert await blocos.aplicar(GRUPO, "grupo1") == "nao_suportado"
    assert _chamadas(gestor, "uuid-outra") == []
    assert blocos.grupo() == "solo"
    assert blocos.valores()[NOMES_GRUPOS] == json.dumps({"g": ["", ""]}, separators=(",", ":"))


async def test_um_grupo_de_uma_caixa_so_e_recusado(monta):
    """A group of one is not a group, and answering ok would publish one nobody hears.

    Um grupo de um não é grupo, e responder ok publicaria um que ninguém escuta.
    """
    gestor = await monta({TIPO: _fabrica()}, (_cadastro("uuid-1"),))
    blocos = Blocos(gestor, ("uuid-1",))
    assert await blocos.aplicar(GRUPO, "grupo1") == "nao_suportado"
    assert _chamadas(gestor, "uuid-1") == []


async def test_o_grupo_se_forma_nomeando_o_mestre(duas):
    """The slave joins the address of the master, and the master never joins itself.

    O escravo entra no endereço do mestre, e o mestre nunca entra em si mesmo.
    """
    gestor, blocos = duas
    assert await blocos.aplicar(GRUPO, "grupo1") is None
    assert _chamadas(gestor, "uuid-2") == [("entrar_no_grupo", IP_1)]
    assert _chamadas(gestor, "uuid-1") == []
    assert blocos.grupo() == "grupo1"
    assert blocos.escravos() == (2,)
    assert _caixa(gestor, "uuid-1").marcas == [True]
    assert _caixa(gestor, "uuid-2").marcas == [True]
    assert blocos.valores()[GRUPO] == "grupo1"


async def test_o_transporte_de_um_escravo_vai_para_o_mestre(duas):
    """Section 14: a play on a slave DISMANTLES the group, so it never reaches the slave.

    Seção 14: um play num escravo DESMONTA o grupo, então ele nunca chega ao escravo.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    _caixa(gestor, "uuid-2").chamadas.clear()
    assert await blocos.aplicar(PLAY_2, True) is None
    assert await blocos.aplicar(PLAY_2, False) is None
    assert _chamadas(gestor, "uuid-2") == []
    assert _chamadas(gestor, "uuid-1") == [("tocar", None), ("pausar", None)]


async def test_o_preset_de_um_escravo_vai_para_o_mestre(duas):
    """The preset plays a stored URL, which is transport, and transport belongs to the master.

    O preset toca uma URL guardada, que é transporte, e transporte é do mestre.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    _caixa(gestor, "uuid-2").chamadas.clear()
    assert await blocos.aplicar(PRESET_2, "cmd3") is None
    assert _chamadas(gestor, "uuid-2") == []
    assert _chamadas(gestor, "uuid-1") == [("comando_extra", "preset:3")]


async def test_o_volume_de_um_escravo_passa_pelo_mestre(duas):
    """Section 14: the volume of a slave goes through the master, never to the slave.

    Seção 14: o volume de um escravo passa pelo mestre, nunca vai para o escravo.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    _caixa(gestor, "uuid-2").chamadas.clear()
    assert await blocos.aplicar(VOLUME_2, 42) is None
    assert _chamadas(gestor, "uuid-2") == []
    assert _chamadas(gestor, "uuid-1") == [("volume_de_escravo", (IP_2, 42))]
    # The master answers for itself, and its own volume never takes the slave path.
    # O mestre responde por si, e o volume dele nunca pega o caminho de escravo.
    assert await blocos.aplicar(VOLUME_1, 30) is None
    assert ("volume", 30) in _chamadas(gestor, "uuid-1")


async def test_o_escravo_espelha_o_que_o_mestre_toca(duas):
    """Section 14: a slave answers stop even while the group plays, so what the master plays
    is mirrored onto it and the play data point of the slave says playing.

    Seção 14: um escravo responde stop mesmo com o grupo tocando, então o que o mestre toca é
    espelhado nele e o data point de play do escravo diz tocando.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").estado_de_teste(reproduzindo=True, tocando=MUSICA)
    await blocos.aplicar(GRUPO, "grupo1")
    valores = blocos.valores()
    assert _caixa(gestor, "uuid-2").espelho == MUSICA
    assert valores[TOCANDO_2] == MUSICA and valores[PLAY_2] is True


async def test_a_entrada_de_um_escravo_nao_e_desviada_para_o_mestre(duas):
    """The input of a speaker is its own even in a group, and the driver is the one that
    refuses it while grouped, because it is the driver that knows the group breaks.

    A entrada de uma caixa é dela mesmo num grupo, e é o driver que a recusa enquanto
    agrupada, porque é ele que sabe que o grupo quebra.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    _caixa(gestor, "uuid-2").recusa = "nao_suportado"
    assert await blocos.aplicar(ENTRADA_2, "line-in") == "nao_suportado"
    assert _chamadas(gestor, "uuid-2")[-1] == ("fonte", "line-in")
    assert ("fonte", "line-in") not in _chamadas(gestor, "uuid-1")


async def test_desfazer_o_grupo_fala_com_o_mestre_e_solta_todo_mundo(duas):
    """Only the master may dismantle the group it leads.

    Só o mestre pode desfazer o grupo que ele lidera.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    _caixa(gestor, "uuid-1").chamadas.clear()
    assert await blocos.aplicar(GRUPO, "solo") is None
    assert _chamadas(gestor, "uuid-1") == [("desfazer_grupo", None)]
    assert blocos.grupo() == "solo" and blocos.escravos() == ()
    assert _caixa(gestor, "uuid-2").marcas == [True, False]


async def test_trocar_de_mestre_desfaz_o_grupo_anterior(duas):
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    assert await blocos.aplicar(GRUPO, "grupo2") is None
    assert ("desfazer_grupo", None) in _chamadas(gestor, "uuid-1")
    assert _chamadas(gestor, "uuid-1")[-1] == ("entrar_no_grupo", IP_2)
    assert blocos.grupo() == "grupo2" and blocos.escravos() == (1,)


async def test_um_grupo_zumbi_e_saneado_no_boot(duas):
    """Section 14: a group left by a previous run answers commands nobody asked for, so the
    physical group goes down before the hub publishes a state it did not build.

    Seção 14: um grupo deixado por uma execução anterior responde a comandos que ninguém
    pediu, então o grupo físico cai antes de o hub publicar um estado que ele não montou.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").grupo = _Grupo(escravos=({"identidade": "uuid-2"},))
    await blocos.sanear()
    assert _chamadas(gestor, "uuid-1") == [("ler_grupo", None), ("desfazer_grupo", None)]
    assert _chamadas(gestor, "uuid-2") == [("ler_grupo", None)]
    assert blocos.grupo() == "solo"


async def test_uma_caixa_muda_no_boot_nao_derruba_o_saneamento(duas):
    """A speaker that does not answer its group on boot is a warning, never an exception.

    Uma caixa que não responde o grupo dela no boot é um aviso, nunca uma exceção.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").estoura = True
    await blocos.sanear()
    assert blocos.grupo() == "solo"


async def test_o_escravo_que_saiu_do_modo_multiroom_e_reconciliado(duas):
    """Section 14: a slave out of the multiroom mode for two polls lost the group to a reboot
    or to the application of the manufacturer, so our books stop routing through the master.

    Seção 14: um escravo fora do modo multiroom por dois polls perdeu o grupo para um reboot
    ou para o aplicativo do fabricante, então nossos livros param de rotear pelo mestre.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    _caixa(gestor, "uuid-2").fora = True
    _caixa(gestor, "uuid-1").chamadas.clear()
    await blocos.sincronizar()
    assert blocos.escravos() == ()
    assert blocos.grupo() == "solo"
    assert _chamadas(gestor, "uuid-1") == [("desfazer_grupo", None)]
    # The volume of that block goes back to the block itself, and not through the old master.
    # O volume daquele bloco volta para a próprio bloco, e não pelo mestre antigo.
    assert await blocos.aplicar(VOLUME_2, 55) is None
    assert ("volume", 55) in _chamadas(gestor, "uuid-2")


async def test_sincronizar_espelha_o_mestre_a_cada_passagem(duas):
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    _caixa(gestor, "uuid-1").estado_de_teste(tocando=MUSICA)
    await blocos.sincronizar()
    assert _caixa(gestor, "uuid-2").espelho == MUSICA


async def test_o_grupo_cai_quando_o_mestre_sai_da_ordem(duas):
    """A group whose master left the order is not a group, and nothing may keep routing to it.

    Um grupo cujo mestre saiu da ordem não é grupo, e nada pode seguir roteando para ele.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    await blocos.definir_ordem(["", "uuid-2"])
    assert blocos.grupo() == "solo"
    assert _chamadas(gestor, "uuid-2") == [("entrar_no_grupo", IP_1)]


async def test_as_operacoes_de_grupo_sao_serializadas(duas):
    """The bench proved forming, sanitizing and reconciling race, so ONE lock holds them and
    two moves are never on the wire at the same time.

    A bancada provou que formar, sanear e reconciliar correm juntos, então UMA trava os segura
    e dois movimentos nunca estão no fio ao mesmo tempo.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    eventos = type(_caixa(gestor, "uuid-1")).eventos
    eventos.clear()
    porta = asyncio.Event()
    for identidade in ("uuid-1", "uuid-2"):
        _caixa(gestor, identidade).pausa = porta
    trocar = asyncio.create_task(blocos.aplicar(GRUPO, "grupo2"))
    desfazer = asyncio.create_task(blocos.aplicar(GRUPO, "solo"))
    for _ in range(8):
        await asyncio.sleep(0)
    # Both are alive and only ONE of them opened a move: the other is waiting on the lock.
    # Os dois estão vivos e só UM deles abriu um movimento: o outro espera na trava.
    assert eventos == ["uuid-1:desfazer_grupo:inicio"]
    porta.set()
    assert await trocar is None
    assert await desfazer is None
    assert all(
        anterior.endswith("inicio") and seguinte.endswith("fim")
        for anterior, seguinte in zip(eventos[::2], eventos[1::2], strict=True)
    )
    assert blocos.grupo() == "solo"


async def test_set_em_bloco_vazio_e_recusado(monta):
    gestor = await monta({TIPO: _fabrica()}, ())
    blocos = Blocos(gestor)
    assert await blocos.aplicar(VOLUME_1, 30) == "bloco_offline"
    assert await blocos.aplicar(GRUPO, "grupo1") == "bloco_offline"


@pytest.mark.parametrize("dpid", [ONLINE_1, TOCANDO_1, NOMES_BLOCOS, NOMES_GRUPOS])
async def test_set_em_data_point_de_reporte_e_recusado(duas, dpid):
    """Section 8: the chip never echoes, so a report only data point takes no set at all.

    Seção 8: o chip nunca ecoa, então um data point só de reporte não aceita set algum.
    """
    _gestor, blocos = duas
    assert await blocos.aplicar(dpid, True) == "dp_somente_leitura"


@pytest.mark.parametrize("dpid", [CENA, 999, "101", 101.0, True, None])
async def test_um_data_point_que_nao_e_deste_modulo_e_recusado(duas, dpid):
    """DP 131 is the scene and belongs to the module that owns the scenes.

    O DP 131 é a cena e é do módulo dono das cenas.
    """
    _gestor, blocos = duas
    assert await blocos.aplicar(dpid, "cena1") == "dp_desconhecido"


@pytest.mark.parametrize(
    ("dpid", "valor"),
    [
        (VOLUME_1, 101),
        (VOLUME_1, -1),
        (VOLUME_1, True),
        (VOLUME_1, "30"),
        (PLAY_1, "sim"),
        (PLAY_1, 1),
        (PRESET_1, "cmd9"),
        (ENTRADA_1, "hdmi3"),
        (ENTRADA_1, 1),
        (GRUPO, "grupo0"),
        (GRUPO, "banda"),
    ],
)
async def test_um_valor_fora_do_tipo_do_dp_nunca_chega_a_caixa(duas, dpid, valor):
    """Section 8 fixes the type of every data point, and nothing wider reaches a speaker.

    A seção 8 fixa o tipo de todo data point, e nada mais largo chega a uma caixa.
    """
    gestor, blocos = duas
    assert await blocos.aplicar(dpid, valor) == "valor_invalido"
    assert _chamadas(gestor, "uuid-1") == []


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        ("eq_offline", "bloco_offline"),
        ("invalid_value", "valor_invalido"),
        ("nao_suportado", "nao_suportado"),
        ("erro_aparelho", "erro_aparelho"),
    ],
)
async def test_o_codigo_do_driver_vira_o_vocabulario_do_barramento(duas, resposta, esperado):
    """Section 11: the bus answers a stable code the bridge already knows, never a new one.

    Seção 11: o barramento responde um código estável que a ponte já conhece, nunca um novo.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-1").recusa = resposta
    codigo = await blocos.aplicar(VOLUME_1, 30)
    assert codigo == esperado and codigo in modulo.CODIGOS


async def test_um_movimento_de_grupo_com_codigo_proprio_vira_erro_aparelho(duas):
    """A driver that invents a code would reach the bridge as a word nobody translates.

    Um driver que inventa um código chegaria à ponte como uma palavra que ninguém traduz.
    """
    gestor, blocos = duas
    _caixa(gestor, "uuid-2").recusa = "codigo_que_ninguem_traduz"
    assert await blocos.aplicar(GRUPO, "grupo1") == "erro_aparelho"
    assert blocos.grupo() == "solo"


async def test_nenhuma_excecao_escapa_do_aplicar(duas):
    """A speaker that raises is a stable code, never an exception out of the bus.

    Uma caixa que estoura é um código estável, nunca uma exceção saindo do barramento.
    """
    gestor, blocos = duas
    for identidade in ("uuid-1", "uuid-2"):
        _caixa(gestor, identidade).estoura = True
    assert await blocos.aplicar(VOLUME_1, 30) == "erro_aparelho"
    assert await blocos.aplicar(GRUPO, "grupo1") == "erro_aparelho"


async def test_um_driver_multiroom_sem_os_movimentos_de_grupo_nao_derruba_o_barramento(monta):
    """A driver that declares agrupar and offers no move is refused, never crashed into.

    Um driver que declara agrupar e não oferece movimento é recusado, nunca quebrado.
    """
    gestor = await monta(
        {TIPO: _fabrica(com_movimentos=False)},
        (_cadastro("uuid-1", ip=IP_1), _cadastro("uuid-2", ip=IP_2)),
    )
    blocos = Blocos(gestor, ("uuid-1", "uuid-2"))
    assert await blocos.aplicar(GRUPO, "grupo1") == "bloco_offline"
    await blocos.sanear()


async def test_nomes_longos_nao_tiram_os_nomes_das_blocos_do_barramento(monta):
    """Section 8: a string data point carries 255 BYTES, and the registration takes a name of
    any length, so six long names still reach the bridge as one JSON it can read.

    Seção 8: um data point string leva 255 BYTES, e o cadastro aceita nome de qualquer
    tamanho, então seis nomes longos ainda chegam à ponte como um JSON que ela consegue ler.
    """
    gestor = await monta(
        {TIPO: _fabrica()},
        tuple(
            _cadastro(f"uuid-{n}", ip=f"192.0.2.{n}", nome="Sala de estar aberta " * 10)
            for n in range(1, 7)
        ),
    )
    blocos = Blocos(gestor, tuple(f"uuid-{n}" for n in range(1, 7)))
    valores = blocos.valores()
    for dpid in (NOMES_BLOCOS, NOMES_GRUPOS):
        texto = valores[dpid]
        assert len(texto.encode("utf-8")) <= 255
        assert len(json.loads(texto)[{NOMES_BLOCOS: "z", NOMES_GRUPOS: "g"}[dpid]]) == 6


@pytest.mark.parametrize("caractere", ['"', "\\"])
async def test_um_nome_que_o_json_escapa_nao_tira_os_nomes_do_barramento(monta, caractere):
    """Section 8: a name of quotes or backslashes is ordinary input and must not take the
    names of all six blocks off the bus.

    Why: json escapes a quote and a backslash, so a budget measured in raw bytes lied for
    these names, the shortened list overflowed again, and the data point was dropped whole.

    Seção 8: um nome de aspas ou de barras é entrada comum e não pode tirar do barramento os
    nomes das seis blocos.

    Por que: o json escapa aspa e barra, então um orçamento medido em bytes crus mentia para
    estes nomes, a lista encurtada estourava de novo, e o data point sumia inteiro.
    """
    gestor = await monta(
        {TIPO: _fabrica()},
        tuple(_cadastro(f"uuid-{n}", ip=f"192.0.2.{n}", nome=caractere * 40) for n in range(1, 7)),
    )
    valores = Blocos(gestor, tuple(f"uuid-{n}" for n in range(1, 7))).valores()
    for dpid, chave in ((NOMES_BLOCOS, "z"), (NOMES_GRUPOS, "g")):
        texto = valores[dpid]
        assert len(texto.encode("utf-8")) <= 255
        assert len(json.loads(texto)[chave]) == 6


async def test_um_nome_acentuado_nunca_e_cortado_no_meio_de_um_caractere(monta):
    """A JSON cut inside a character reaches the bridge unparseable, which is worse than a
    name the customer sees shortened.

    Um JSON cortado dentro de um caractere chega à ponte impossível de ler, que é pior que um
    nome que o cliente vê encurtado.
    """
    gestor = await monta(
        {TIPO: _fabrica()},
        tuple(_cadastro(f"uuid-{n}", ip=f"192.0.2.{n}", nome="Área " * 40) for n in range(1, 7)),
    )
    texto = Blocos(gestor, tuple(f"uuid-{n}" for n in range(1, 7))).valores()[NOMES_BLOCOS]
    assert len(texto.encode("utf-8")) <= 255
    assert all(nome.startswith("Área") for nome in json.loads(texto)["z"])


async def test_os_nomes_das_blocos_seguem_a_ordem(duas):
    _gestor, blocos = duas
    assert blocos.valores()[NOMES_BLOCOS] == json.dumps(
        {"z": ["Sala", "Cozinha"]}, ensure_ascii=False, separators=(",", ":")
    )


def test_a_config_carrega_as_blocos_e_recusa_o_que_a_secao_8_nao_numera(tmp_path):
    """The order on disk is the same order the bus reads, and a seventh block is refused.

    A ordem em disco é a mesma que o barramento lê, e um sétimo bloco é recusado.
    """
    from iphub import config

    dir_data = tmp_path / "data"
    dir_data.mkdir()
    config.salvar(Config(blocos=("uuid-1", "", "uuid-2")), dir_data)
    assert config.carregar(dir_data).blocos == ("uuid-1", "", "uuid-2")
    caminho = dir_data / config.ARQUIVO
    for ruim in ([f"uuid-{n}" for n in range(7)], ["uuid-1", "uuid-1"], ["uuid-1", 2], "uuid-1"):
        dados = json.loads(caminho.read_text(encoding="utf-8"))
        dados["blocos"] = ruim
        caminho.write_text(json.dumps(dados), encoding="utf-8")
        with pytest.raises(ConfigIncompativel):
            config.carregar(dir_data)


def test_blocos_vazias_sao_o_padrao_de_um_hub_sem_caixa():
    """Section 6: the hub works with zero equipment registered, and no assistant needs one.

    Seção 6: o hub funciona com zero equipamentos cadastrados, e nenhum assistente exige um.
    """
    assert Config().blocos == ()


def test_esvaziar_uma_vaga_nunca_encurta_a_ordem():
    assert modulo.sem(("a", "b", "c"), "b") == ("a", "", "c")
    assert modulo.sem(("a", "b"), "z") == ("a", "b")


@pytest.mark.parametrize(("bloco", "valor"), [(0, "solo"), (1, "grupo1"), (6, "grupo6")])
def test_o_grupo_da_bloco_e_o_nome_no_fio(bloco, valor):
    assert modulo.valor_do_grupo(bloco) == valor
    assert modulo.bloco_do_grupo(valor) == bloco


@pytest.mark.parametrize("valor", ["grupo0", "grupo7", "grupo", "", None, 1, "SOLO"])
def test_um_grupo_que_a_secao_8_nao_nomeia_nao_e_bloco(valor):
    assert modulo.bloco_do_grupo(valor) is None


async def test_sanear_pergunta_as_seis_caixas_juntas_e_nao_uma_apos_a_outra(monta):
    """Section 14: /health answers in about 7 s on the reference appliance, and sanear runs
    before the listening socket opens.

    Why: one deadline per speaker meant a site whose boxes are unreachable, which is a VLAN
    change or a router reboot, had no panel for half a minute, and that is exactly when the
    operator needs it. Six mute speakers must cost the slowest one, not the sum.

    Seção 14: o /health responde em uns 7 s no appliance de referência, e o sanear roda antes
    de o socket de escuta abrir.

    Por que: um prazo por caixa fazia um site com as caixas inalcançáveis, que é troca de VLAN
    ou reboot de roteador, ficar sem painel por meio minuto, e é justamente quando o operador
    precisa dele. Seis caixas mudas precisam custar a mais lenta, não a soma.
    """

    class Muda(_fabrica()):
        async def ler_grupo(self):
            await asyncio.Event().wait()

    gestor = await monta(
        {TIPO: Muda},
        tuple(_cadastro(f"uuid-{n}", ip=f"192.0.2.{n}") for n in range(1, 7)),
    )
    blocos = Blocos(gestor, tuple(f"uuid-{n}" for n in range(1, 7)), limite_s=0.2)
    laco = asyncio.get_running_loop()
    comeco = laco.time()
    await blocos.sanear()
    gasto = laco.time() - comeco
    # Six deadlines end to end would be 1.2 s; one deadline plus slack is the whole budget.
    # Seis prazos em fila dariam 1,2 s; um prazo mais folga é o orçamento inteiro.
    assert gasto < 0.6, f"sanear took {gasto:.2f}s, which is one deadline per speaker"


async def test_uma_caixa_escrava_de_grupo_alheio_e_reconhecida_e_nao_lida_como_solo(duas):
    """Section 14: the customer can group a speaker with the app of the manufacturer, and a
    lost reply to a join or a restart with a group up reach the same state.

    Why: a speaker in multiroom slave mode refuses volume, transport, preset and input, and
    nothing here put it there. Reading it as solo left the block dead on the bus with the panel
    drawing controls that only answer no, and nothing anywhere saying why.

    Seção 14: o cliente pode agrupar uma caixa com o app do fabricante, e uma resposta perdida
    a um convite ou um reinício com grupo de pé chegam ao mesmo estado.

    Por que: uma caixa em modo escravo de multiroom recusa volume, transporte, preset e
    entrada, e nada aqui a pôs lá. Lê-la como solo deixava o bloco morto no barramento com o
    painel desenhando controles que só respondem não, sem nada dizendo por quê.
    """
    gestor, blocos = duas
    assert blocos.escravos_alheios() == ()
    _caixa(gestor, "uuid-1").escravo_alheio = True
    assert blocos.escravos_alheios() == (1,)
    # A speaker the hub itself put in the group is not an alien slave: it is ours.
    # Uma caixa que o próprio hub pôs no grupo não é escrava alheia: ela é nossa.
    _caixa(gestor, "uuid-2").escravo_alheio = True
    await blocos.aplicar(GRUPO, "grupo1")
    assert 2 not in blocos.escravos_alheios()


async def test_remover_o_mestre_manda_o_ungroup_antes_de_reescrever_a_ordem(duas):
    """Section 14: only the master dismantles the group, and _multiroom reads the order to
    find it, so the order may not be rewritten first.

    Why: writing the new order first made the master unreachable, so multiroom:Ungroup never
    reached the wire: the speakers stayed physically grouped forever, playing together, while
    the hub published solo and the customer had no way to separate them.

    Seção 14: só o mestre desfaz o grupo, e o _multiroom lê a ordem para achá-lo, então a
    ordem não pode ser reescrita antes.

    Por que: gravar a ordem nova primeiro deixava o mestre inalcançável, então o
    multiroom:Ungroup nunca chegava ao fio: as caixas ficavam fisicamente agrupadas para
    sempre, tocando juntas, enquanto o hub publicava solo e o cliente não tinha como separá-las.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    mestre = _caixa(gestor, "uuid-1")
    mestre.chamadas.clear()
    await blocos.esquecer("uuid-1")
    assert ("desfazer_grupo", None) in mestre.chamadas, mestre.chamadas
    assert blocos.grupo() == "solo"


async def test_um_ungroup_recusado_nao_esquece_o_grupo_e_a_repeticao_tenta_de_novo(duas):
    """A refused dismantle must leave the group in the books, or the retry is a silent no-op.

    Why: forgetting it tells the customer the speakers are apart while they are still playing
    together, and the second attempt then finds no group and answers ok without touching the
    wire.

    Um desfazer recusado precisa deixar o grupo nos livros, senão a repetição é um nada
    silencioso.

    Por que: esquecê-lo diz ao cliente que as caixas estão separadas enquanto elas seguem
    tocando juntas, e a segunda tentativa não acha grupo e responde ok sem tocar no fio.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    mestre = _caixa(gestor, "uuid-1")
    mestre.recusa = "eq_offline"
    assert await blocos.aplicar(GRUPO, "solo") == "bloco_offline"
    assert blocos.grupo() == "grupo1", "the books forgot a group that is still physically up"
    mestre.recusa = None
    mestre.chamadas.clear()
    assert await blocos.aplicar(GRUPO, "solo") is None
    assert ("desfazer_grupo", None) in mestre.chamadas
    assert blocos.grupo() == "solo"


async def test_um_escravo_largado_dos_livros_perde_a_marca_de_grupo(duas):
    """A block dropped from the books while its driver still believes it is grouped refuses
    transport and input forever, for a group nobody is in any more.

    Um bloco largado dos livros com o driver dele ainda achando que está agrupado recusa
    transporte e entrada para sempre, por um grupo em que ninguém mais está.
    """
    gestor, blocos = duas
    await blocos.aplicar(GRUPO, "grupo1")
    escravo = _caixa(gestor, "uuid-2")
    assert escravo.marcas == [True]
    await blocos.esquecer("uuid-2")
    assert escravo.marcas == [True, False], escravo.marcas


async def test_a_caixa_presa_em_grupo_alheio_e_convidada_a_sair_no_boot_e_no_sincronismo(duas):
    """A speaker held in a group this hub does not lead refuses volume, transport, preset and
    input, so leaving it there is leaving the block dead on the bus.

    Why: sanear only reconciled from the master outwards and sincronizar returned at once
    whenever our books said solo, which is exactly the case, so nothing ever asked the speaker
    to leave and every command of that block failed forever.

    Uma caixa presa num grupo que este hub não lidera recusa volume, transporte, preset e
    entrada, então deixá-la ali é deixar o bloco morto no barramento.

    Por que: o sanear só reconciliava do mestre para fora e o sincronizar voltava na hora
    sempre que os livros diziam solo, que é justamente o caso, então nada nunca pedia à caixa
    que saísse e todo comando daquele bloco falhava para sempre.
    """
    gestor, blocos = duas
    caixa = _caixa(gestor, "uuid-1")
    caixa.escravo_alheio = True
    caixa.chamadas.clear()
    await blocos.sanear()
    assert ("desfazer_grupo", None) in caixa.chamadas, caixa.chamadas
    caixa.chamadas.clear()
    await blocos.sincronizar()
    assert ("desfazer_grupo", None) in caixa.chamadas, caixa.chamadas

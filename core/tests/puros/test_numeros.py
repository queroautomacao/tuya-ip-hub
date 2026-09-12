# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Sections 6, 8 and 14 under attack: the numbers of a licence, the data points they publish
and the group logic of a licence of audio and video.

Every fact the bench paid for is a test that ATTACKS it here, with a fake multiroom driver
and never a speaker: a play on a slave must never reach the slave, a mixed group must never
be offered, the volume of a slave must never reach the slave, a slave that answers stop must
read what the master plays, a removal must never move the equipment of number 2 into number
1, and two group operations must never interleave.

The gestor is a double too: it holds the registrations, answers the states and the manifests,
applies the capability gate and records the poll out of turn, so a test reads
exactly what reached which equipment and nothing else runs.

The data point numbers are written by hand in this file. A test that asked the map for them
would agree with any change the map made to the contract, which is exactly what
a contract test exists to catch.
"""

import asyncio
import json
from dataclasses import dataclass

import pytest

from iphub.config import Cadastro, Item, Licenca
from iphub.dpbus import mapa
from iphub.dpbus import numeros as modulo
from iphub.dpbus.numeros import Licencas, Numeros, OrdemInvalida, sem, traduzir
from iphub.drivers.base import NAO_SUPORTADO, Driver
from iphub.drivers.gestor import EQ_NAO_ENCONTRADO, EQ_OFFLINE, ERRO_APARELHO
from iphub.drivers.manifesto import MODOS_AR, VENTOS, Estado, Manifesto

TIPO = "multiroom_falso"
OUTRO_TIPO = "multiroom_de_outra_marca"
TIPO_DE_PROJETOR = "projetor_falso"
TIPO_DE_TV = "tv_falsa"
TIPO_DE_RECEIVER = "receiver_falso"
TIPO_DE_AR = "ar_falso"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")
CAPACIDADES_DE_PROJETOR = ("ligar", "desligar", "fonte")
CAPACIDADES_DE_TV = ("ligar", "desligar", "tecla")
CAPACIDADES_DE_RECEIVER = ("ligar", "desligar", "volume", "mudo", "fonte", "atalho", "modo")
CAPACIDADES_DE_AR = ("ligar", "desligar", "temperatura", "modo", "vento")
TECLAS_DA_TV = ("canal_mais", "canal_menos", "ok")

LICENCA_AV = Licenca(id="av1", produto="av")
OUTRA_LICENCA_AV = Licenca(id="av2", produto="av")
LICENCA_AR = Licenca(id="ar1", produto="ar")

# The numbers for the product of audio and video, written by hand on purpose.
LIGADO_1, LIGADO_2, LIGADO_3 = 101, 102, 103
NIVEL_1, NIVEL_2, NIVEL_3 = 121, 122, 123
CENA = 141
GRUPO = 142
COMANDO = 143
ONLINE = 144
MUDOS = 145
ENTRADAS = 146
MODOS = 147
TITULOS = 148
PERFIS_1, PERFIS_2, PERFIS_3, PERFIS_4, PERFIS_5 = 149, 150, 151, 152, 153
NOMES_CENAS_1, NOMES_CENAS_2 = 154, 155

# The numbers for the product of air: machine k starts at 101 + 5(k - 1).
AR_LIGADO_1, AR_TEMPERATURA_1, AR_MODO_1, AR_VENTO_1 = 101, 102, 103, 104
AR_LIGADO_2, AR_TEMPERATURA_2, AR_MODO_2, AR_VENTO_2 = 106, 107, 108, 109
AR_LIGADO_8, AR_TEMPERATURA_8, AR_MODO_8, AR_VENTO_8 = 136, 137, 138, 139
AR_CENA = 171
AR_ONLINE = 172
AR_NOMES = 173
AR_NOMES_CENAS_1, AR_NOMES_CENAS_2 = 174, 175

IP_1 = "192.0.2.11"
IP_2 = "192.0.2.12"
IP_3 = "192.0.2.13"

MUSICA = "Musica 1 - Artista"

ENTRADAS_DA_CAIXA = (Item("Wi-Fi", "wifi"), Item("Linha", "line-in"))
LISTAS_DO_RECEIVER = {
    "entradas": (Item("HDMI 1", "hdmi1"), Item("HDMI 2", "hdmi2")),
    "atalhos": (Item("Netflix", "app:netflix"),),
    "modos": (Item("Estereo", "stereo"), Item("Filme", "movie")),
}

# A registration this heavy makes one profile of about 200 bytes, the most
# allows one to weigh, which is what attacks the packing of the five strings.
NOME_PESADO = "Sala de estar grande"
ENTRADAS_PESADAS = tuple(Item(f"Entrada {k:02d} longa", f"in{k}") for k in range(1, 11))
ATALHOS_PESADOS = tuple(Item(f"Atalho {k:02d} longo ", f"at{k}") for k in range(1, 9))
MODOS_PESADOS = tuple(Item(f"Modo {k:02d} comprido", f"md{k}") for k in range(1, 9))


@dataclass(frozen=True)
class _Grupo:
    """What a master answers when it is asked which speakers follow it."""

    escravos: tuple = ()


def _manifesto(
    tipo: str,
    categoria: str,
    capacidades: tuple[str, ...],
    teclas: tuple[str, ...] = (),
    modos: tuple[str, ...] = (),
    ventos: tuple[str, ...] = (),
) -> Manifesto:
    textos = {"descricao": "Equipamento de teste"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Equipamento", "en": "Equipment"},
        categoria=categoria,
        capacidades=capacidades,
        textos={"pt": dict(textos), "en": dict(textos)},
        teclas=teclas,
        modos=modos,
        ventos=ventos,
    )


def _fabrica(
    tipo: str = TIPO,
    *,
    categoria: str = "multiroom",
    capacidades: tuple[str, ...] = CAPACIDADES,
    teclas: tuple[str, ...] = (),
    modos: tuple[str, ...] = (),
    ventos: tuple[str, ...] = (),
    com_movimentos: bool = True,
    eventos: list[str] | None = None,
) -> type[Driver]:
    """A driver with knobs, so a test breaks it exactly where it wants to attack."""
    registro: list[str] = [] if eventos is None else eventos

    class Falsa(Driver):
        MANIFESTO = _manifesto(tipo, categoria, capacidades, teclas, modos, ventos)
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
            self.visitas = 0
            self._defina(
                online=True, volume=20, fonte="wifi", fontes=("wifi", "line-in"), tocando=None
            )

        async def atualizar(self) -> None:
            self.visitas += 1

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

            async def tirar_do_grupo(self, ip_do_escravo: object) -> str | None:
                return await self._passo("tirar_do_grupo", ip_do_escravo)

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


def _projetor(capacidades: tuple[str, ...] = CAPACIDADES_DE_PROJETOR) -> type[Driver]:
    return _fabrica(TIPO_DE_PROJETOR, categoria="projetor", capacidades=capacidades)


def _tv() -> type[Driver]:
    return _fabrica(TIPO_DE_TV, categoria="tv", capacidades=CAPACIDADES_DE_TV, teclas=TECLAS_DA_TV)


def _receiver() -> type[Driver]:
    return _fabrica(TIPO_DE_RECEIVER, categoria="receiver", capacidades=CAPACIDADES_DE_RECEIVER)


def _ar() -> type[Driver]:
    return _fabrica(
        TIPO_DE_AR,
        categoria="ar_condicionado",
        capacidades=CAPACIDADES_DE_AR,
        modos=MODOS_AR,
        ventos=VENTOS,
    )


def _cadastro(
    identidade: str,
    tipo: str = TIPO,
    ip: str = IP_1,
    nome: str = "Sala",
    listas: dict[str, tuple[Item, ...]] | None = None,
) -> Cadastro:
    if listas is None:
        listas = {"entradas": ENTRADAS_DA_CAIXA}
    return Cadastro(identidade=identidade, tipo=tipo, nome=nome, ip=ip, listas=dict(listas))


def _pesado(identidade: str, ip: str = IP_1) -> Cadastro:
    return _cadastro(identidade, ip=ip, nome=NOME_PESADO, listas={"entradas": ENTRADAS_PESADAS})


class GestorFalso:
    """The gestor as the numbers module sees it: registrations, states, drivers, manifests,
    the capability gate and the poll out of turn; no task and no socket.
    """

    def __init__(self, catalogo: dict[str, type[Driver]], cadastros: tuple[Cadastro, ...] = ()):
        self._catalogo = dict(catalogo)
        self._cadastros: dict[str, Cadastro] = {}
        self._drivers: dict[str, Driver] = {}
        self.visitas: list[str] = []
        for cadastro in cadastros:
            self._cadastros[cadastro.identidade] = cadastro
            classe = self._catalogo.get(cadastro.tipo)
            if classe is not None:
                self._drivers[cadastro.identidade] = classe(cadastro)

    @property
    def cadastros(self) -> tuple[Cadastro, ...]:
        return tuple(self._cadastros.values())

    def estados(self) -> dict[str, Estado]:
        return {
            identidade: (
                self._drivers[identidade].estado()
                if identidade in self._drivers
                else Estado(online=False)
            )
            for identidade in self._cadastros
        }

    def driver(self, identidade: str) -> Driver | None:
        return self._drivers.get(identidade)

    def manifesto(self, identidade: str) -> Manifesto | None:
        cadastro = self._cadastros.get(identidade)
        return None if cadastro is None else self.manifesto_de_tipo(cadastro.tipo)

    def manifesto_de_tipo(self, tipo: str) -> Manifesto | None:
        classe = self._catalogo.get(tipo)
        return None if classe is None else classe.MANIFESTO

    async def executar(self, identidade: str, acao: str, valor: object = None) -> str | None:
        if identidade not in self._cadastros:
            return EQ_NAO_ENCONTRADO
        manifesto = self.manifesto(identidade)
        # What the manifest does not declare is refused before the driver.
        if manifesto is None or acao not in manifesto.capacidades:
            return NAO_SUPORTADO
        driver = self._drivers.get(identidade)
        if driver is None:
            return EQ_OFFLINE
        try:
            return await driver.executar(acao, valor)
        except Exception:
            return ERRO_APARELHO

    async def visitar_e_esperar(self, identidade: str) -> None:
        self.visitas.append(identidade)
        driver = self._drivers.get(identidade)
        if driver is not None:
            await driver.atualizar()

    def remover(self, identidade: str) -> None:
        """The registration is gone, the way a hand edited config.json leaves it."""
        self._cadastros.pop(identidade, None)
        self._drivers.pop(identidade, None)

    def desmontar(self, identidade: str) -> None:
        """The registration stays and the driver is gone, the way a driver that failed to
        build leaves it.
        """
        self._drivers.pop(identidade, None)


@pytest.fixture
def duas():
    """Two speakers of the same kind in numbers 1 and 2, which is the smallest real group."""
    gestor = GestorFalso(
        {TIPO: _fabrica()},
        (_cadastro("uuid-1", ip=IP_1, nome="Sala"), _cadastro("uuid-2", ip=IP_2, nome="Cozinha")),
    )
    return gestor, Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-2"))


@pytest.fixture
def tres():
    """Three speakers of the same kind, for a group with more than one slave."""
    gestor = GestorFalso(
        {TIPO: _fabrica()},
        (
            _cadastro("uuid-1", ip=IP_1, nome="Sala"),
            _cadastro("uuid-2", ip=IP_2, nome="Cozinha"),
            _cadastro("uuid-3", ip=IP_3, nome="Quarto"),
        ),
    )
    return gestor, Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-2", "uuid-3"))


@pytest.fixture
def ares():
    """Two air conditioners in the machines 1 and 2 of a licence of air."""
    gestor = GestorFalso(
        {TIPO_DE_AR: _ar()},
        (
            _cadastro("ar-1", tipo=TIPO_DE_AR, ip=IP_1, nome="Sala"),
            _cadastro("ar-2", tipo=TIPO_DE_AR, ip=IP_2, nome="Quarto"),
        ),
    )
    for identidade in ("ar-1", "ar-2"):
        _caixa(gestor, identidade).estado_de_teste(
            ligado=True, temperatura=22, modo="frio", vento="alto"
        )
    return gestor, Numeros(gestor, LICENCA_AR, ("ar-1", "ar-2"))


@pytest.fixture
def misto():
    """A speaker, a projector, a TV, a receiver and an air conditioner, all registered."""
    gestor = GestorFalso(
        {
            TIPO: _fabrica(),
            TIPO_DE_PROJETOR: _projetor(),
            TIPO_DE_TV: _tv(),
            TIPO_DE_RECEIVER: _receiver(),
            TIPO_DE_AR: _ar(),
        },
        (
            _cadastro("uuid-1", ip=IP_1, nome="Sala"),
            _cadastro("uuid-p", tipo=TIPO_DE_PROJETOR, ip=IP_2, nome="Projetor"),
            _cadastro("uuid-tv", tipo=TIPO_DE_TV, ip=IP_3, nome="TV"),
            _cadastro(
                "uuid-r",
                tipo=TIPO_DE_RECEIVER,
                ip="192.0.2.14",
                nome="Receiver",
                listas=LISTAS_DO_RECEIVER,
            ),
            _cadastro("ar-1", tipo=TIPO_DE_AR, ip="192.0.2.15", nome="Ar"),
        ),
    )
    return gestor


def _caixa(gestor: GestorFalso, identidade: str):
    return gestor.driver(identidade)


def _chamadas(gestor: GestorFalso, identidade: str) -> list[tuple[str, object]]:
    return _caixa(gestor, identidade).chamadas


async def _livro(
    gestor: GestorFalso,
    licencas: tuple[Licenca, ...],
    numeros: dict[str, tuple[str, ...]] | None = None,
) -> Licencas:
    """The book built licence by licence through the door the routes take, adicionar and
    definir_ordem, for the tests of the operations of the book; the boot from config.json
    has tests of its own.
    """
    livro = Licencas(gestor)
    for licenca in licencas:
        livro.adicionar(licenca)
    for id_licenca, ordem in (numeros or {}).items():
        await livro.definir_ordem(id_licenca, list(ordem))
    return livro


# The order of the numbers and the codes it refuses with.


async def test_a_ordem_so_aceita_identidade_ja_cadastrada(duas):
    """There is no second registry, so a number names an equipment that exists."""
    _gestor, numeros = duas
    with pytest.raises(OrdemInvalida) as erro:
        await numeros.definir_ordem(["uuid-1", "uuid-que-ninguem-cadastrou"])
    assert erro.value.codigo == "eq_nao_encontrado"
    assert numeros.ordem == ("uuid-1", "uuid-2")


async def test_qualquer_equipamento_de_av_ocupa_um_numero(misto):
    """A number is one of the twelve equipment numbers of the app, and any
    registered equipment of the product may take one; multiroom is a capability, not the
    ticket in.
    """
    numeros = Numeros(misto, LICENCA_AV)
    ordem = ["uuid-1", "uuid-p", "uuid-tv", "uuid-r"]
    assert await numeros.definir_ordem(ordem) == tuple(ordem)
    assert numeros.ordem == tuple(ordem)


def test_um_ar_condicionado_so_entra_numa_licenca_de_ar_e_o_resto_so_na_de_av(misto):
    """The product of the licence is decided by the category of the manifest."""
    with pytest.raises(OrdemInvalida) as erro:
        Numeros(misto, LICENCA_AV).validar(["uuid-1", "ar-1"])
    assert erro.value.codigo == "produto_incompativel"
    with pytest.raises(OrdemInvalida) as erro:
        Numeros(misto, LICENCA_AR).validar(["uuid-1"])
    assert erro.value.codigo == "produto_incompativel"
    assert Numeros(misto, LICENCA_AR).validar(["ar-1"]) == ("ar-1",)


async def test_uma_identidade_de_outra_licenca_e_numero_ocupado(duas):
    """One equipment in two numbers of the installation answers two data points, and the
    bridge reads a device that contradicts itself.
    """
    gestor, numeros = duas
    with pytest.raises(OrdemInvalida) as erro:
        numeros.validar(["uuid-1", "uuid-2"], alheias=("uuid-2",))
    assert erro.value.codigo == "numero_ocupado"
    livro = await _livro(gestor, (LICENCA_AV, OUTRA_LICENCA_AV), {"av1": ("uuid-1",)})
    with pytest.raises(OrdemInvalida) as erro:
        livro.validar_ordem("av2", ["uuid-1"])
    assert erro.value.codigo == "numero_ocupado"
    # The licence that already holds the equipment may keep it, and may take the free one.
    assert livro.validar_ordem("av1", ["uuid-1", "uuid-2"]) == ("uuid-1", "uuid-2")
    assert livro.validar_ordem("av2", ["uuid-2"]) == ("uuid-2",)


def test_um_manifesto_que_saiu_da_imagem_nao_esvazia_o_numero():
    """A registration whose driver left the image cannot be judged by product, and a number
    is not emptied on boot because a driver failed to load.
    """
    gestor = GestorFalso({}, (_cadastro("uuid-sumido", tipo="tipo_que_saiu"),))
    assert Numeros(gestor, LICENCA_AV).validar(["uuid-sumido"]) == ("uuid-sumido",)
    assert Numeros(gestor, LICENCA_AR).validar(["uuid-sumido"]) == ("uuid-sumido",)


async def test_um_decimo_terceiro_numero_nao_existe(duas):
    """Numbers twelve equipment, and a thirteenth identity names a number the bus
    has not.
    """
    _gestor, numeros = duas
    with pytest.raises(OrdemInvalida) as erro:
        await numeros.definir_ordem(["uuid-1", *[""] * 11, "uuid-2"])
    assert erro.value.codigo == "numeros_demais"
    assert numeros.ordem == ("uuid-1", "uuid-2")


def test_uma_nona_maquina_de_ar_nao_existe(ares):
    """Numbers eight machines in the product of air."""
    _gestor, numeros = ares
    assert numeros.capacidade == 8
    with pytest.raises(OrdemInvalida) as erro:
        numeros.validar(["ar-1", *[""] * 7, "ar-2"])
    assert erro.value.codigo == "numeros_demais"


async def test_a_mesma_caixa_em_dois_numeros_e_recusada(duas):
    """One speaker answering the level of two numbers is a device that contradicts itself."""
    _gestor, numeros = duas
    with pytest.raises(OrdemInvalida) as erro:
        await numeros.definir_ordem(["uuid-1", "uuid-1"])
    assert erro.value.codigo == "numero_repetido"


@pytest.mark.parametrize(
    "ordem", [["uuid-1", 2], "uuid-1", [None], [{"identidade": "uuid-1"}], None, 5]
)
async def test_uma_ordem_que_nao_e_lista_de_texto_e_recusada(duas, ordem):
    _gestor, numeros = duas
    with pytest.raises(OrdemInvalida) as erro:
        await numeros.definir_ordem(ordem)
    assert erro.value.codigo == "identidade_invalida"


def test_a_ordem_aceita_vagas_vazias_e_apara_espacos(duas):
    """An empty string is a number nobody occupies, and an identity typed with spaces around
    it is the same identity.
    """
    _gestor, numeros = duas
    assert numeros.validar(["", " uuid-2 ", ""]) == ("", "uuid-2", "")
    assert numeros.validar([]) == ()
    assert numeros.validar(("uuid-2", "uuid-1")) == ("uuid-2", "uuid-1")


def test_os_perfis_dos_numeros_precisam_caber_nas_cinco_strings():
    """The profiles of a licence travel in five strings of 255 bytes, and an
    order whose profiles do not pack is refused before it is saved.
    """
    gestor = GestorFalso(
        {TIPO: _fabrica()}, tuple(_pesado(f"uuid-{n}", ip=f"192.0.2.{n}") for n in range(1, 7))
    )
    numeros = Numeros(gestor, LICENCA_AV)
    cinco = [f"uuid-{n}" for n in range(1, 6)]
    assert numeros.validar(cinco) == tuple(cinco)
    with pytest.raises(OrdemInvalida) as erro:
        numeros.validar([*cinco, "uuid-6"])
    assert erro.value.codigo == "perfis_longos"


async def test_perfis_cabem_julga_um_cadastro_trocado():
    """A route checks an edited registration against the licence that holds it before it is
    written, because one heavier profile can push the whole set out of its strings.
    """
    leve = _cadastro("uuid-0", ip="192.0.2.10", nome="Sala")
    pesados = tuple(_pesado(f"uuid-{n}", ip=f"192.0.2.{n}") for n in range(1, 6))
    gestor = GestorFalso({TIPO: _fabrica()}, (leve, *pesados))
    ordem = ("uuid-0", *(cadastro.identidade for cadastro in pesados))
    livro = await _livro(gestor, (LICENCA_AV, LICENCA_AR), {"av1": ordem})
    numeros = livro.de("av1")
    assert numeros.ordem == ordem
    assert numeros.perfis_cabem()
    assert not numeros.perfis_cabem(_pesado("uuid-0", ip="192.0.2.10"))
    assert not livro.perfis_cabem(_pesado("uuid-0", ip="192.0.2.10"))
    # An equipment that occupies no number fits anywhere, and a licence of air has no profile.
    assert livro.perfis_cabem(_pesado("uuid-9"))
    assert livro.de("ar1").perfis_cabem(_pesado("uuid-0"))


def test_esvaziar_uma_vaga_nunca_encurta_a_ordem():
    """The position IS the number, so a removal never promotes the number below."""
    assert sem(("a", "b", "c"), "b") == ("a", "", "c")
    assert sem(("a", "b"), "z") == ("a", "b")
    assert sem((), "a") == ()


def test_a_identidade_e_o_numero_vao_e_voltam(duas):
    _gestor, numeros = duas
    assert numeros.identidade(1) == "uuid-1"
    assert numeros.identidade(2) == "uuid-2"
    assert all(numeros.identidade(n) == "" for n in (0, 3, 12, 13, -1))
    assert numeros.numero("uuid-2") == 2
    assert numeros.numero("") == 0
    assert numeros.numero("uuid-que-ninguem-cadastrou") == 0
    assert numeros.ocupadas() == ("uuid-1", "uuid-2")
    assert numeros.id == "av1" and numeros.produto == "av" and numeros.capacidade == 12
    assert numeros.multiroom and numeros.licenca == LICENCA_AV


def test_uma_licenca_de_ar_nao_forma_grupo(ares):
    _gestor, numeros = ares
    assert not numeros.multiroom
    assert numeros.grupo() == 0 and numeros.escravos() == ()


async def test_remover_um_equipamento_esvazia_a_vaga_e_nao_move_ninguem(duas):
    """The position IS the number, so a removal must never promote number 2.

    A shift would move the speaker of number 2 into number 1 in every automation the customer
    already built, and nothing on the bus would say it happened.
    """
    gestor, numeros = duas
    gestor.remover("uuid-1")
    assert await numeros.esquecer("uuid-1") == ("", "uuid-2")
    assert numeros.identidade(2) == "uuid-2"
    assert numeros.numero("uuid-2") == 2
    valores = numeros.valores()
    assert NIVEL_1 not in valores and LIGADO_1 not in valores
    assert valores[ONLINE] == 0b10 and valores[NIVEL_2] == 20


# The data points of a licence of audio and video.


def test_a_numeracao_de_av_e_a_da_secao_8(tres):
    """The numbering for three speakers, written by hand in the test."""
    _gestor, numeros = tres
    valores = numeros.valores()
    assert valores[NIVEL_1] == 20 and valores[NIVEL_2] == 20 and valores[NIVEL_3] == 20
    assert valores[ONLINE] == 0b111
    assert valores[GRUPO] == 0
    assert valores[MUDOS] == 0
    assert valores[ENTRADAS] == "1=1;2=1;3=1"
    assert valores[MODOS] == "" and valores[TITULOS] == ""
    assert valores[PERFIS_1].startswith("1|au|Sala|")
    assert all(valores[dpid] == "" for dpid in (PERFIS_2, PERFIS_3, PERFIS_4, PERFIS_5))
    # A speaker that cannot switch itself off has no power switch on the app.
    assert not any(dpid in valores for dpid in (LIGADO_1, LIGADO_2, LIGADO_3))
    # The scene and the command are send only, and the chip never echoes a received DP; the
    # names of the scenes belong to the module that owns the scenes.
    assert not any(dpid in valores for dpid in (CENA, COMANDO, NOMES_CENAS_1, NOMES_CENAS_2))


def test_um_numero_vazio_nao_publica_data_point_nenhum():
    """A bridge reading a false online would show an empty number as a speaker switched off."""
    gestor = GestorFalso({TIPO: _fabrica()}, (_cadastro("uuid-2", ip=IP_2, nome="Cozinha"),))
    valores = Numeros(gestor, LICENCA_AV, ("", "uuid-2")).valores()
    assert LIGADO_1 not in valores and NIVEL_1 not in valores
    assert valores[ONLINE] == 0b10
    assert valores[ENTRADAS] == "2=1"
    assert valores[PERFIS_1] == "2|au|Cozinha|Wi-Fi,Linha|||NMEPG"


async def test_uma_identidade_pendurada_e_um_numero_vazio(duas):
    """The file may be edited by hand, and a dangling identity is an empty number, not a 500."""
    gestor, numeros = duas
    gestor.remover("uuid-1")
    valores = numeros.valores()
    assert NIVEL_1 not in valores and valores[ONLINE] == 0b10
    assert await numeros.aplicar(NIVEL_1, 30) == "numero_offline"
    assert await numeros.aplicar(COMANDO, "1:tocar") == "numero_offline"


async def test_um_equipamento_sem_driver_esta_offline_e_recusa_como_offline(duas):
    """A registration whose driver failed to build is a number nothing answers for."""
    gestor, numeros = duas
    gestor.desmontar("uuid-1")
    valores = numeros.valores()
    assert NIVEL_1 not in valores and valores[ONLINE] == 0b10
    assert await numeros.aplicar(NIVEL_1, 30) == "numero_offline"


async def test_o_dp_de_ligado_so_existe_para_o_par_de_energia(misto):
    """The power switch of number n is DP 100 + n, read from ligado and written as
    ligar/desligar, and only for a driver that declares BOTH; a speaker that is always on
    stays silent on it.
    """
    numeros = Numeros(misto, LICENCA_AV, ("uuid-1", "uuid-p"))
    _caixa(misto, "uuid-p").estado_de_teste(ligado=False)
    _caixa(misto, "uuid-1").estado_de_teste(ligado=True)
    valores = numeros.valores()
    assert valores[LIGADO_2] is False
    assert LIGADO_1 not in valores
    assert await numeros.aplicar(LIGADO_2, True) is None
    assert await numeros.aplicar(LIGADO_2, False) is None
    assert _chamadas(misto, "uuid-p") == [("ligar", None), ("desligar", None)]
    assert await numeros.aplicar(LIGADO_1, True) == "nao_suportado"
    assert _chamadas(misto, "uuid-1") == []


async def test_metade_do_par_de_energia_nao_e_chave_nenhuma():
    """A driver that declares ligar without desligar reports nothing on the switch and a set
    answers nao_suportado, because a switch that cannot turn off is one the customer cannot
    trust.
    """
    meio = _projetor(capacidades=("ligar", "fonte"))
    gestor = GestorFalso({TIPO_DE_PROJETOR: meio}, (_cadastro("uuid-p", tipo=TIPO_DE_PROJETOR),))
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-p",))
    _caixa(gestor, "uuid-p").estado_de_teste(ligado=True)
    assert LIGADO_1 not in numeros.valores()
    assert await numeros.aplicar(LIGADO_1, True) == "nao_suportado"
    assert _chamadas(gestor, "uuid-p") == []


def test_o_nivel_so_e_publicado_quando_o_driver_o_conhece(duas):
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").estado_de_teste(volume=None)
    _caixa(gestor, "uuid-2").estado_de_teste(volume=0)
    valores = numeros.valores()
    assert NIVEL_1 not in valores
    assert valores[NIVEL_2] == 0


def test_o_mudo_vai_nos_bits_e_a_entrada_e_o_indice_da_lista_do_cadastro(duas):
    """DP 145 carries one bit per muted number and DP 146 the 1-based index of the
    active input in the list of the registration, found by the value of the driver; an input
    the list does not name is not published.
    """
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").estado_de_teste(mudo=True, fonte="line-in")
    _caixa(gestor, "uuid-2").estado_de_teste(mudo=False, fonte="hdmi3")
    valores = numeros.valores()
    assert valores[MUDOS] == 0b01
    assert valores[ENTRADAS] == "1=2"
    _caixa(gestor, "uuid-2").estado_de_teste(mudo=True, fonte=None)
    valores = numeros.valores()
    assert valores[MUDOS] == 0b11
    assert valores[ENTRADAS] == "1=2"


def test_o_modo_de_som_e_o_indice_da_lista_de_modos(misto):
    """DP 147 is the sound mode of a receiver as the index in its list of modes."""
    numeros = Numeros(misto, LICENCA_AV, ("uuid-1", "uuid-r"))
    _caixa(misto, "uuid-r").estado_de_teste(modo="movie", fonte="hdmi2")
    valores = numeros.valores()
    assert valores[MODOS] == "2=2"
    assert valores[ENTRADAS] == "1=1;2=2"
    _caixa(misto, "uuid-r").estado_de_teste(modo="night")
    assert numeros.valores()[MODOS] == ""


def test_o_titulo_do_que_toca_vai_no_dp_de_titulos_e_nunca_e_empurrado(duas):
    """DP 148 carries the title of every number that has one, each inside 18
    characters, and it only answers a query, because a title changes with every track.
    """
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").estado_de_teste(tocando=MUSICA, reproduzindo=True)
    _caixa(gestor, "uuid-2").estado_de_teste(tocando=None, reproduzindo=False)
    assert numeros.valores()[TITULOS] == "1=Musica 1 - Artista"
    _caixa(gestor, "uuid-2").estado_de_teste(tocando="Uma faixa com um titulo muito comprido")
    assert numeros.valores()[TITULOS] == "1=Musica 1 - Artista;2=Uma faixa com um t"
    assert mapa.de_dp("av", TITULOS).empurrado is False


def test_os_perfis_viajam_empacotados_nos_dps_149_a_153(duas):
    """Numero|template|nome|entradas|atalhos|modos|funcoes, one per occupied
    number, joined by ';' and spread over five strings filled in order.
    """
    _gestor, numeros = duas
    valores = numeros.valores()
    assert valores[PERFIS_1] == "1|au|Sala|Wi-Fi,Linha|||NMEPG;2|au|Cozinha|Wi-Fi,Linha|||NMEPG"
    assert all(valores[dpid] == "" for dpid in (PERFIS_2, PERFIS_3, PERFIS_4, PERFIS_5))
    assert mapa.desempacotar(valores[dpid] for dpid in range(PERFIS_1, PERFIS_5 + 1)) == (
        "1|au|Sala|Wi-Fi,Linha|||NMEPG",
        "2|au|Cozinha|Wi-Fi,Linha|||NMEPG",
    )


def test_um_perfil_que_nao_cabe_fica_fora_do_barramento_e_nao_derruba_o_resto():
    """The routes refuse a registration whose profile does not pack, so this is a config.json
    edited by hand: the profile strings stay off the bus and every other data point is still
    published.
    """
    listas = {"entradas": ENTRADAS_PESADAS, "atalhos": ATALHOS_PESADOS, "modos": MODOS_PESADOS}
    gestor = GestorFalso(
        {TIPO_DE_RECEIVER: _receiver()},
        (_cadastro("uuid-r", tipo=TIPO_DE_RECEIVER, nome=NOME_PESADO, listas=listas),),
    )
    valores = Numeros(gestor, LICENCA_AV, ("uuid-r",)).valores()
    assert not any(dpid in valores for dpid in range(PERFIS_1, PERFIS_5 + 1))
    assert valores[ONLINE] == 0b1 and valores[NIVEL_1] == 20 and valores[GRUPO] == 0


# The data points of a licence of air.


def test_a_numeracao_de_ar_e_a_da_secao_8(ares):
    """The numbering for two machines, written by hand in the test."""
    _gestor, numeros = ares
    valores = numeros.valores()
    assert valores[AR_LIGADO_1] is True and valores[AR_LIGADO_2] is True
    assert valores[AR_TEMPERATURA_1] == 22 and valores[AR_TEMPERATURA_2] == 22
    assert valores[AR_MODO_1] == "frio" and valores[AR_MODO_2] == "frio"
    assert valores[AR_VENTO_1] == "alto" and valores[AR_VENTO_2] == "alto"
    assert valores[AR_ONLINE] == 0b11
    assert valores[AR_NOMES] == '{"m":["Sala","Quarto"]}'
    assert not any(dpid in valores for dpid in (AR_CENA, AR_NOMES_CENAS_1, AR_NOMES_CENAS_2))
    assert not any(dpid in valores for dpid in (GRUPO, COMANDO, ONLINE, MUDOS))


def test_a_oitava_maquina_termina_no_139():
    gestor = GestorFalso(
        {TIPO_DE_AR: _ar()},
        tuple(_cadastro(f"ar-{k}", tipo=TIPO_DE_AR, ip=f"192.0.2.{k}") for k in range(1, 9)),
    )
    _caixa(gestor, "ar-8").estado_de_teste(ligado=False, temperatura=30, modo="seco", vento="auto")
    valores = Numeros(gestor, LICENCA_AR, tuple(f"ar-{k}" for k in range(1, 9))).valores()
    assert valores[AR_LIGADO_8] is False and valores[AR_TEMPERATURA_8] == 30
    assert valores[AR_MODO_8] == "seco" and valores[AR_VENTO_8] == "auto"
    assert valores[AR_ONLINE] == 0b11111111
    assert 140 not in valores and 141 not in valores


@pytest.mark.parametrize(
    ("campos", "ausentes"),
    [
        ({"ligado": None}, (AR_LIGADO_1,)),
        ({"temperatura": None}, (AR_TEMPERATURA_1,)),
        ({"temperatura": 35}, (AR_TEMPERATURA_1,)),
        ({"temperatura": 15}, (AR_TEMPERATURA_1,)),
        ({"temperatura": True}, (AR_TEMPERATURA_1,)),
        ({"modo": "gelo"}, (AR_MODO_1,)),
        ({"modo": None}, (AR_MODO_1,)),
        ({"vento": "turbo"}, (AR_VENTO_1,)),
        ({"online": False}, ()),
    ],
)
def test_um_valor_fora_do_vocabulario_do_ar_nao_e_publicado(ares, campos, ausentes):
    """A data point is never reported outside its type and its range: a driver
    that cannot tell, or that says a word the enum does not have, leaves it out.
    """
    gestor, numeros = ares
    _caixa(gestor, "ar-1").estado_de_teste(**campos)
    valores = numeros.valores()
    assert not any(dpid in valores for dpid in ausentes)
    esperado = 0b11 if campos.get("online", True) else 0b10
    assert valores[AR_ONLINE] == esperado
    assert valores[AR_TEMPERATURA_2] == 22


def test_uma_maquina_vazia_nao_publica_nada_e_o_nome_dela_fica_vazio():
    gestor = GestorFalso(
        {TIPO_DE_AR: _ar()}, (_cadastro("ar-2", tipo=TIPO_DE_AR, ip=IP_2, nome="Quarto"),)
    )
    _caixa(gestor, "ar-2").estado_de_teste(ligado=True, temperatura=24)
    valores = Numeros(gestor, LICENCA_AR, ("", "ar-2")).valores()
    assert not any(dpid in valores for dpid in (AR_LIGADO_1, AR_TEMPERATURA_1, AR_MODO_1))
    assert valores[AR_LIGADO_2] is True and valores[AR_TEMPERATURA_2] == 24
    assert valores[AR_ONLINE] == 0b10
    assert valores[AR_NOMES] == '{"m":["","Quarto"]}'


def test_nomes_longos_nao_tiram_os_nomes_das_maquinas_do_barramento():
    """A string data point carries 255 BYTES, and the registration takes a name of
    any length, so eight long names still reach the bridge as one JSON it can read.
    """
    gestor = GestorFalso(
        {TIPO_DE_AR: _ar()},
        tuple(
            _cadastro(
                f"ar-{k}", tipo=TIPO_DE_AR, ip=f"192.0.2.{k}", nome="Sala de estar aberta " * 10
            )
            for k in range(1, 9)
        ),
    )
    texto = Numeros(gestor, LICENCA_AR, tuple(f"ar-{k}" for k in range(1, 9))).valores()[AR_NOMES]
    assert len(texto.encode("utf-8")) <= 255
    nomes = json.loads(texto)["m"]
    assert len(nomes) == 8 and all(nome.startswith("Sala") for nome in nomes)


@pytest.mark.parametrize("caractere", ['"', "\\"])
def test_um_nome_que_o_json_escapa_nao_tira_os_nomes_do_barramento(caractere):
    """A name of quotes or backslashes is ordinary input and must not take the names of all
    eight machines off the bus.

    Json escapes a quote and a backslash, so a budget measured in raw bytes lies for
    these names and a shortened list overflows again.
    """
    gestor = GestorFalso(
        {TIPO_DE_AR: _ar()},
        tuple(
            _cadastro(f"ar-{k}", tipo=TIPO_DE_AR, ip=f"192.0.2.{k}", nome=caractere * 40)
            for k in range(1, 9)
        ),
    )
    texto = Numeros(gestor, LICENCA_AR, tuple(f"ar-{k}" for k in range(1, 9))).valores()[AR_NOMES]
    assert len(texto.encode("utf-8")) <= 255
    assert len(json.loads(texto)["m"]) == 8


def test_um_nome_acentuado_nunca_e_cortado_no_meio_de_um_caractere():
    """A JSON cut inside a character reaches the bridge unparseable, which is worse than a
    name the customer sees shortened.
    """
    gestor = GestorFalso(
        {TIPO_DE_AR: _ar()},
        tuple(
            _cadastro(f"ar-{k}", tipo=TIPO_DE_AR, ip=f"192.0.2.{k}", nome="Área " * 40)
            for k in range(1, 9)
        ),
    )
    texto = Numeros(gestor, LICENCA_AR, tuple(f"ar-{k}" for k in range(1, 9))).valores()[AR_NOMES]
    assert len(texto.encode("utf-8")) <= 255
    assert all(nome.startswith("Área") for nome in json.loads(texto)["m"])


# One set: the data points of a number and the channels of the installation.


@pytest.mark.parametrize("dpid", [CENA, 999, "101", 101.0, True, None, 0, -101, 156])
async def test_um_data_point_que_nao_e_deste_modulo_e_recusado(duas, dpid):
    """DP 141 is the scene and belongs to the module that owns the scenes; the rest is not in
    the contract at all.
    """
    _gestor, numeros = duas
    assert await numeros.aplicar(dpid, 1) == "dp_desconhecido"


@pytest.mark.parametrize(
    "dpid",
    [ONLINE, MUDOS, ENTRADAS, MODOS, TITULOS, PERFIS_1, PERFIS_5, NOMES_CENAS_1, NOMES_CENAS_2],
)
async def test_set_em_data_point_de_reporte_e_recusado(duas, dpid):
    """The chip never echoes, so a report only data point takes no set at all."""
    _gestor, numeros = duas
    assert await numeros.aplicar(dpid, 1) == "dp_somente_leitura"


async def test_a_cena_e_os_reports_de_ar_tambem_sao_recusados(ares):
    _gestor, numeros = ares
    assert await numeros.aplicar(AR_CENA, 1) == "dp_desconhecido"
    assert await numeros.aplicar(AR_ONLINE, 1) == "dp_somente_leitura"
    assert await numeros.aplicar(AR_NOMES, "x") == "dp_somente_leitura"
    # The group and the command channel exist in the product of audio and video only.
    assert await numeros.aplicar(GRUPO, 1) == "dp_desconhecido"
    assert await numeros.aplicar(COMANDO, "1:ligar") == "dp_desconhecido"


@pytest.mark.parametrize(
    ("dpid", "valor"),
    [
        (NIVEL_1, 101),
        (NIVEL_1, -1),
        (NIVEL_1, True),
        (NIVEL_1, "30"),
        (NIVEL_1, 30.0),
        (NIVEL_1, None),
        (LIGADO_1, "sim"),
        (LIGADO_1, 1),
        (LIGADO_1, None),
        (GRUPO, 13),
        (GRUPO, -1),
        (GRUPO, "grupo1"),
        (GRUPO, True),
        (COMANDO, ""),
        (COMANDO, "x" * 256),
        (COMANDO, "1:ligar\n"),
        (COMANDO, 5),
        (COMANDO, None),
    ],
)
async def test_um_valor_fora_do_tipo_do_dp_nunca_chega_a_caixa(duas, dpid, valor):
    """Fixes the type of every data point, and nothing wider reaches a speaker."""
    gestor, numeros = duas
    assert await numeros.aplicar(dpid, valor) == "valor_invalido"
    assert _chamadas(gestor, "uuid-1") == [] and _chamadas(gestor, "uuid-2") == []


@pytest.mark.parametrize(
    ("dpid", "valor"),
    [
        (AR_TEMPERATURA_1, 15),
        (AR_TEMPERATURA_1, 31),
        (AR_TEMPERATURA_1, 22.0),
        (AR_TEMPERATURA_1, "22"),
        (AR_TEMPERATURA_1, True),
        (AR_MODO_1, "gelo"),
        (AR_MODO_1, 1),
        (AR_VENTO_1, "turbo"),
        (AR_LIGADO_1, 1),
    ],
)
async def test_um_valor_fora_do_vocabulario_do_ar_nunca_chega_a_maquina(ares, dpid, valor):
    gestor, numeros = ares
    assert await numeros.aplicar(dpid, valor) == "valor_invalido"
    assert _chamadas(gestor, "ar-1") == []


async def test_set_em_numero_vazio_e_recusado():
    gestor = GestorFalso({TIPO: _fabrica()}, ())
    numeros = Numeros(gestor, LICENCA_AV)
    assert await numeros.aplicar(NIVEL_1, 30) == "numero_offline"
    assert await numeros.aplicar(LIGADO_1, True) == "numero_offline"
    assert await numeros.aplicar(GRUPO, 1) == "numero_offline"
    assert await numeros.aplicar(COMANDO, "1:ligar") == "numero_offline"


async def test_o_nivel_vai_como_volume_para_o_proprio_numero(duas):
    gestor, numeros = duas
    assert await numeros.aplicar(NIVEL_2, 30) is None
    assert await numeros.aplicar(NIVEL_2, 0) is None
    assert _chamadas(gestor, "uuid-2") == [("volume", 30), ("volume", 0)]
    assert _chamadas(gestor, "uuid-1") == []


async def test_a_temperatura_o_modo_e_o_vento_vao_direto_a_maquina(ares):
    """The four data points of a machine are the four capabilities,
    with the value as it came, and the machine of number 2 answers for number 2 only.
    """
    gestor, numeros = ares
    assert await numeros.aplicar(AR_TEMPERATURA_2, 23) is None
    assert await numeros.aplicar(AR_MODO_2, "quente") is None
    assert await numeros.aplicar(AR_VENTO_2, "baixo") is None
    assert await numeros.aplicar(AR_LIGADO_2, False) is None
    assert await numeros.aplicar(AR_LIGADO_2, True) is None
    assert _chamadas(gestor, "ar-2") == [
        ("temperatura", 23),
        ("modo", "quente"),
        ("vento", "baixo"),
        ("desligar", None),
        ("ligar", None),
    ]
    assert _chamadas(gestor, "ar-1") == []


async def test_o_canal_de_comando_liga_e_desliga_pelo_par_de_energia(misto):
    numeros = Numeros(misto, LICENCA_AV, ("uuid-1", "uuid-p"))
    assert await numeros.aplicar(COMANDO, "2:ligar") is None
    assert await numeros.aplicar(COMANDO, "2:desligar") is None
    assert _chamadas(misto, "uuid-p") == [("ligar", None), ("desligar", None)]
    assert _chamadas(misto, "uuid-1") == []


async def test_o_mudo_do_canal_alterna_o_estado_atual(duas):
    """The panel has one mute button, so n:mudo toggles what the equipment reports
    now, and a speaker that does not say is taken as not muted.
    """
    gestor, numeros = duas
    caixa = _caixa(gestor, "uuid-1")
    caixa.estado_de_teste(mudo=None)
    assert await numeros.aplicar(COMANDO, "1:mudo") is None
    caixa.estado_de_teste(mudo=False)
    assert await numeros.aplicar(COMANDO, "1:mudo") is None
    caixa.estado_de_teste(mudo=True)
    assert await numeros.aplicar(COMANDO, "1:mudo") is None
    assert caixa.chamadas == [("mudo", True), ("mudo", True), ("mudo", False)]


async def test_a_entrada_do_canal_e_o_indice_da_lista_do_cadastro(duas):
    """N:entrada:k names the k-th item of the list of the registration, and the
    driver receives the VALUE of that item, never the index.
    """
    gestor, numeros = duas
    assert await numeros.aplicar(COMANDO, "1:entrada:2") is None
    assert await numeros.aplicar(COMANDO, "1:entrada:1") is None
    assert _chamadas(gestor, "uuid-1") == [("fonte", "line-in"), ("fonte", "wifi")]
    assert await numeros.aplicar(COMANDO, "1:entrada:3") == "valor_invalido"
    assert await numeros.aplicar(COMANDO, "1:entrada:0") == "valor_invalido"
    assert len(_chamadas(gestor, "uuid-1")) == 2


async def test_o_atalho_e_o_modo_do_canal_leem_as_listas_do_receiver(misto):
    numeros = Numeros(misto, LICENCA_AV, ("uuid-1", "uuid-r"))
    assert await numeros.aplicar(COMANDO, "2:atalho:1") is None
    assert await numeros.aplicar(COMANDO, "2:modo:2") is None
    assert await numeros.aplicar(COMANDO, "2:entrada:1") is None
    assert _chamadas(misto, "uuid-r") == [
        ("atalho", "app:netflix"),
        ("modo", "movie"),
        ("fonte", "hdmi1"),
    ]
    assert await numeros.aplicar(COMANDO, "2:atalho:2") == "valor_invalido"
    assert await numeros.aplicar(COMANDO, "2:modo:3") == "valor_invalido"
    assert len(_chamadas(misto, "uuid-r")) == 3


async def test_um_indice_numa_lista_que_o_cadastro_nao_tem_e_valor_invalido(duas):
    gestor, numeros = duas
    assert await numeros.aplicar(COMANDO, "1:atalho:1") == "valor_invalido"
    assert await numeros.aplicar(COMANDO, "1:modo:1") == "valor_invalido"
    assert _chamadas(gestor, "uuid-1") == []


async def test_a_tecla_do_canal_vai_como_palavra_da_secao_6(misto):
    numeros = Numeros(misto, LICENCA_AV, ("uuid-1", "uuid-tv"))
    assert await numeros.aplicar(COMANDO, "2:tecla:canal_mais") is None
    assert await numeros.aplicar(COMANDO, "2:tecla:ok") is None
    assert _chamadas(misto, "uuid-tv") == [("tecla", "canal_mais"), ("tecla", "ok")]
    assert await numeros.aplicar(COMANDO, "2:tecla:voar") == "valor_invalido"
    assert len(_chamadas(misto, "uuid-tv")) == 2


async def test_o_extra_do_canal_leva_o_valor_inteiro_ao_driver(duas):
    gestor, numeros = duas
    assert await numeros.aplicar(COMANDO, "1:extra:preset:3") is None
    assert _chamadas(gestor, "uuid-1") == [("comando_extra", "preset:3")]


async def test_o_transporte_do_canal_e_o_par_da_secao_6(duas):
    gestor, numeros = duas
    assert await numeros.aplicar(COMANDO, "1:tocar") is None
    assert await numeros.aplicar(COMANDO, "1:pausar") is None
    assert _chamadas(gestor, "uuid-1") == [("tocar", None), ("pausar", None)]


async def test_uma_capacidade_que_o_manifesto_nao_declara_e_nao_suportado(misto):
    """The gate refuses before the driver, so a projector never sees a play and a
    speaker never sees a key.
    """
    numeros = Numeros(misto, LICENCA_AV, ("uuid-1", "uuid-p"))
    assert await numeros.aplicar(COMANDO, "2:tocar") == "nao_suportado"
    assert await numeros.aplicar(COMANDO, "2:mudo") == "nao_suportado"
    assert await numeros.aplicar(COMANDO, "1:proxima") == "nao_suportado"
    assert await numeros.aplicar(COMANDO, "1:tecla:ok") == "nao_suportado"
    assert await numeros.aplicar(NIVEL_2, 30) == "nao_suportado"
    assert _chamadas(misto, "uuid-p") == [] and _chamadas(misto, "uuid-1") == []


@pytest.mark.parametrize(
    "texto",
    ["banda", "1:", "1:ligar:sim", "13:ligar", "0:ligar", "1:volume:30", "1:LIGAR", "1;ligar"],
)
async def test_um_comando_fora_da_gramatica_e_valor_invalido(duas, texto):
    """The command channel is a closed grammar, and the level has a data point of its own."""
    gestor, numeros = duas
    assert await numeros.aplicar(COMANDO, texto) == "valor_invalido"
    assert _chamadas(gestor, "uuid-1") == []


async def test_um_comando_para_um_numero_vazio_e_numero_offline(duas):
    gestor, numeros = duas
    assert await numeros.aplicar(COMANDO, "3:ligar") == "numero_offline"
    assert await numeros.aplicar(COMANDO, "12:tocar") == "numero_offline"
    assert _chamadas(gestor, "uuid-1") == [] and _chamadas(gestor, "uuid-2") == []


@pytest.mark.parametrize(
    ("resposta", "esperado"),
    [
        ("eq_offline", "numero_offline"),
        ("invalid_value", "valor_invalido"),
        ("nao_suportado", "nao_suportado"),
        ("auth_pendente", "auth_pendente"),
        ("erro_aparelho", "erro_aparelho"),
        ("codigo_que_ninguem_traduz", "erro_aparelho"),
    ],
)
async def test_o_codigo_do_driver_vira_o_vocabulario_do_barramento(duas, resposta, esperado):
    """The bus answers a stable code the bridge already knows, never a new one."""
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").recusa = resposta
    codigo = await numeros.aplicar(NIVEL_1, 30)
    assert codigo == esperado and codigo in modulo.CODIGOS
    assert await numeros.aplicar(COMANDO, "1:tocar") == esperado


@pytest.mark.parametrize(
    ("codigo", "esperado"),
    [
        (None, None),
        ("eq_offline", "numero_offline"),
        ("eq_nao_encontrado", "numero_offline"),
        ("invalid_value", "valor_invalido"),
        ("nao_suportado", "nao_suportado"),
        ("auth_pendente", "auth_pendente"),
        ("erro_aparelho", "erro_aparelho"),
        ("dp_desconhecido", "dp_desconhecido"),
        ("codigo_que_ninguem_traduz", "erro_aparelho"),
        ("", "erro_aparelho"),
    ],
)
def test_traduzir_leva_um_codigo_da_secao_6_ao_vocabulario_da_secao_8(codigo, esperado):
    assert traduzir(codigo) == esperado
    assert esperado is None or esperado in modulo.CODIGOS


def test_o_vocabulario_de_ordem_e_de_barramento_e_o_da_secao_8():
    assert set(modulo.CODIGOS_DE_ORDEM) == {
        "numeros_demais",
        "numero_repetido",
        "numero_ocupado",
        "eq_nao_encontrado",
        "identidade_invalida",
        "produto_incompativel",
        "perfis_longos",
    }
    assert set(modulo.CODIGOS) == {
        "dp_desconhecido",
        "dp_somente_leitura",
        "valor_invalido",
        "numero_offline",
        "licenca_desconhecida",
        "nao_suportado",
        "auth_pendente",
        "erro_aparelho",
    }


async def test_nenhuma_excecao_escapa_do_aplicar(duas):
    """A speaker that raises is a stable code, never an exception out of the bus."""
    gestor, numeros = duas
    for identidade in ("uuid-1", "uuid-2"):
        _caixa(gestor, identidade).estoura = True
    assert await numeros.aplicar(NIVEL_1, 30) == "erro_aparelho"
    assert await numeros.aplicar(COMANDO, "1:tocar") == "erro_aparelho"
    assert await numeros.aplicar(GRUPO, 1) == "erro_aparelho"


# An action of a scene, routed the way a data point is.


async def test_acionar_roteia_o_volume_e_o_transporte_pelo_grupo(duas):
    """A scene names equipment, action and value, and the group routes it the way the data
    points are routed: the volume of a slave through the master, its transport to the master,
    its input to the slave itself.
    """
    gestor, numeros = duas
    assert await numeros.acionar("uuid-2", "volume", 30) is None
    assert _chamadas(gestor, "uuid-2") == [("volume", 30)]
    assert await numeros.aplicar(GRUPO, 1) is None
    _caixa(gestor, "uuid-2").chamadas.clear()
    _caixa(gestor, "uuid-1").chamadas.clear()
    assert await numeros.acionar("uuid-2", "volume", 42) is None
    assert await numeros.acionar("uuid-2", "tocar", None) is None
    assert await numeros.acionar("uuid-2", "pausar", None) is None
    assert await numeros.acionar("uuid-2", "fonte", "line-in") is None
    assert await numeros.acionar("uuid-2", "comando_extra", "preset:3") is None
    assert _chamadas(gestor, "uuid-1") == [
        ("volume_de_escravo", (IP_2, 42)),
        ("tocar", None),
        ("pausar", None),
    ]
    assert _chamadas(gestor, "uuid-2") == [("fonte", "line-in"), ("comando_extra", "preset:3")]


async def test_acionar_e_o_canal_levam_a_radio_e_o_preset_de_um_escravo_ao_mestre():
    """A radio or a preset pressed on a slave takes the group down the way a play
    does, so a shortcut of a slave goes to the master by name, by the command channel and by
    the value of the list of the registration.
    """
    radio = Item("Radio", "http://10.0.0.2/radio")
    listas = {"entradas": ENTRADAS_DA_CAIXA, "atalhos": (radio,)}
    gestor = GestorFalso(
        {TIPO: _fabrica(capacidades=(*CAPACIDADES, "atalho"))},
        (
            _cadastro("uuid-1", ip=IP_1, nome="Sala", listas=listas),
            _cadastro("uuid-2", ip=IP_2, nome="Cozinha", listas=listas),
        ),
    )
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-2"))
    assert await numeros.acionar("uuid-2", "atalho", "preset:3") is None
    assert _chamadas(gestor, "uuid-2") == [("atalho", "preset:3")]
    assert await numeros.aplicar(GRUPO, 1) is None
    _caixa(gestor, "uuid-1").chamadas.clear()
    _caixa(gestor, "uuid-2").chamadas.clear()
    assert await numeros.acionar("uuid-2", "atalho", "preset:3") is None
    assert await numeros.aplicar(COMANDO, "2:atalho:1") is None
    assert _chamadas(gestor, "uuid-1") == [("atalho", "preset:3"), ("atalho", radio.valor)]
    assert _chamadas(gestor, "uuid-2") == []
    # The master presses its own key, on its own, and a solo number too.
    assert await numeros.aplicar(COMANDO, "1:atalho:1") is None
    assert _chamadas(gestor, "uuid-1")[-1] == ("atalho", radio.valor)


async def test_acionar_forma_o_grupo_pela_identidade_do_mestre_e_solo_pela_string_vazia(duas):
    gestor, numeros = duas
    assert await numeros.acionar("uuid-2", "grupo", "uuid-1") is None
    assert numeros.grupo() == 1 and numeros.escravos() == (2,)
    assert _chamadas(gestor, "uuid-2") == [("entrar_no_grupo", IP_1)]
    assert await numeros.acionar("uuid-2", "grupo", "") is None
    assert numeros.grupo() == 0
    assert _chamadas(gestor, "uuid-1") == [("desfazer_grupo", None)]
    assert await numeros.acionar("uuid-1", "grupo", "uuid-2") is None
    assert numeros.grupo() == 2 and numeros.escravos() == (1,)
    assert await numeros.acionar("uuid-1", "grupo", None) is None
    assert numeros.grupo() == 0


@pytest.mark.parametrize("valor", ["uuid-que-ninguem-cadastrou", 5, 1, True, [], "solo"])
async def test_acionar_grupo_com_um_mestre_fora_da_licenca_e_valor_invalido(duas, valor):
    gestor, numeros = duas
    assert await numeros.acionar("uuid-1", "grupo", valor) == "valor_invalido"
    assert numeros.grupo() == 0
    assert _chamadas(gestor, "uuid-1") == [] and _chamadas(gestor, "uuid-2") == []


async def test_acionar_num_equipamento_que_nao_ocupa_numero_e_numero_offline(duas):
    _gestor, numeros = duas
    assert await numeros.acionar("uuid-que-ninguem-cadastrou", "volume", 30) == "numero_offline"
    assert await numeros.acionar("", "tocar", None) == "numero_offline"


async def test_acionar_traduz_o_codigo_do_driver_e_o_portao_da_secao_6(duas):
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").recusa = "eq_offline"
    assert await numeros.acionar("uuid-1", "volume", 30) == "numero_offline"
    assert await numeros.acionar("uuid-1", "ligar", None) == "nao_suportado"


async def test_acionar_numa_licenca_de_ar_recusa_o_grupo_e_manda_o_resto(ares):
    gestor, numeros = ares
    assert await numeros.acionar("ar-1", "grupo", "ar-1") == "nao_suportado"
    assert await numeros.acionar("ar-1", "grupo", "") == "nao_suportado"
    assert await numeros.acionar("ar-1", "temperatura", 24) is None
    assert _chamadas(gestor, "ar-1") == [("temperatura", 24)]


# The reread asks the DEVICE, out of turn and awaited.


async def test_reler_visita_o_equipamento_dono_do_data_point(duas):
    """The reread is a check against the device, so the equipment of the number is
    polled out of turn and the caller waits for it; for the command channel the number comes
    from the command itself.
    """
    gestor, numeros = duas
    await numeros.reler(NIVEL_2)
    assert gestor.visitas == ["uuid-2"]
    assert _caixa(gestor, "uuid-2").visitas == 1
    await numeros.reler(LIGADO_1, True)
    await numeros.reler(COMANDO, "1:ligar")
    assert gestor.visitas == ["uuid-2", "uuid-1", "uuid-1"]
    # A data point of the installation, a bad command, an empty number and a number outside
    # the contract visit nobody.
    await numeros.reler(GRUPO, 1)
    await numeros.reler(COMANDO, "lixo")
    await numeros.reler(COMANDO, None)
    await numeros.reler(NIVEL_3, 30)
    await numeros.reler(999, 30)
    await numeros.reler("101", 30)
    assert gestor.visitas == ["uuid-2", "uuid-1", "uuid-1"]


async def test_reler_numa_licenca_de_ar_visita_a_maquina(ares):
    gestor, numeros = ares
    await numeros.reler(AR_TEMPERATURA_2, 23)
    await numeros.reler(AR_VENTO_1, "alto")
    assert gestor.visitas == ["ar-2", "ar-1"]


# The group of a licence of audio and video, every rule of it paid on the bench.


async def test_um_grupo_num_numero_que_nao_agrupa_e_nao_suportado_e_nunca_offline(misto):
    """An online TV in a number cannot lead a group, and the code says so; offline is only for
    a number nothing answers for.
    """
    assert await Numeros(misto, LICENCA_AV, ("uuid-tv",)).aplicar(GRUPO, 1) == "nao_suportado"
    assert await Numeros(misto, LICENCA_AV).aplicar(GRUPO, 1) == "numero_offline"
    assert _chamadas(misto, "uuid-tv") == []


async def test_reordenar_mantem_o_grupo_pela_identidade_e_nunca_pela_posicao(misto):
    """A projector put in the number of a slave must not inherit its role: the books follow
    the identity, so the group falls and the level of that number reaches the projector.
    """
    misto._cadastros["uuid-2"] = _cadastro("uuid-2", ip="192.0.2.16", nome="Cozinha")
    misto._drivers["uuid-2"] = _fabrica()(misto._cadastros["uuid-2"])
    projetor = _projetor(capacidades=("volume", "ligar", "desligar"))
    misto._catalogo[TIPO_DE_PROJETOR] = projetor
    misto._drivers["uuid-p"] = projetor(misto._cadastros["uuid-p"])
    numeros = Numeros(misto, LICENCA_AV, ("uuid-1", "uuid-2"))
    assert await numeros.aplicar(GRUPO, 1) is None
    assert numeros.grupo() == 1
    await numeros.definir_ordem(["uuid-1", "uuid-p", "uuid-2"])
    assert numeros.grupo() == 0
    assert _chamadas(misto, "uuid-p") == []
    assert await numeros.aplicar(NIVEL_2, 30) is None
    assert _chamadas(misto, "uuid-p") == [("volume", 30)]
    assert ("volume", 30) not in _chamadas(misto, "uuid-1")
    assert ("volume_de_escravo", ("192.0.2.16", 30)) not in _chamadas(misto, "uuid-1")


async def test_a_ordem_nova_larga_so_o_escravo_que_saiu_e_mantem_o_grupo(tres):
    """A group whose master and one slave keep their numbers is still a group; only the slave
    that left the order is dropped, with its mark cleared.
    """
    gestor, numeros = tres
    assert await numeros.aplicar(GRUPO, 1) is None
    assert numeros.escravos() == (2, 3)
    await numeros.definir_ordem(["uuid-1", "uuid-2", ""])
    assert numeros.grupo() == 1 and numeros.escravos() == (2,)
    assert _caixa(gestor, "uuid-3").marcas == [True, False]
    assert ("desfazer_grupo", None) not in _chamadas(gestor, "uuid-1")


async def test_multiroom_sem_a_capacidade_de_agrupar_ocupa_um_numero_e_nao_lidera():
    """The manifest decides what the equipment can do, never whether it has a number."""
    gestor = GestorFalso(
        {TIPO: _fabrica(capacidades=("volume", "tocar"))},
        (_cadastro("uuid-1"), _cadastro("uuid-2", ip=IP_2)),
    )
    numeros = Numeros(gestor, LICENCA_AV)
    assert await numeros.definir_ordem(["uuid-1", "uuid-2"]) == ("uuid-1", "uuid-2")
    assert await numeros.aplicar(GRUPO, 1) == "nao_suportado"
    assert _chamadas(gestor, "uuid-2") == []


async def test_um_grupo_misto_nunca_e_oferecido():
    """A group only exists between speakers of the same domain, so a speaker of
    another kind is never even asked to join.
    """
    gestor = GestorFalso(
        {TIPO: _fabrica(), OUTRO_TIPO: _fabrica(OUTRO_TIPO)},
        (_cadastro("uuid-1", ip=IP_1), _cadastro("uuid-outra", tipo=OUTRO_TIPO, ip=IP_2)),
    )
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-outra"))
    assert await numeros.aplicar(GRUPO, 1) == "nao_suportado"
    assert _chamadas(gestor, "uuid-outra") == []
    assert numeros.grupo() == 0
    assert numeros.valores()[GRUPO] == 0


async def test_um_grupo_de_uma_caixa_so_e_recusado():
    """A group of one is not a group, and answering ok would publish one nobody hears."""
    gestor = GestorFalso({TIPO: _fabrica()}, (_cadastro("uuid-1"),))
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-1",))
    assert await numeros.aplicar(GRUPO, 1) == "nao_suportado"
    assert _chamadas(gestor, "uuid-1") == []


async def test_o_grupo_se_forma_nomeando_o_mestre(duas):
    """The slave joins the address of the master, the master never joins itself, and DP 142
    carries the NUMBER of the master.
    """
    gestor, numeros = duas
    assert await numeros.aplicar(GRUPO, 1) is None
    assert _chamadas(gestor, "uuid-2") == [("entrar_no_grupo", IP_1)]
    assert _chamadas(gestor, "uuid-1") == []
    assert numeros.grupo() == 1
    assert numeros.escravos() == (2,)
    assert _caixa(gestor, "uuid-1").marcas == [True]
    assert _caixa(gestor, "uuid-2").marcas == [True]
    assert numeros.valores()[GRUPO] == 1


async def test_um_mestre_sem_endereco_e_numero_offline():
    """A speaker whose address is not known yet cannot be joined, so the number is offline."""
    gestor = GestorFalso(
        {TIPO: _fabrica()}, (_cadastro("uuid-1", ip=""), _cadastro("uuid-2", ip=IP_2))
    )
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-2"))
    assert await numeros.aplicar(GRUPO, 1) == "numero_offline"
    assert _chamadas(gestor, "uuid-2") == []
    assert numeros.grupo() == 0


async def test_um_grupo_sem_companheira_montada_e_numero_offline(duas):
    gestor, numeros = duas
    gestor.desmontar("uuid-2")
    assert await numeros.aplicar(GRUPO, 1) == "numero_offline"
    assert numeros.grupo() == 0


async def test_um_convite_recusado_por_todos_nao_forma_grupo(duas):
    gestor, numeros = duas
    _caixa(gestor, "uuid-2").recusa = "eq_offline"
    assert await numeros.aplicar(GRUPO, 1) == "numero_offline"
    assert numeros.grupo() == 0 and numeros.escravos() == ()
    assert _caixa(gestor, "uuid-1").marcas == [] and _caixa(gestor, "uuid-2").marcas == []


async def test_um_convite_recusado_por_uma_caixa_nao_derruba_o_grupo_das_outras(tres):
    gestor, numeros = tres
    _caixa(gestor, "uuid-3").recusa = "eq_offline"
    assert await numeros.aplicar(GRUPO, 1) is None
    assert numeros.grupo() == 1 and numeros.escravos() == (2,)
    assert _caixa(gestor, "uuid-3").marcas == []
    assert numeros.valores()[GRUPO] == 1


async def test_o_transporte_de_um_escravo_vai_para_o_mestre(duas):
    """A play on a slave DISMANTLES the group, so it never reaches the slave."""
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    _caixa(gestor, "uuid-2").chamadas.clear()
    assert await numeros.aplicar(COMANDO, "2:tocar") is None
    assert await numeros.aplicar(COMANDO, "2:pausar") is None
    assert _chamadas(gestor, "uuid-2") == []
    assert _chamadas(gestor, "uuid-1") == [("tocar", None), ("pausar", None)]
    # The master answers its own transport, and the transport of a solo speaker is its own.
    assert await numeros.aplicar(COMANDO, "1:tocar") is None
    assert _chamadas(gestor, "uuid-1")[-1] == ("tocar", None)


async def test_o_volume_de_um_escravo_passa_pelo_mestre(duas):
    """The volume of a slave goes through the master, never to the slave."""
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    _caixa(gestor, "uuid-2").chamadas.clear()
    assert await numeros.aplicar(NIVEL_2, 42) is None
    assert _chamadas(gestor, "uuid-2") == []
    assert _chamadas(gestor, "uuid-1") == [("volume_de_escravo", (IP_2, 42))]
    # The master answers for itself, and its own volume never takes the slave path.
    assert await numeros.aplicar(NIVEL_1, 30) is None
    assert ("volume", 30) in _chamadas(gestor, "uuid-1")


async def test_o_escravo_espelha_o_que_o_mestre_toca(duas):
    """A slave answers stop even while the group plays, so what the master plays
    is mirrored onto it and the title of the slave is the title of the master.
    """
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").estado_de_teste(reproduzindo=True, tocando=MUSICA)
    await numeros.aplicar(GRUPO, 1)
    escravo = _caixa(gestor, "uuid-2")
    assert escravo.espelho == MUSICA
    assert escravo.estado().reproduzindo is True and escravo.estado().tocando == MUSICA
    assert numeros.valores()[TITULOS] == "1=Musica 1 - Artista;2=Musica 1 - Artista"


async def test_a_entrada_de_um_escravo_nao_e_desviada_para_o_mestre(duas):
    """The input of a speaker is its own even in a group, and the driver is the one that
    refuses it while grouped, because it is the driver that knows the group breaks.
    """
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    _caixa(gestor, "uuid-2").recusa = "nao_suportado"
    assert await numeros.aplicar(COMANDO, "2:entrada:2") == "nao_suportado"
    assert _chamadas(gestor, "uuid-2")[-1] == ("fonte", "line-in")
    assert ("fonte", "line-in") not in _chamadas(gestor, "uuid-1")


async def test_desfazer_o_grupo_fala_com_o_mestre_e_solta_todo_mundo(duas):
    """Only the master may dismantle the group it leads."""
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    _caixa(gestor, "uuid-1").chamadas.clear()
    assert await numeros.aplicar(GRUPO, 0) is None
    assert _chamadas(gestor, "uuid-1") == [("desfazer_grupo", None)]
    assert numeros.grupo() == 0 and numeros.escravos() == ()
    assert _caixa(gestor, "uuid-2").marcas == [True, False]
    assert _caixa(gestor, "uuid-1").marcas == [True, False]
    # Solo on a licence that has no group is nothing to do, and never a refusal.
    assert await numeros.aplicar(GRUPO, 0) is None
    assert _chamadas(gestor, "uuid-1") == [("desfazer_grupo", None)]


async def test_trocar_de_mestre_desfaz_o_grupo_anterior(duas):
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    assert await numeros.aplicar(GRUPO, 2) is None
    assert ("desfazer_grupo", None) in _chamadas(gestor, "uuid-1")
    assert _chamadas(gestor, "uuid-1")[-1] == ("entrar_no_grupo", IP_2)
    assert numeros.grupo() == 2 and numeros.escravos() == (1,)


async def test_um_grupo_zumbi_e_saneado_no_boot(duas):
    """A group left by a previous run answers commands nobody asked for, so the
    physical group goes down before the hub publishes a state it did not build.
    """
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").grupo = _Grupo(escravos=({"identidade": "uuid-2"},))
    await numeros.sanear()
    assert _chamadas(gestor, "uuid-1") == [("ler_grupo", None), ("desfazer_grupo", None)]
    assert _chamadas(gestor, "uuid-2") == [("ler_grupo", None)]
    assert numeros.grupo() == 0
    assert _caixa(gestor, "uuid-1").marcas == [False] and _caixa(gestor, "uuid-2").marcas == [False]


async def test_uma_caixa_muda_no_boot_nao_derruba_o_saneamento(duas):
    """A speaker that does not answer its group on boot is a warning, never an exception."""
    gestor, numeros = duas
    _caixa(gestor, "uuid-1").estoura = True
    await numeros.sanear()
    assert numeros.grupo() == 0


async def test_sanear_e_sincronizar_numa_licenca_de_ar_nao_tocam_em_maquina_nenhuma(ares):
    gestor, numeros = ares
    await numeros.sanear()
    await numeros.sincronizar()
    assert _chamadas(gestor, "ar-1") == [] and _chamadas(gestor, "ar-2") == []
    assert numeros.escravos_alheios() == ()


async def test_o_escravo_que_saiu_do_modo_multiroom_e_reconciliado(duas):
    """A slave out of the multiroom mode for two polls lost the group to a reboot
    or to the application of the manufacturer, so our books stop routing through the master.
    """
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    _caixa(gestor, "uuid-2").fora = True
    _caixa(gestor, "uuid-1").chamadas.clear()
    await numeros.sincronizar()
    assert numeros.escravos() == ()
    assert numeros.grupo() == 0
    assert _chamadas(gestor, "uuid-1") == [("desfazer_grupo", None)]
    # The volume of that number goes back to the number itself, and not through the old master.
    assert await numeros.aplicar(NIVEL_2, 55) is None
    assert ("volume", 55) in _chamadas(gestor, "uuid-2")


async def test_um_escravo_que_saiu_deixa_o_grupo_de_pe_para_os_que_ficaram(tres):
    gestor, numeros = tres
    await numeros.aplicar(GRUPO, 1)
    _caixa(gestor, "uuid-3").fora = True
    _caixa(gestor, "uuid-1").chamadas.clear()
    await numeros.sincronizar()
    assert numeros.grupo() == 1 and numeros.escravos() == (2,)
    assert _chamadas(gestor, "uuid-1") == []
    assert _caixa(gestor, "uuid-3").marcas == [True, False]


async def test_sincronizar_espelha_o_mestre_a_cada_passagem(duas):
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    _caixa(gestor, "uuid-1").estado_de_teste(tocando=MUSICA, reproduzindo=True)
    await numeros.sincronizar()
    assert _caixa(gestor, "uuid-2").espelho == MUSICA
    assert _caixa(gestor, "uuid-2").estado().reproduzindo is True


async def test_sincronizar_solta_um_grupo_cujo_mestre_sumiu_do_cadastro(duas):
    """A master that is not registered any more leads nobody, and the books say so."""
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    gestor.remover("uuid-1")
    await numeros.sincronizar()
    assert numeros.grupo() == 0 and numeros.escravos() == ()
    assert _caixa(gestor, "uuid-2").marcas == [True, False]


async def test_o_grupo_cai_quando_o_mestre_sai_da_ordem(duas):
    """A group whose master left the order is not a group, and nothing may keep routing to it."""
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    await numeros.definir_ordem(["", "uuid-2"])
    assert numeros.grupo() == 0
    assert _chamadas(gestor, "uuid-2") == [("entrar_no_grupo", IP_1)]
    assert ("desfazer_grupo", None) in _chamadas(gestor, "uuid-1")


async def test_as_operacoes_de_grupo_sao_serializadas(duas):
    """The bench proved forming, sanitizing and reconciling race, so ONE lock holds them and
    two moves are never on the wire at the same time.
    """
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    eventos = type(_caixa(gestor, "uuid-1")).eventos
    eventos.clear()
    porta = asyncio.Event()
    for identidade in ("uuid-1", "uuid-2"):
        _caixa(gestor, identidade).pausa = porta
    trocar = asyncio.create_task(numeros.aplicar(GRUPO, 2))
    desfazer = asyncio.create_task(numeros.aplicar(GRUPO, 0))
    for _ in range(8):
        await asyncio.sleep(0)
    # Both are alive and only ONE of them opened a move: the other is waiting on the lock.
    assert eventos == ["uuid-1:desfazer_grupo:inicio"]
    porta.set()
    assert await trocar is None
    assert await desfazer is None
    assert all(
        anterior.endswith("inicio") and seguinte.endswith("fim")
        for anterior, seguinte in zip(eventos[::2], eventos[1::2], strict=True)
    )
    assert numeros.grupo() == 0


async def test_um_movimento_de_grupo_com_codigo_proprio_vira_erro_aparelho(duas):
    """A driver that invents a code would reach the bridge as a word nobody translates."""
    gestor, numeros = duas
    _caixa(gestor, "uuid-2").recusa = "codigo_que_ninguem_traduz"
    assert await numeros.aplicar(GRUPO, 1) == "erro_aparelho"
    assert numeros.grupo() == 0


async def test_um_movimento_de_grupo_que_emudece_e_numero_offline_e_nao_um_travamento(duas):
    """The lock is held while a speaker answers, so the deadline of one move is what keeps a
    box that accepted the connection and went quiet from freezing the group of the licence;
    and a box that did not answer in time is offline, the same as on the direct road, not a
    fault of the device.
    """
    gestor, _ = duas
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-2"), limite_s=0.05)
    _caixa(gestor, "uuid-2").pausa = asyncio.Event()
    assert await numeros.aplicar(GRUPO, 1) == "numero_offline"
    assert numeros.grupo() == 0
    assert await numeros.aplicar(NIVEL_1, 30) is None


async def test_o_volume_de_um_escravo_cujo_mestre_emudece_e_numero_offline(duas):
    """The volume of a slave goes through the master, and a master that accepted
    the connection and went quiet answers the code of a number that did not answer, on the
    scene road and on the bus alike.
    """
    gestor, _ = duas
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-2"), limite_s=0.05)
    assert await numeros.aplicar(GRUPO, 1) is None
    _caixa(gestor, "uuid-1").pausa = asyncio.Event()
    assert await numeros.acionar("uuid-2", "volume", 30) == "numero_offline"
    assert await numeros.aplicar(NIVEL_2, 31) == "numero_offline"


async def test_um_driver_multiroom_sem_os_movimentos_de_grupo_nao_derruba_o_barramento():
    """A driver that declares agrupar and offers no move is refused, never crashed into, and
    the code is the one of a capability it cannot fulfil, because the speaker is there.
    """
    gestor = GestorFalso(
        {TIPO: _fabrica(com_movimentos=False)},
        (_cadastro("uuid-1", ip=IP_1), _cadastro("uuid-2", ip=IP_2)),
    )
    numeros = Numeros(gestor, LICENCA_AV, ("uuid-1", "uuid-2"))
    assert await numeros.aplicar(GRUPO, 1) == "nao_suportado"
    assert await numeros.acionar("uuid-2", "grupo", "uuid-1") == "nao_suportado"
    await numeros.sanear()
    await numeros.sincronizar()
    assert numeros.escravos_alheios() == ()
    assert await numeros.aplicar(NIVEL_2, 30) is None


async def test_sanear_pergunta_as_caixas_juntas_e_nao_uma_apos_a_outra():
    """/health answers in about 7 s on the reference appliance, and sanear runs
    before the listening socket opens.

    One deadline per speaker meant a site whose boxes are unreachable, which is a VLAN
    change or a router reboot, had no panel for half a minute, and that is exactly when the
    operator needs it. Six mute speakers must cost the slowest one, not the sum.
    """

    class Muda(_fabrica()):
        async def ler_grupo(self):
            await asyncio.Event().wait()

    gestor = GestorFalso(
        {TIPO: Muda}, tuple(_cadastro(f"uuid-{n}", ip=f"192.0.2.{n}") for n in range(1, 7))
    )
    numeros = Numeros(gestor, LICENCA_AV, tuple(f"uuid-{n}" for n in range(1, 7)), limite_s=0.2)
    laco = asyncio.get_running_loop()
    comeco = laco.time()
    await numeros.sanear()
    gasto = laco.time() - comeco
    # Six deadlines end to end would be 1.2 s; one deadline plus slack is the whole budget.
    assert gasto < 0.6, f"sanear took {gasto:.2f}s, which is one deadline per speaker"
    assert numeros.grupo() == 0


async def test_uma_caixa_escrava_de_grupo_alheio_e_reconhecida_e_nao_lida_como_solo(duas):
    """The customer can group a speaker with the app of the manufacturer, and a
    lost reply to a join or a restart with a group up reach the same state.

    A speaker in multiroom slave mode refuses volume, transport, preset and input, and
    nothing here put it there. Reading it as solo left the number dead on the bus with the
    panel drawing controls that only answer no, and nothing anywhere saying why.
    """
    gestor, numeros = duas
    assert numeros.escravos_alheios() == ()
    _caixa(gestor, "uuid-1").escravo_alheio = True
    assert numeros.escravos_alheios() == (1,)
    # A speaker the hub itself put in the group is not an alien slave: it is ours.
    _caixa(gestor, "uuid-2").escravo_alheio = True
    await numeros.aplicar(GRUPO, 1)
    assert 2 not in numeros.escravos_alheios()


async def test_remover_o_mestre_manda_o_ungroup_antes_de_reescrever_a_ordem(duas):
    """Only the master dismantles the group, and the books read the order to find
    it, so the order may not be rewritten first.

    Writing the new order first made the master unreachable, so multiroom:Ungroup never
    reached the wire: the speakers stayed physically grouped forever, playing together, while
    the hub published solo and the customer had no way to separate them.
    """
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    mestre = _caixa(gestor, "uuid-1")
    mestre.chamadas.clear()
    assert await numeros.esquecer("uuid-1") == ("", "uuid-2")
    assert ("desfazer_grupo", None) in mestre.chamadas, mestre.chamadas
    assert numeros.grupo() == 0
    assert _caixa(gestor, "uuid-2").marcas == [True, False]


async def test_um_ungroup_recusado_nao_esquece_o_grupo_e_a_repeticao_tenta_de_novo(duas):
    """A refused dismantle must leave the group in the books, or the retry is a silent no-op.

    Forgetting it tells the customer the speakers are apart while they are still playing
    together, and the second attempt then finds no group and answers ok without touching the
    wire.
    """
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    mestre = _caixa(gestor, "uuid-1")
    mestre.recusa = "eq_offline"
    assert await numeros.aplicar(GRUPO, 0) == "numero_offline"
    assert numeros.grupo() == 1, "the books forgot a group that is still physically up"
    assert numeros.valores()[GRUPO] == 1
    mestre.recusa = None
    mestre.chamadas.clear()
    assert await numeros.aplicar(GRUPO, 0) is None
    assert ("desfazer_grupo", None) in mestre.chamadas
    assert numeros.grupo() == 0


async def test_desligar_a_licenca_derruba_o_grupo_diga_o_que_disser_o_mestre(duas):
    """A licence leaving the installation has nothing left to retry with, so its group falls
    in the books even when the master refused.
    """
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    mestre = _caixa(gestor, "uuid-1")
    mestre.recusa = "eq_offline"
    mestre.chamadas.clear()
    await numeros.desligar()
    assert mestre.chamadas == [("desfazer_grupo", None)]
    assert numeros.grupo() == 0 and numeros.escravos() == ()
    assert _caixa(gestor, "uuid-2").marcas == [True, False]


async def test_um_escravo_largado_dos_livros_perde_a_marca_de_grupo(duas):
    """A number dropped from the books while its driver still believes it is grouped refuses
    transport and input forever, for a group nobody is in any more.
    """
    gestor, numeros = duas
    await numeros.aplicar(GRUPO, 1)
    escravo = _caixa(gestor, "uuid-2")
    assert escravo.marcas == [True]
    await numeros.esquecer("uuid-2")
    assert escravo.marcas == [True, False], escravo.marcas
    assert numeros.grupo() == 0


async def test_a_caixa_presa_em_grupo_alheio_e_convidada_a_sair_no_boot_e_no_sincronismo(
    duas, monkeypatch
):
    """A speaker held in a group this hub does not lead refuses volume, transport, preset and
    input, so leaving it there is leaving the number dead on the bus.

    Sanear only reconciled from the master outwards and sincronizar returned at once
    whenever our books said solo, which is exactly the case, so nothing ever asked the speaker
    to leave and every command of that number failed forever.
    """
    gestor, numeros = duas
    # The invitation to leave waits a minute between one ask and the next, and this test
    # is about WHO asks, so the wait is zero here and has a test of its own.
    monkeypatch.setattr(modulo, "ESPERA_DE_SAIDA_S", 0.0)
    caixa = _caixa(gestor, "uuid-1")
    caixa.escravo_alheio = True
    caixa.chamadas.clear()
    await numeros.sanear()
    assert ("desfazer_grupo", None) in caixa.chamadas, caixa.chamadas
    caixa.chamadas.clear()
    await numeros.sincronizar()
    assert ("desfazer_grupo", None) in caixa.chamadas, caixa.chamadas
    # A speaker that would not leave stays flagged instead of being published as solo.
    caixa.recusa = "eq_offline"
    await numeros.sincronizar()
    assert numeros.escravos_alheios() == (1,)


# The book of every licence of the installation.


# Defeito em producao: core/iphub/dpbus/numeros.py:1240
def test_o_livro_confia_na_ordem_do_config_so_ate_onde_ela_e_valida(misto):
    """The route validates an order and config.json does not, so a number the book refuses
    is left empty on boot instead of publishing a number nothing can command: an identity
    that is not registered, the same one twice, an equipment of the other product.
    """
    ordens = {
        "av1": ["uuid-1", "uuid-que-ninguem-cadastrou", "uuid-1", "ar-1", "uuid-p"],
        "ar1": ["uuid-tv", "ar-1"],
    }
    livro = Licencas(misto, (LICENCA_AV, LICENCA_AR), ordens)
    assert livro.de("av1").ordem == ("uuid-1", "", "", "", "uuid-p")
    assert livro.de("ar1").ordem == ("", "ar-1")
    assert livro.onde("ar-1") == ("ar1", 2)
    assert livro.onde("uuid-tv") is None


# Defeito em producao: core/iphub/dpbus/numeros.py:1240
def test_o_livro_apara_uma_ordem_mais_longa_que_o_produto_e_esvazia_a_repetida(misto):
    ordens = {"av1": ["uuid-1", *[""] * 11, "uuid-p"], "ar1": [*[""] * 8, "ar-1"]}
    livro = Licencas(misto, (LICENCA_AV, LICENCA_AR), ordens)
    assert livro.de("av1").ordem == ("uuid-1", *[""] * 11)
    assert livro.de("ar1").ordem == tuple([""] * 8)
    assert livro.onde("uuid-p") is None and livro.onde("ar-1") is None


# Defeito em producao: core/iphub/dpbus/numeros.py:1240
def test_uma_identidade_em_duas_licencas_fica_so_na_primeira(duas):
    gestor, _ = duas
    ordens = {"av1": ["uuid-1"], "av2": ["uuid-1", "uuid-2"]}
    livro = Licencas(gestor, (LICENCA_AV, OUTRA_LICENCA_AV), ordens)
    assert livro.de("av1").ordem == ("uuid-1",)
    assert livro.de("av2").ordem == ("", "uuid-2")
    assert livro.onde("uuid-1") == ("av1", 1) and livro.onde("uuid-2") == ("av2", 2)


# Defeito em producao: core/iphub/dpbus/numeros.py:1240
def test_o_livro_sem_numeros_sobe_com_toda_licenca_vazia(duas):
    """A book with no order at all boots with every licence empty, and one with no licence
    boots empty; both are ordinary states of a hub,.
    """
    gestor, _ = duas
    assert Licencas(gestor).ids() == ()
    livro = Licencas(gestor, (LICENCA_AV, LICENCA_AR))
    assert livro.ids() == ("av1", "ar1")
    assert livro.numeros() == {"av1": (), "ar1": ()}
    assert [numeros.id for numeros in livro.todas()] == ["av1", "ar1"]
    assert livro.produto_de("av1") == "av" and livro.produto_de("ar1") == "ar"
    assert livro.produto_de("x") is None and livro.de(5) is None and livro.de(None) is None


async def test_onde_e_numeros_dizem_a_licenca_e_o_numero_de_cada_equipamento(misto):
    ordens = {"av1": ("uuid-1", "", "uuid-p"), "ar1": ("ar-1",)}
    livro = await _livro(misto, (LICENCA_AV, LICENCA_AR), ordens)
    assert livro.ids() == ("av1", "ar1")
    assert [numeros.id for numeros in livro.todas()] == ["av1", "ar1"]
    assert livro.produto_de("av1") == "av" and livro.produto_de("ar1") == "ar"
    assert livro.produto_de("x") is None and livro.de(5) is None and livro.de(None) is None
    assert livro.onde("uuid-1") == ("av1", 1)
    assert livro.onde("uuid-p") == ("av1", 3)
    assert livro.onde("ar-1") == ("ar1", 1)
    assert livro.onde("uuid-tv") is None and livro.onde("") is None
    assert livro.numeros() == {"av1": ("uuid-1", "", "uuid-p"), "ar1": ("ar-1",)}


async def test_adicionar_e_trocar_uma_licenca(duas):
    gestor, _ = duas
    livro = await _livro(gestor, (LICENCA_AV,), {"av1": ("uuid-1",)})
    nova = livro.adicionar(OUTRA_LICENCA_AV)
    assert nova.id == "av2" and nova.ordem == () and livro.ids() == ("av1", "av2")
    with pytest.raises(ValueError):
        livro.adicionar(Licenca(id="av1", produto="av"))
    livro.trocar(Licenca(id="av1", produto="av", nome="Casa", uuid="u", pid="p", chave="k"))
    assert livro.de("av1").licenca.nome == "Casa"
    assert livro.de("av1").ordem == ("uuid-1",)
    # The product of a licence never changes, because its numbers would change meaning.
    with pytest.raises(ValueError):
        livro.trocar(Licenca(id="av1", produto="ar"))
    with pytest.raises(KeyError):
        livro.trocar(Licenca(id="nao-existe", produto="av"))


async def test_remover_uma_licenca_derruba_o_grupo_dela_mesmo_com_o_mestre_recusando(duas):
    """The licence leaves with its numbers and its group; the equipment stays registered."""
    gestor, _ = duas
    livro = await _livro(gestor, (LICENCA_AV, LICENCA_AR), {"av1": ("uuid-1", "uuid-2")})
    assert await livro.aplicar("av1", GRUPO, 1) is None
    mestre = _caixa(gestor, "uuid-1")
    mestre.recusa = "eq_offline"
    mestre.chamadas.clear()
    await livro.remover("av1")
    assert mestre.chamadas == [("desfazer_grupo", None)]
    assert _caixa(gestor, "uuid-2").marcas == [True, False]
    assert livro.de("av1") is None and livro.ids() == ("ar1",)
    assert livro.numeros() == {"ar1": ()}
    assert gestor.cadastros == (gestor._cadastros["uuid-1"], gestor._cadastros["uuid-2"])
    await livro.remover("nao-existe")


async def test_esquecer_no_livro_esvazia_a_vaga_na_licenca_que_a_segurava(misto):
    ordens = {"av1": ("uuid-1", "uuid-p"), "ar1": ("ar-1",)}
    livro = await _livro(misto, (LICENCA_AV, LICENCA_AR), ordens)
    assert await livro.esquecer("uuid-1") == {"av1": ("", "uuid-p"), "ar1": ("ar-1",)}
    assert await livro.esquecer("uuid-que-ninguem-cadastrou") == {
        "av1": ("", "uuid-p"),
        "ar1": ("ar-1",),
    }
    assert livro.onde("uuid-1") is None and livro.onde("uuid-p") == ("av1", 2)


async def test_definir_ordem_pelo_livro_julga_as_outras_licencas(duas):
    gestor, _ = duas
    livro = await _livro(gestor, (LICENCA_AV, OUTRA_LICENCA_AV), {"av1": ("uuid-1",)})
    with pytest.raises(OrdemInvalida) as erro:
        await livro.definir_ordem("av2", ["uuid-1"])
    assert erro.value.codigo == "numero_ocupado"
    assert await livro.definir_ordem("av2", ["", "uuid-2"]) == ("", "uuid-2")
    assert livro.numeros() == {"av1": ("uuid-1",), "av2": ("", "uuid-2")}
    with pytest.raises(KeyError):
        await livro.definir_ordem("nao-existe", ["uuid-2"])


async def test_o_livro_aplica_e_le_por_licenca_e_recusa_uma_desconhecida(misto):
    ordens = {"av1": ("uuid-1", "uuid-p"), "ar1": ("ar-1",)}
    livro = await _livro(misto, (LICENCA_AV, LICENCA_AR), ordens)
    _caixa(misto, "ar-1").estado_de_teste(ligado=True, temperatura=22)
    assert await livro.aplicar("av1", LIGADO_2, True) is None
    assert await livro.aplicar("ar1", AR_TEMPERATURA_1, 25) is None
    assert _chamadas(misto, "uuid-p") == [("ligar", None)]
    assert _chamadas(misto, "ar-1") == [("temperatura", 25)]
    assert await livro.aplicar("desconhecida", NIVEL_1, 30) == "licenca_desconhecida"
    assert await livro.aplicar(None, NIVEL_1, 30) == "licenca_desconhecida"
    assert livro.valores("av1")[ONLINE] == 0b11
    assert livro.valores("ar1")[AR_TEMPERATURA_1] == 22
    assert livro.valores("desconhecida") == {}


async def test_acionar_pelo_livro_vai_pela_licenca_que_segura_o_equipamento(misto):
    """A scene reaches an equipment through the licence that holds it, and straight through
    the gestor when none does; the group is only ever an action of a licence.
    """
    misto._cadastros["uuid-2"] = _cadastro("uuid-2", ip=IP_2, nome="Cozinha")
    misto._drivers["uuid-2"] = _fabrica()(misto._cadastros["uuid-2"])
    livro = await _livro(misto, (LICENCA_AV, LICENCA_AR), {"av1": ("uuid-1", "uuid-2")})
    assert await livro.acionar("uuid-2", "grupo", "uuid-1") is None
    assert livro.de("av1").grupo() == 1
    assert await livro.acionar("uuid-2", "volume", 42) is None
    assert _chamadas(misto, "uuid-1")[-1] == ("volume_de_escravo", (IP_2, 42))
    assert await livro.acionar("uuid-tv", "tecla", "ok") is None
    assert _chamadas(misto, "uuid-tv") == [("tecla", "ok")]
    assert await livro.acionar("uuid-tv", "grupo", "uuid-tv") == "nao_suportado"
    assert await livro.acionar("uuid-tv", "volume", 30) == "nao_suportado"
    _caixa(misto, "uuid-p").recusa = "eq_offline"
    assert await livro.acionar("uuid-p", "ligar", None) == "numero_offline"
    assert await livro.acionar("uuid-que-ninguem-cadastrou", "ligar", None) == "numero_offline"


async def test_o_livro_saneia_sincroniza_e_rele_toda_licenca(misto):
    misto._cadastros["uuid-2"] = _cadastro("uuid-2", ip=IP_2, nome="Cozinha")
    misto._drivers["uuid-2"] = _fabrica()(misto._cadastros["uuid-2"])
    ordens = {"av1": ("uuid-1", "uuid-2"), "ar1": ("ar-1",)}
    livro = await _livro(misto, (LICENCA_AV, LICENCA_AR), ordens)
    _caixa(misto, "uuid-1").grupo = _Grupo(escravos=({"identidade": "uuid-2"},))
    await livro.sanear()
    assert _chamadas(misto, "uuid-1") == [("ler_grupo", None), ("desfazer_grupo", None)]
    assert _chamadas(misto, "ar-1") == []
    assert await livro.aplicar("av1", GRUPO, 1) is None
    _caixa(misto, "uuid-1").estado_de_teste(tocando=MUSICA)
    await livro.sincronizar()
    assert _caixa(misto, "uuid-2").espelho == MUSICA
    await livro.reler("av1", NIVEL_2, 30)
    await livro.reler("ar1", AR_MODO_1, "frio")
    await livro.reler("desconhecida", NIVEL_1, 30)
    await livro.reler(None, NIVEL_1, 30)
    assert misto.visitas == ["uuid-2", "ar-1"]


async def test_o_convite_a_sair_espera_um_minuto_entre_um_pedido_e_o_seguinte(duas):
    """A slave held in a foreign group that ignores the invitation is asked again a minute
    later and not on every tick, because makes Ungroup a command of the master
    and a request a second is a flood on a speaker that will not obey.
    """
    gestor, numeros = duas
    caixa = _caixa(gestor, "uuid-1")
    caixa.escravo_alheio = True
    caixa.recusa = "eq_offline"
    caixa.chamadas.clear()
    await numeros.sincronizar()
    await numeros.sincronizar()
    await numeros.sincronizar()
    assert caixa.chamadas.count(("desfazer_grupo", None)) == 1


async def test_trocar_de_mestre_com_o_mestre_antigo_mudo_recusa_e_mantem_os_livros(duas):
    """The old master refused to dismantle its group, so its slave still plays its audio; a
    second group formed over it would leave the first with nobody in the books to take it
    down, so the change of master answers the refusal and changes nothing.
    """
    gestor, numeros = duas
    assert await numeros.aplicar(GRUPO, 1) is None
    assert numeros.grupo() == 1
    _caixa(gestor, "uuid-1").recusa = "eq_offline"
    _chamadas(gestor, "uuid-1").clear()
    assert await numeros.aplicar(GRUPO, 2) == "numero_offline"
    assert numeros.grupo() == 1
    assert numeros.escravos() == (2,)
    assert ("entrar_no_grupo", IP_2) not in _chamadas(gestor, "uuid-1")


async def test_refazer_o_grupo_mantem_o_escravo_que_ainda_e_escravo_e_solta_o_que_saiu(tres):
    """A morning scene re-forms the group it formed yesterday; a member whose new invitation
    got lost is still a slave when the speaker says so and stays in the books, and one that
    really left has its mark cleared instead of being evicted for a lost reply.
    """
    gestor, numeros = tres
    assert await numeros.aplicar(GRUPO, 1) is None
    assert numeros.escravos() == (2, 3)
    ainda = _caixa(gestor, "uuid-2")
    ainda.recusa = "eq_offline"
    ainda.escravo_alheio = True
    saiu = _caixa(gestor, "uuid-3")
    saiu.recusa = "eq_offline"
    saiu.marcas.clear()
    assert await numeros.aplicar(GRUPO, 1) is None
    assert numeros.grupo() == 1
    assert 2 in numeros.escravos()
    assert 3 not in numeros.escravos()
    assert saiu.marcas == [False]


async def test_perfis_cabem_julga_pelo_tipo_novo_do_cadastro():
    """A registration edited to another tipo is judged with the manifest of that tipo, so a
    projector that turns into a receiver with ten inputs is refused before it is written
    and never pushes the licence out of its strings on the next report.
    """
    pesados = tuple(_pesado(f"uuid-{n}", ip=f"192.0.2.{n}") for n in range(1, 6))
    projetor = _cadastro(
        "uuid-p",
        tipo=TIPO_DE_PROJETOR,
        ip="192.0.2.20",
        nome=NOME_PESADO,
        listas={"entradas": ENTRADAS_PESADAS, "atalhos": ATALHOS_PESADOS, "modos": MODOS_PESADOS},
    )
    gestor = GestorFalso(
        {
            TIPO: _fabrica(),
            TIPO_DE_PROJETOR: _projetor(("ligar", "desligar")),
            TIPO_DE_RECEIVER: _receiver(),
        },
        (*pesados, projetor),
    )
    ordem = (*(cadastro.identidade for cadastro in pesados), "uuid-p")
    livro = await _livro(gestor, (LICENCA_AV,), {"av1": ordem})
    assert livro.perfis_cabem(projetor)
    receiver = _cadastro(
        "uuid-p",
        tipo=TIPO_DE_RECEIVER,
        ip="192.0.2.20",
        nome=NOME_PESADO,
        listas={"entradas": ENTRADAS_PESADAS, "atalhos": ATALHOS_PESADOS, "modos": MODOS_PESADOS},
    )
    assert not livro.perfis_cabem(receiver)


async def test_o_grupo_leva_os_membros_escolhidos_e_nao_a_licenca_inteira(tres):
    """A master carries up to seven slaves and the customer picks them one by one,
    so a group formed with a chosen set invites exactly that set and nobody else.
    """
    gestor, numeros = tres
    assert await numeros.formar(1, [3]) is None
    assert numeros.grupo() == 1
    assert numeros.escravos() == (3,)
    assert _chamadas(gestor, "uuid-3") == [("entrar_no_grupo", IP_1)]
    assert _chamadas(gestor, "uuid-2") == []


async def test_um_membro_tirado_do_grupo_sai_pelo_mestre_e_o_resto_segue_tocando(tres):
    """Taking one member out is a move on the master and never the Ungroup of everybody: the
    ones that stay never hear a gap.
    """
    gestor, numeros = tres
    assert await numeros.formar(1) is None
    assert numeros.escravos() == (2, 3)
    _caixa(gestor, "uuid-1").chamadas.clear()
    assert await numeros.formar(1, [2]) is None
    assert numeros.escravos() == (2,)
    assert ("tirar_do_grupo", IP_3) in _chamadas(gestor, "uuid-1")
    assert ("desfazer_grupo", None) not in _chamadas(gestor, "uuid-1")
    assert _caixa(gestor, "uuid-3").marcas[-1] is False
    # The last member leaving is a group of one, which is no group at all.
    assert await numeros.formar(1, []) is None
    assert numeros.grupo() == 0
    assert ("desfazer_grupo", None) in _chamadas(gestor, "uuid-1")


async def test_um_membro_que_o_mestre_recusa_tirar_continua_nos_livros(tres):
    """A member the master refused to take out is still physically playing the audio of the
    group, so forgetting it here would draw it as solo while it follows a master.
    """
    gestor, numeros = tres
    assert await numeros.formar(1) is None
    _caixa(gestor, "uuid-1").recusa = "erro_aparelho"
    assert await numeros.formar(1, [2]) == "erro_aparelho"
    assert 3 in numeros.escravos()


async def test_um_membro_fora_da_licenca_ou_o_proprio_mestre_e_valor_invalido(tres):
    """A number that is not a companion is an empty slot, another tipo or the master itself."""
    _, numeros = tres
    assert await numeros.formar(1, [1]) == "valor_invalido"
    assert await numeros.formar(1, [9]) == "valor_invalido"
    assert await numeros.formar(0, [2]) is None
    assert await numeros.formar("1", [2]) == "valor_invalido"
    assert numeros.grupo() == 0


async def test_uma_cena_monta_o_grupo_um_membro_por_passo(tres):
    """The customer picks the members one by one, so a scene picks them one step
    at a time, and a step that names the master with no value takes the whole group down.
    """
    gestor, numeros = tres
    assert await numeros.acionar("uuid-2", "grupo", "uuid-1") is None
    assert numeros.escravos() == (2,)
    assert await numeros.acionar("uuid-3", "grupo", "uuid-1") is None
    assert numeros.escravos() == (2, 3)
    # A member leaves alone and the group keeps playing without it.
    assert await numeros.acionar("uuid-2", "grupo", "") is None
    assert numeros.escravos() == (3,)
    assert numeros.grupo() == 1
    # The master leaving takes everybody with it.
    assert await numeros.acionar("uuid-1", "grupo", "") is None
    assert numeros.grupo() == 0
    # Nobody joins itself.
    assert await numeros.acionar("uuid-1", "grupo", "uuid-1") == "valor_invalido"

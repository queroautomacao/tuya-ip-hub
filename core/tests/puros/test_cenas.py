# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""A scene under attack: data and never program, and one failed step never ends a scene.

The refusals are a table of attacks, each one a file somebody could save, so a code the
vocabulary carries and nobody attacks fails the suite instead of being translated in the
panel for a case that never happens. The rules being attacked are the ones of section 8 (a
step is one action of section 6 on one registered equipment, thirty two scenes whose
position is the number, the 255 BYTES of each of the two name data points) and the rule
section 7 fixes for a driver and holds for a scene: no condition, no loop, no expression, no
action that runs a scene.

The execution drives an injected sleep, so the order and the waits are read without spending
a second of them, and the numbers module a scene drives is a double that records what it was
told and answers what the test decided.

Uma cena sob ataque: dado e nunca programa, e um passo que falha nunca encerra uma cena.

As recusas são uma tabela de ataques, cada um um arquivo que alguém poderia salvar, então um
código que o vocabulário carrega e ninguém ataca quebra a suíte em vez de ser traduzido no
painel para um caso que nunca ocorre. As regras atacadas são as da seção 8 (um passo é uma
ação da seção 6 num equipamento cadastrado, trinta e duas cenas cuja posição é o número, os
255 BYTES de cada um dos dois data points de nomes) e a regra que a seção 7 fixa para um
driver e vale para uma cena: sem condicional, sem laço, sem expressão, sem ação que dispara
uma cena.

A execução dirige um sono injetado, então a ordem e as esperas são lidas sem gastar um segundo
delas, e o módulo de números que uma cena dirige é um dublê que registra o que mandaram nele e
responde o que o teste decidiu.
"""

import asyncio
import json
import logging
from collections.abc import Collection, Sequence
from pathlib import Path

import pytest

from iphub import config
from iphub.cenas import (
    ACOES,
    CENA_ACAO_DESCONHECIDA,
    CENA_CHAVE_DESCONHECIDA,
    CENA_EM_CURSO,
    CENA_EQUIPAMENTO_DESCONHECIDO,
    CENA_EQUIPAMENTO_INVALIDO,
    CENA_ESPERA_INVALIDA,
    CENA_INTERVALO_INVALIDO,
    CENA_NAO_ENCONTRADA,
    CENA_NAO_OBJETO,
    CENA_NOME_INVALIDO,
    CENA_PASSO_NAO_OBJETO,
    CENA_PASSOS_DEMAIS,
    CENA_PASSOS_INVALIDOS,
    CENA_VALOR_INVALIDO,
    CENAS_DEMAIS,
    CENAS_NAO_LISTA,
    CODIGOS,
    CODIGOS_DE_EXECUCAO,
    COM_TEXTO,
    ESPERA_MAXIMA_MS,
    IDENTIDADE_MAXIMA,
    INTERVALO_PADRAO_MS,
    MAXIMO,
    NOME_MAXIMO,
    PASSOS_MAXIMOS,
    SEM_VALOR,
    VALOR_TEXTO_MAXIMO,
    Cena,
    CenasInvalidas,
    Executor,
    Passo,
    numero_de,
    validar,
    valor_valido,
)
from iphub.dpbus import mapa
from iphub.dpbus.protocolo import NUMERO_OFFLINE
from iphub.drivers.manifesto import CAPACIDADES, TECLAS, VENTOS
from iphub.versao import SCHEMA_VERSION


# Why: something outside Exception, which is what a device library raising SystemExit deep
# inside a socket call looks like from the step that called it.
# Por que: algo fora de Exception, que é o que uma biblioteca de aparelho estourando SystemExit
# no fundo de uma chamada de socket parece do passo que a chamou.
class Explosao(BaseException):
    pass


VOLTAS = 200

# Three registered pieces of equipment, named by identity and never by IP, section 6.
# Três equipamentos cadastrados, nomeados pela identidade e nunca pelo IP, seção 6.
CAIXA = "uuid-caixa-da-sala"
PROJETOR = "uuid-projetor"
RECEIVER = "mac-receiver-00-11-22"
IDENTIDADES = (CAIXA, PROJETOR, RECEIVER)
DESCONHECIDO = "uuid-que-ninguem-cadastrou"

NO_EQUIPAMENTO = "cenas[0].passos[0].equipamento"
NA_ACAO = "cenas[0].passos[0].acao"
NO_VALOR = "cenas[0].passos[0].valor"
NA_ESPERA = "cenas[0].passos[0].espera_ms"


def _passo(**campos: object) -> dict:
    return {"equipamento": CAIXA, "acao": "volume", "valor": 30, **campos}


def _ligar(equipamento: str = PROJETOR, **campos: object) -> dict:
    return {"equipamento": equipamento, "acao": "ligar", "valor": None, **campos}


def _fonte(equipamento: str = RECEIVER, valor: str = "HDMI1", **campos: object) -> dict:
    return {"equipamento": equipamento, "acao": "fonte", "valor": valor, **campos}


def _sem(passo: dict, chave: str) -> dict:
    return {nome: valor for nome, valor in passo.items() if nome != chave}


def _cena(**campos: object) -> dict:
    return {"nome": "Noite", "passos": [_passo()], **campos}


def _problemas(dados: object, identidades: Collection[str] | None = None) -> set[tuple[str, str]]:
    with pytest.raises(CenasInvalidas) as erro:
        validar(dados, identidades)
    return set(erro.value.problemas)


def _uma(passos: Sequence[dict], nome: str = "Noite") -> tuple[Cena, ...]:
    return validar([{"nome": nome, "passos": list(passos)}])


def _nomes_no_limite() -> list[str]:
    """Sixteen names whose JSON is exactly the 255 bytes of one name data point.

    Dezesseis nomes cujo JSON tem exatamente os 255 bytes de um data point de nomes.
    """
    return ["n" * 13] * 8 + ["n" * 12] * 8


class Acionador:
    """The numbers module a scene drives, as a double: it records, it refuses and it blocks
    on command.

    O módulo de números que uma cena dirige, como dublê: ele registra, recusa e trava sob
    comando.
    """

    def __init__(self) -> None:
        self.chamadas: list[tuple[str, str, object]] = []
        self.respostas: dict[str, str] = {}
        self.explosoes: dict[str, BaseException] = {}
        self.travar_em = ""
        self.liberar = asyncio.Event()

    async def __call__(self, identidade: str, acao: str, valor: object) -> str | None:
        self.chamadas.append((identidade, acao, valor))
        if identidade == self.travar_em:
            await self.liberar.wait()
        erro = self.explosoes.get(identidade)
        if erro is not None:
            raise erro
        return self.respostas.get(identidade)


class Sono:
    """A sleep the test reads instead of spending: it records the wait and yields.

    Um sono que o teste lê em vez de gastar: ele registra a espera e cede a vez.
    """

    def __init__(self) -> None:
        self.esperas: list[float] = []

    async def __call__(self, segundos: float) -> None:
        self.esperas.append(segundos)
        await asyncio.sleep(0)


@pytest.fixture
def acionador() -> Acionador:
    return Acionador()


@pytest.fixture
def sono() -> Sono:
    return Sono()


async def _terminar(executor: Executor, numero: int) -> None:
    for _ in range(VOLTAS):
        if not executor.em_curso(numero):
            return
        await asyncio.sleep(0)
    raise AssertionError(f"scene {numero} never finished")


ATAQUES = (
    (
        "a lista de cenas que nao e lista",
        {"cena1": []},
        {("cenas", CENAS_NAO_LISTA)},
    ),
    (
        "trinta e tres cenas, e a secao 8 numera trinta e duas",
        [_cena() for _ in range(MAXIMO + 1)],
        {("cenas", CENAS_DEMAIS)},
    ),
    (
        "uma cena que nao e objeto",
        [42],
        {("cenas[0]", CENA_NAO_OBJETO)},
    ),
    (
        "chave que ninguem le na cena",
        [_cena(quando="18:00")],
        {("cenas[0].quando", CENA_CHAVE_DESCONHECIDA)},
    ),
    (
        "chave que ninguem le no passo",
        [_cena(passos=[_passo(repete=3)])],
        {("cenas[0].passos[0].repete", CENA_CHAVE_DESCONHECIDA)},
    ),
    (
        "nome que nao e texto",
        [_cena(nome=7)],
        {("cenas[0].nome", CENA_NOME_INVALIDO)},
    ),
    (
        "nome maior que o teto",
        [_cena(nome="n" * (NOME_MAXIMO + 1))],
        {("cenas[0].nome", CENA_NOME_INVALIDO)},
    ),
    (
        "nome com caractere de controle",
        [_cena(nome="Noite\n")],
        {("cenas[0].nome", CENA_NOME_INVALIDO)},
    ),
    (
        "passos que nao sao lista",
        [_cena(passos={"acao": "ligar"})],
        {("cenas[0].passos", CENA_PASSOS_INVALIDOS)},
    ),
    (
        "passos demais, um arquivo que virou programa",
        [_cena(passos=[_passo() for _ in range(PASSOS_MAXIMOS + 1)])],
        {("cenas[0].passos", CENA_PASSOS_DEMAIS)},
    ),
    (
        "um passo que nao e objeto",
        [_cena(passos=["ligar tudo"])],
        {("cenas[0].passos[0]", CENA_PASSO_NAO_OBJETO)},
    ),
    (
        "passo sem equipamento",
        [_cena(passos=[_sem(_passo(), "equipamento")])],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_INVALIDO)},
    ),
    (
        "equipamento que nao e texto",
        [_cena(passos=[_passo(equipamento=7)])],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_INVALIDO)},
    ),
    (
        "equipamento vazio",
        [_cena(passos=[_passo(equipamento="")])],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_INVALIDO)},
    ),
    (
        "equipamento maior que o teto de uma identidade",
        [_cena(passos=[_passo(equipamento="u" * (IDENTIDADE_MAXIMA + 1))])],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_INVALIDO)},
    ),
    (
        "equipamento com caractere de controle",
        [_cena(passos=[_passo(equipamento="uuid\n")])],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_INVALIDO)},
    ),
    (
        "equipamento com surrogado solto, que o utf-8 nao escreve",
        [_cena(passos=[_passo(equipamento="\ud800")])],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_INVALIDO)},
    ),
    (
        "passo sem acao",
        [_cena(passos=[_sem(_passo(), "acao")])],
        {(NA_ACAO, CENA_ACAO_DESCONHECIDA)},
    ),
    (
        "acao que nao e texto",
        [_cena(passos=[_passo(acao=7)])],
        {(NA_ACAO, CENA_ACAO_DESCONHECIDA)},
    ),
    (
        "acao fora do vocabulario da secao 6",
        [_cena(passos=[_passo(acao="piscar")])],
        {(NA_ACAO, CENA_ACAO_DESCONHECIDA)},
    ),
    (
        "acao escrita em maiusculas",
        [_cena(passos=[_passo(acao="VOLUME")])],
        {(NA_ACAO, CENA_ACAO_DESCONHECIDA)},
    ),
    (
        "agrupar, que e capacidade de manifesto e nao acao de cena",
        [_cena(passos=[_passo(acao="agrupar", valor=None)])],
        {(NA_ACAO, CENA_ACAO_DESCONHECIDA)},
    ),
    (
        "cena que dispara uma cena, o laco escrito em dado",
        [_cena(passos=[_passo(acao="cena", valor=2)])],
        {(NA_ACAO, CENA_ACAO_DESCONHECIDA)},
    ),
    (
        "ligar com valor",
        [_cena(passos=[_passo(acao="ligar", valor=True)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "tocar com valor",
        [_cena(passos=[_passo(acao="tocar", valor="Radio 1")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "volume acima de 100",
        [_cena(passos=[_passo(valor=101)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "volume negativo",
        [_cena(passos=[_passo(valor=-1)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "volume que e o true do json",
        [_cena(passos=[_passo(valor=True)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "volume escrito como texto",
        [_cena(passos=[_passo(valor="30")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "volume com casa decimal",
        [_cena(passos=[_passo(valor=30.0)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "volume sem valor",
        [_cena(passos=[_sem(_passo(), "valor")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "temperatura abaixo de 16 graus",
        [_cena(passos=[_passo(acao="temperatura", valor=15)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "temperatura acima de 30 graus",
        [_cena(passos=[_passo(acao="temperatura", valor=31)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "temperatura com casa decimal",
        [_cena(passos=[_passo(acao="temperatura", valor=22.0)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "mudo que nao e booleano",
        [_cena(passos=[_passo(acao="mudo", valor=1)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "mudo escrito como texto",
        [_cena(passos=[_passo(acao="mudo", valor="on")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "mudo sem valor",
        [_cena(passos=[_passo(acao="mudo", valor=None)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "tecla fora do vocabulario da secao 6",
        [_cena(passos=[_passo(acao="tecla", valor="power")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "tecla que nao e texto",
        [_cena(passos=[_passo(acao="tecla", valor=1)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "vento fora do vocabulario da secao 6",
        [_cena(passos=[_passo(acao="vento", valor="turbo")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "grupo que nao e texto",
        [_cena(passos=[_passo(acao="grupo", valor=7)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "grupo sem valor, que nao e o solo",
        [_cena(passos=[_passo(acao="grupo", valor=None)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "grupo com identidade maior que o teto",
        [_cena(passos=[_passo(acao="grupo", valor="u" * (IDENTIDADE_MAXIMA + 1))])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "grupo com caractere de controle",
        [_cena(passos=[_passo(acao="grupo", valor="uuid\r")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "fonte vazia",
        [_cena(passos=[_passo(acao="fonte", valor="")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "fonte que nao e texto",
        [_cena(passos=[_passo(acao="fonte", valor=1)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "fonte maior que o teto de um texto",
        [_cena(passos=[_passo(acao="fonte", valor="h" * (VALOR_TEXTO_MAXIMO + 1))])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "fonte com caractere de controle",
        [_cena(passos=[_passo(acao="fonte", valor="wifi\r")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "fonte com surrogado solto, que o utf-8 nao escreve",
        [_cena(passos=[_passo(acao="fonte", valor="\ud800")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "atalho sem valor",
        [_cena(passos=[_passo(acao="atalho", valor=None)])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "comando extra vazio",
        [_cena(passos=[_passo(acao="comando_extra", valor="")])],
        {(NO_VALOR, CENA_VALOR_INVALIDO)},
    ),
    (
        "espera negativa",
        [_cena(passos=[_passo(espera_ms=-1)])],
        {(NA_ESPERA, CENA_ESPERA_INVALIDA)},
    ),
    (
        "espera acima do teto",
        [_cena(passos=[_passo(espera_ms=ESPERA_MAXIMA_MS + 1)])],
        {(NA_ESPERA, CENA_ESPERA_INVALIDA)},
    ),
    (
        "espera que e o true do json",
        [_cena(passos=[_passo(espera_ms=True)])],
        {(NA_ESPERA, CENA_ESPERA_INVALIDA)},
    ),
    (
        "espera escrita como texto",
        [_cena(passos=[_passo(espera_ms="500")])],
        {(NA_ESPERA, CENA_ESPERA_INVALIDA)},
    ),
    (
        "intervalo negativo",
        [_cena(intervalo_ms=-1)],
        {("cenas[0].intervalo_ms", CENA_INTERVALO_INVALIDO)},
    ),
    (
        "intervalo acima do teto",
        [_cena(intervalo_ms=ESPERA_MAXIMA_MS + 1)],
        {("cenas[0].intervalo_ms", CENA_INTERVALO_INVALIDO)},
    ),
    (
        "intervalo que e o true do json",
        [_cena(intervalo_ms=True)],
        {("cenas[0].intervalo_ms", CENA_INTERVALO_INVALIDO)},
    ),
    (
        "dezesseis nomes que nao cabem nos 255 bytes de uma string de nomes",
        [_cena(nome="n" * 20) for _ in range(mapa.NOMES_POR_DP)],
        {("cenas", mapa.NOMES_LONGOS)},
    ),
    (
        "nomes que so estouram na segunda string, a das cenas 17 a 32",
        [_cena(nome="") for _ in range(mapa.NOMES_POR_DP)]
        + [_cena(nome="n" * 20) for _ in range(mapa.NOMES_POR_DP)],
        {("cenas", mapa.NOMES_LONGOS)},
    ),
    (
        "nome com surrogado solto, que a string de nomes nao sabe escrever",
        [_cena(nome="\ud800")],
        {("cenas", mapa.NOME_NAO_GRAVAVEL)},
    ),
)

# The attacks that need a registry: the route passes the identities it has, the boot does not.
# Os ataques que precisam de cadastro: a rota passa as identidades que tem, o boot não.
ATAQUES_COM_CADASTRO = (
    (
        "equipamento que ninguem cadastrou",
        IDENTIDADES,
        [_cena(passos=[_passo(equipamento=DESCONHECIDO)])],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_DESCONHECIDO)},
    ),
    (
        "cadastro vazio, entao todo equipamento e desconhecido",
        (),
        [_cena()],
        {(NO_EQUIPAMENTO, CENA_EQUIPAMENTO_DESCONHECIDO)},
    ),
    (
        "o segundo passo nomeia o desconhecido e so ele e recusado",
        IDENTIDADES,
        [_cena(passos=[_ligar(), _fonte(equipamento=DESCONHECIDO)])],
        {("cenas[0].passos[1].equipamento", CENA_EQUIPAMENTO_DESCONHECIDO)},
    ),
)


@pytest.mark.parametrize(("rotulo", "dados", "esperado"), ATAQUES, ids=[a[0] for a in ATAQUES])
def test_a_cena_quebrada_e_recusada_pelo_campo_e_nada_mais(rotulo, dados, esperado):
    assert _problemas(dados) == esperado
    # Why: a registry that knows every identity a file names adds no problem of its own, so
    # the verdict of the route and the verdict of the boot are the same list.
    # Por que: um cadastro que conhece toda identidade que um arquivo nomeia não acrescenta
    # problema próprio, então o veredito da rota e o do boot são a mesma lista.
    assert _problemas(dados, IDENTIDADES) == esperado


@pytest.mark.parametrize(
    ("rotulo", "identidades", "dados", "esperado"),
    ATAQUES_COM_CADASTRO,
    ids=[a[0] for a in ATAQUES_COM_CADASTRO],
)
def test_o_equipamento_fora_do_cadastro_e_recusado_quando_ha_cadastro(
    rotulo, identidades, dados, esperado
):
    assert _problemas(dados, identidades) == esperado


def test_sem_cadastro_o_equipamento_e_julgado_quando_o_passo_roda():
    """The boot reads config.json with no registry at hand, and a registration erased by
    hand must not keep the whole file from loading.

    O boot lê o config.json sem cadastro à mão, e um cadastro apagado na mão não pode impedir
    o arquivo inteiro de carregar.
    """
    cenas = validar([_cena(passos=[_passo(equipamento=DESCONHECIDO)])])
    assert cenas[0].passos[0].equipamento == DESCONHECIDO


# defeito em producao: core/iphub/cenas.py:154
def test_todo_codigo_do_vocabulario_tem_um_ataque():
    """A code nobody attacks is a code the panel translates for a case that never happens.

    Um código que ninguém ataca é um código que o painel traduz para um caso que nunca ocorre.
    """
    atacados = {
        codigo for *_, esperado in (*ATAQUES, *ATAQUES_COM_CADASTRO) for _, codigo in esperado
    }
    assert atacados == set(CODIGOS)


def test_todo_codigo_e_estavel_e_unico():
    todos = (*CODIGOS, *CODIGOS_DE_EXECUCAO)
    assert len(set(todos)) == len(todos)
    assert all(codigo.startswith("cena") or codigo in mapa.CODIGOS_DE_NOMES for codigo in todos)
    assert all(codigo.replace("_", "").isalnum() for codigo in todos)


def test_uma_recusa_e_um_value_error():
    # Why: whoever refuses to boot on a broken file catches ValueError, and config turns this
    # into its own refusal; the subclass has to stay under it.
    # Por que: quem recusa o boot com arquivo quebrado captura ValueError, e o config
    # transforma isto na recusa dele; a subclasse precisa ficar debaixo dele.
    assert issubclass(CenasInvalidas, ValueError)


@pytest.mark.parametrize(
    "dados",
    [
        None,
        "cenas",
        7,
        b"[]",
        {"cenas": []},
        [None],
        [[]],
        [{"nome": None}],
        [{"passos": None}],
        [{"passos": [None]}],
        [{"passos": [{"equipamento": None, "acao": None, "valor": object()}]}],
        [{"passos": [_passo(valor=float("nan"))]}],
        [{7: 1}],
    ],
)
def test_validar_nunca_estoura_outra_coisa_alem_de_cenas_invalidas(dados):
    # Why: this judges a hand edited config.json on boot, and anything else leaving here
    # would take the boot of the appliance down with it.
    # Por que: isto julga um config.json editado na mão no boot, e qualquer outra coisa
    # saindo daqui levaria o boot do appliance junto.
    with pytest.raises(CenasInvalidas):
        validar(dados)


def test_a_lista_quebrada_responde_todo_problema_de_uma_vez():
    dados = [
        _cena(nome="x" * (NOME_MAXIMO + 1), passos=[_passo(acao="agrupar", valor=None)]),
        _cena(passos=[_passo(espera_ms=-5), _passo(acao="ligar", valor=True)]),
    ]
    assert _problemas(dados) == {
        ("cenas[0].nome", CENA_NOME_INVALIDO),
        ("cenas[0].passos[0].acao", CENA_ACAO_DESCONHECIDA),
        ("cenas[1].passos[0].espera_ms", CENA_ESPERA_INVALIDA),
        ("cenas[1].passos[1].valor", CENA_VALOR_INVALIDO),
    }


def test_a_cena_valida_vira_dado_tipado():
    cenas = _uma([_ligar(espera_ms=500), _fonte(), _passo(acao="grupo", valor="")])
    assert cenas == (
        Cena(
            nome="Noite",
            passos=(
                Passo(equipamento=PROJETOR, acao="ligar", valor=None, espera_ms=500),
                Passo(equipamento=RECEIVER, acao="fonte", valor="HDMI1", espera_ms=None),
                Passo(equipamento=CAIXA, acao="grupo", valor="", espera_ms=None),
            ),
            intervalo_ms=INTERVALO_PADRAO_MS,
        ),
    )


def test_uma_instalacao_sem_cena_e_uma_lista_vazia():
    assert validar([]) == ()
    assert validar([], IDENTIDADES) == ()


VALORES_ACEITOS = (
    ("ligar", None),
    ("desligar", None),
    ("tocar", None),
    ("pausar", None),
    ("proxima", None),
    ("anterior", None),
    ("volume", 0),
    ("volume", 100),
    ("temperatura", 16),
    ("temperatura", 30),
    ("mudo", True),
    ("mudo", False),
    ("tecla", "canal_mais"),
    ("tecla", "digito_9"),
    ("vento", "auto"),
    ("vento", "alto"),
    ("grupo", ""),
    ("grupo", CAIXA),
    ("fonte", "HDMI1"),
    ("atalho", "Rádio 1"),
    ("modo", "frio"),
    ("modo", "Cinema"),
    ("comando_extra", "x" * VALOR_TEXTO_MAXIMO),
)


@pytest.mark.parametrize(
    ("acao", "valor"),
    VALORES_ACEITOS,
    ids=[f"{acao}={valor!r}"[:32] for acao, valor in VALORES_ACEITOS],
)
def test_o_valor_de_um_passo_e_julgado_pela_acao(acao, valor):
    """Section 6 says what each action takes, and the value travels to the driver untouched.

    A seção 6 diz o que cada ação recebe, e o valor viaja para o driver sem retoque.
    """
    assert valor_valido(acao, valor)
    cenas = _uma([_passo(acao=acao, valor=valor)])
    assert cenas[0].passos[0] == Passo(equipamento=CAIXA, acao=acao, valor=valor)


def test_toda_acao_de_cena_tem_um_valor_aceito():
    # Why: an action that the table above never accepts would be a scene nobody can write.
    # Por que: uma ação que a tabela acima nunca aceita seria uma cena que ninguém escreve.
    assert {acao for acao, _ in VALORES_ACEITOS} == set(ACOES)


def test_as_acoes_de_uma_cena_sao_as_capacidades_da_secao_6_mais_o_grupo():
    """agrupar is what a manifest declares to say the equipment CAN group; the move itself
    is the grupo action, whose value is the identity of the leader or empty for solo.

    agrupar é o que um manifesto declara para dizer que o equipamento SABE agrupar; o
    movimento em si é a ação grupo, cujo valor é a identidade do líder ou vazio para o solo.
    """
    assert set(ACOES) == (set(CAPACIDADES) - {"agrupar"}) | {"grupo"}
    assert "agrupar" not in ACOES
    assert set(SEM_VALOR) | set(COM_TEXTO) < set(ACOES)
    assert not valor_valido("agrupar", None)
    assert not valor_valido("agrupar", CAIXA)
    assert not valor_valido("cena", 1)


def test_tecla_e_vento_falam_o_vocabulario_inteiro_da_secao_6():
    assert all(valor_valido("tecla", tecla) for tecla in TECLAS)
    assert all(valor_valido("vento", vento) for vento in VENTOS)


def test_os_tetos_sao_os_da_secao_8():
    assert MAXIMO == mapa.CENAS == 32
    assert PASSOS_MAXIMOS == 64


def test_trinta_e_duas_cenas_de_sessenta_e_quatro_passos_cabem():
    passos = [_passo() for _ in range(PASSOS_MAXIMOS)]
    cenas = validar([_cena(nome=f"C{n}", passos=passos) for n in range(1, MAXIMO + 1)])
    assert len(cenas) == MAXIMO
    assert all(len(cena.passos) == PASSOS_MAXIMOS for cena in cenas)


def test_a_posicao_de_uma_cena_e_o_numero_dela(acionador):
    """Section 8 numbers 1 to 32, so an erased scene empties its slot instead of pulling the
    next one back into a number the customer already automated.

    A seção 8 numera 1 a 32, então uma cena apagada esvazia a vaga dela em vez de puxar a
    seguinte para um número que o cliente já automatizou.
    """
    cenas = validar([{"nome": "", "passos": []}, _cena(nome="Jantar")])
    executor = Executor(cenas, acionador)
    assert executor.cenas == cenas
    assert executor.nomes() == ("", "Jantar")
    assert executor.executar(1) == CENA_NAO_ENCONTRADA
    assert executor.cena_de(2).nome == "Jantar"


def test_dezesseis_nomes_no_limite_exato_de_uma_string_de_nomes_passam():
    nomes = _nomes_no_limite()
    primeira, _ = mapa.nomes_das_cenas(nomes)
    assert len(primeira.encode("utf-8")) == mapa.TEXTO_MAXIMO_BYTES
    cenas = validar([_cena(nome=nome) for nome in nomes])
    assert [cena.nome for cena in cenas] == nomes


def test_o_teto_de_uma_string_de_nomes_e_de_bytes_e_nao_de_letras():
    """The platform fixes a string at 255 bytes, and an accented letter costs two of them;
    counting characters would publish a JSON the bridge cannot read.

    A plataforma fixa uma string em 255 bytes, e uma letra acentuada custa dois deles; contar
    caracteres publicaria um JSON que a ponte não consegue ler.
    """
    nomes = _nomes_no_limite()
    nomes[0] = "ç" + nomes[0][1:]
    assert len(nomes[0]) == 13
    assert _problemas([_cena(nome=nome) for nome in nomes]) == {("cenas", mapa.NOMES_LONGOS)}


def test_a_segunda_string_de_nomes_tem_o_mesmo_teto():
    """Scenes 17 to 32 travel on the second name data point, judged by the same 255 bytes.

    As cenas 17 a 32 viajam no segundo data point de nomes, julgado pelos mesmos 255 bytes.
    """
    nomes = [""] * mapa.NOMES_POR_DP + _nomes_no_limite()
    cenas = validar([_cena(nome=nome) for nome in nomes])
    assert len(cenas) == MAXIMO
    nomes[-1] = "ç" + nomes[-1][1:]
    assert _problemas([_cena(nome=nome) for nome in nomes]) == {("cenas", mapa.NOMES_LONGOS)}


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        (1, 1),
        (17, 17),
        (32, MAXIMO),
        (0, None),
        (33, None),
        (-1, None),
        (True, None),
        ("1", None),
        (1.0, None),
        (None, None),
        ("cena1", None),
    ],
)
def test_o_numero_da_cena_e_um_inteiro_de_um_a_trinta_e_dois(valor, esperado):
    assert numero_de(valor) == esperado


async def test_os_passos_correm_na_ordem_com_as_esperas_declaradas(acionador, sono):
    cenas = _uma([_ligar(espera_ms=500), _fonte(espera_ms=250), _passo()])
    executor = Executor(cenas, acionador, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert acionador.chamadas == [
        (PROJETOR, "ligar", None),
        (RECEIVER, "fonte", "HDMI1"),
        (CAIXA, "volume", 30),
    ]
    assert sono.esperas == [0.5, 0.25]


async def test_a_espera_do_ultimo_passo_nao_e_dormida(acionador, sono):
    executor = Executor(_uma([_passo(espera_ms=ESPERA_MAXIMA_MS)]), acionador, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert sono.esperas == []


async def test_um_passo_sem_espera_dorme_o_intervalo_da_cena(acionador, sono):
    """An AV device needs a moment between commands, so a step that names no wait sleeps the
    interval of the scene, one second unless the scene says otherwise; a wait of zero written
    on the step is an order and sleeps nothing.

    Um aparelho de AV precisa de um instante entre comandos, então um passo que não nomeia
    espera dorme o intervalo da cena, um segundo salvo a cena dizer outro; uma espera zero
    escrita no passo é ordem e não dorme nada.
    """
    passos = [_ligar(), _passo(espera_ms=0), _fonte()]
    cenas = validar([{"nome": "Filme", "passos": passos}])
    assert cenas[0].intervalo_ms == INTERVALO_PADRAO_MS == 1_000
    assert [passo.espera_ms for passo in cenas[0].passos] == [None, 0, None]
    executor = Executor(cenas, acionador, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert sono.esperas == [1.0]


async def test_o_intervalo_da_cena_e_editavel_e_zero_desliga_a_espera(acionador, sono):
    dois = [_ligar(), _passo()]
    cenas = validar(
        [
            {"nome": "Rapida", "intervalo_ms": 0, "passos": dois},
            {"nome": "Lenta", "intervalo_ms": 2_500, "passos": dois},
        ]
    )
    assert (cenas[0].intervalo_ms, cenas[1].intervalo_ms) == (0, 2_500)
    executor = Executor(cenas, acionador, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert sono.esperas == []
    assert executor.executar(2) is None
    await _terminar(executor, 2)
    assert sono.esperas == [2.5]


async def test_executar_responde_antes_de_o_primeiro_passo_ser_acionado(acionador, sono):
    """Fire and forget: whoever asked gets the answer at once, and a scene of ten seconds of
    waits does not hold the socket or the route that started it.

    Disparar e esquecer: quem pediu recebe a resposta na hora, e uma cena de dez segundos de
    espera não segura o socket nem a rota que a começou.
    """
    executor = Executor(_uma([_ligar(), _passo()]), acionador, dormir=sono)
    assert executor.executar(1) is None
    assert acionador.chamadas == []
    await _terminar(executor, 1)
    assert acionador.chamadas == [(PROJETOR, "ligar", None), (CAIXA, "volume", 30)]


async def test_a_cena_trinta_e_dois_roda_pelo_numero_dela(acionador, sono):
    vazias = [{"nome": "", "passos": []} for _ in range(MAXIMO - 1)]
    cenas = validar([*vazias, _cena(nome="Ultima", passos=[_ligar()])])
    executor = Executor(cenas, acionador, dormir=sono)
    assert executor.cena_de(MAXIMO).nome == "Ultima"
    assert executor.executar(MAXIMO) is None
    await _terminar(executor, MAXIMO)
    assert acionador.chamadas == [(PROJETOR, "ligar", None)]


async def test_um_passo_recusado_nao_para_a_cena(acionador, sono, caplog):
    """A projector that is off must not stop the lights of the same scene.

    Um projetor desligado não pode parar as luzes da mesma cena.
    """
    acionador.respostas[PROJETOR] = NUMERO_OFFLINE
    cenas = _uma([_ligar(), _passo(), _fonte()])
    executor = Executor(cenas, acionador, dormir=sono)
    with caplog.at_level(logging.WARNING, logger="iphub.cenas"):
        assert executor.executar(1) is None
        await _terminar(executor, 1)
    assert acionador.chamadas == [
        (PROJETOR, "ligar", None),
        (CAIXA, "volume", 30),
        (RECEIVER, "fonte", "HDMI1"),
    ]
    assert NUMERO_OFFLINE in caplog.text


@pytest.mark.parametrize(
    "erro",
    [TimeoutError("sem resposta"), Explosao("fora de Exception")],
    ids=["exception", "fora de Exception"],
)
async def test_um_passo_que_estoura_nao_para_a_cena(acionador, sono, erro):
    acionador.explosoes[PROJETOR] = erro
    cenas = _uma([_ligar(), _fonte()])
    executor = Executor(cenas, acionador, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert acionador.chamadas == [(PROJETOR, "ligar", None), (RECEIVER, "fonte", "HDMI1")]


async def test_a_mesma_cena_nao_roda_duas_vezes(acionador, sono):
    """Two runs of one scene interleaved would leave the volume of whichever step landed
    last, which is not what the file says.

    Duas execuções de uma cena intercaladas deixariam o volume do passo que chegou por último,
    que não é o que o arquivo diz.
    """
    acionador.travar_em = PROJETOR
    executor = Executor(_uma([_ligar(), _passo()]), acionador, dormir=sono)
    assert executor.executar(1) is None
    await asyncio.sleep(0)
    assert executor.executar(1) == CENA_EM_CURSO
    assert executor.em_curso(1) is True
    acionador.liberar.set()
    await _terminar(executor, 1)
    assert acionador.chamadas == [(PROJETOR, "ligar", None), (CAIXA, "volume", 30)]


async def test_a_cena_que_terminou_roda_de_novo(acionador, sono):
    executor = Executor(_uma([_passo()]), acionador, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert acionador.chamadas == [(CAIXA, "volume", 30), (CAIXA, "volume", 30)]


@pytest.mark.parametrize("numero", [0, -1, 2, MAXIMO + 1, True, "1", 1.0, None, "cena1"])
async def test_o_numero_que_nao_e_uma_cena_e_recusado_sem_estourar(acionador, numero):
    executor = Executor(_uma([_passo()]), acionador)
    assert executor.executar(numero) == CENA_NAO_ENCONTRADA
    assert executor.em_curso(numero) is False
    assert executor.cena_de(numero) is None


async def test_trocar_a_lista_nao_muda_a_cena_em_curso(acionador, sono):
    """A run that started with one file must not finish with half of the next one.

    Uma execução que começou com um arquivo não pode terminar com metade do seguinte.
    """
    acionador.travar_em = PROJETOR
    executor = Executor(_uma([_ligar(), _passo()]), acionador, dormir=sono)
    assert executor.executar(1) is None
    await asyncio.sleep(0)
    executor.trocar(_uma([_fonte()], nome="Outra"))
    acionador.liberar.set()
    await _terminar(executor, 1)
    assert acionador.chamadas == [(PROJETOR, "ligar", None), (CAIXA, "volume", 30)]
    assert executor.nomes() == ("Outra",)


async def test_parar_tira_a_cena_do_fio_sem_deixar_tarefa(acionador, sono):
    acionador.travar_em = PROJETOR
    executor = Executor(_uma([_ligar(), _passo()]), acionador, dormir=sono)
    assert executor.executar(1) is None
    await asyncio.sleep(0)
    await executor.parar()
    assert executor.em_curso(1) is False
    assert acionador.chamadas == [(PROJETOR, "ligar", None)]
    assert [t for t in asyncio.all_tasks() if t.get_name().startswith("cena:")] == []


def test_os_nomes_de_trinta_e_duas_cenas_cabem_nas_duas_strings(acionador):
    cenas = validar([_cena(nome=f"Cena {numero}") for numero in range(1, MAXIMO + 1)])
    executor = Executor(cenas, acionador)
    assert len(executor.nomes()) == MAXIMO
    assert mapa.nomes_cabem(executor.nomes())
    primeira, segunda = mapa.nomes_das_cenas(executor.nomes())
    assert json.loads(primeira) == {"c": list(executor.nomes()[:16])}
    assert json.loads(segunda) == {"c": list(executor.nomes()[16:])}


@pytest.fixture
def dir_data(tmp_path: Path) -> Path:
    caminho = tmp_path / "data"
    caminho.mkdir()
    return caminho


def _gravar_cru(dir_data: Path, dados: dict) -> None:
    (dir_data / config.ARQUIVO).write_text(
        json.dumps({"schema_version": SCHEMA_VERSION, **dados}), encoding="utf-8"
    )


def test_a_cena_volta_do_disco_como_dado_tipado(dir_data: Path):
    cenas = _uma([_ligar(espera_ms=500), _fonte(), _passo(acao="grupo", valor="")])
    config.salvar(config.Config(cenas=cenas), dir_data)
    assert config.carregar(dir_data).cenas == cenas


def test_uma_config_sem_cenas_e_uma_instalacao_sem_cena(dir_data: Path):
    _gravar_cru(dir_data, {"idioma": "pt"})
    assert config.carregar(dir_data).cenas == ()


def test_o_boot_carrega_a_cena_de_um_equipamento_que_ja_nao_existe(dir_data: Path):
    """The boot passes no registry, so a scene over an identity erased by hand still loads
    and the step is refused when it runs, instead of the whole appliance refusing to boot.

    O boot não passa cadastro, então uma cena sobre uma identidade apagada na mão ainda
    carrega e o passo é recusado quando roda, em vez de o appliance inteiro recusar o boot.
    """
    _gravar_cru(dir_data, {"cenas": [_cena(passos=[_passo(equipamento=DESCONHECIDO)])]})
    cenas = config.carregar(dir_data).cenas
    assert cenas[0].passos[0].equipamento == DESCONHECIDO


@pytest.mark.parametrize(
    ("rotulo", "cenas"),
    [
        ("um passo com agrupar", [_cena(passos=[_passo(acao="agrupar", valor=None)])]),
        ("uma cena que dispara uma cena", [_cena(passos=[_passo(acao="cena", valor=1)])]),
        ("um volume fora da escala", [_cena(passos=[_passo(valor=101)])]),
        ("trinta e tres cenas", [_cena() for _ in range(MAXIMO + 1)]),
        ("cenas que nao sao lista", {"cena1": []}),
    ],
    ids=["agrupar", "laco de cena", "volume", "trinta e tres cenas", "nao e lista"],
)
def test_o_config_editado_na_mao_e_recusado_como_o_painel_seria(dir_data: Path, rotulo, cenas):
    """The route that saves a scene is one door into this field and the file is the other.

    A rota que salva uma cena é uma porta para este campo e o arquivo é a outra.
    """
    _gravar_cru(dir_data, {"cenas": cenas})
    with pytest.raises(config.ConfigIncompativel) as erro:
        config.carregar(dir_data)
    assert str(dir_data) in str(erro.value)

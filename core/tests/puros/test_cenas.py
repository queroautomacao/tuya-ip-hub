# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""A scene under attack: data and never program, and one failed step never ends a scene.

The refusals are a table of attacks, each one a file somebody could save, so a code the
vocabulary carries and nobody attacks fails the suite instead of being translated in the
panel for a case that never happens. The rules being attacked are the ones of section 8 (a
report only data point is never written by a file, the eight scenes of DP 131, the 255 BYTES
of DP 134) and the rule section 7 fixes for a driver and holds for a scene: no condition, no
loop, no expression.

The execution drives an injected sleep, so the order and the waits are read without spending
a second of them, and the bus is a double that records what it was told and answers what the
test decided.

Uma cena sob ataque: dado e nunca programa, e um passo que falha nunca encerra uma cena.

As recusas são uma tabela de ataques, cada um um arquivo que alguém poderia salvar, então um
código que o vocabulário carrega e ninguém ataca quebra a suíte em vez de ser traduzido no
painel para um caso que nunca ocorre. As regras atacadas são as da seção 8 (um data point de
só report nunca é escrito por um arquivo, as oito cenas do DP 131, os 255 BYTES do DP 134) e a
regra que a seção 7 fixa para um driver e vale para uma cena: sem condicional, sem laço, sem
expressão.

A execução dirige um sono injetado, então a ordem e as esperas são lidas sem gastar um segundo
delas, e o barramento é um dublê que registra o que mandaram nele e responde o que o teste
decidiu.
"""

import asyncio
import json
import logging
from collections.abc import Sequence
from pathlib import Path

import pytest

from iphub import config
from iphub.cenas import (
    CENA_CHAVE_DESCONHECIDA,
    CENA_DP_DESCONHECIDO,
    CENA_DP_PROIBIDO,
    CENA_DP_SOMENTE_LEITURA,
    CENA_EM_CURSO,
    CENA_ESPERA_INVALIDA,
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
    ESPERA_MAXIMA_MS,
    MAXIMO,
    NOME_MAXIMO,
    PASSOS_MAXIMOS,
    Cena,
    CenasInvalidas,
    Executor,
    Passo,
    numero_de,
    validar,
)
from iphub.dpbus import mapa
from iphub.versao import SCHEMA_VERSION


# Why: something outside Exception, which is what a device library raising SystemExit deep
# inside a socket call looks like from the step that called it.
# Por que: algo fora de Exception, que é o que uma biblioteca de aparelho estourando SystemExit
# no fundo de uma chamada de socket parece do passo que a chamou.
class Explosao(BaseException):
    pass


VOLTAS = 200

VOLUME_1 = mapa.dp_de(1, "volume")
PLAY_1 = mapa.dp_de(1, "play")
PRESET_1 = mapa.dp_de(1, "preset")
ONLINE_1 = mapa.dp_de(1, "online")
TOCANDO_1 = mapa.dp_de(1, "tocando")
ENTRADA_1 = mapa.dp_de(1, "entrada")


def _passo(**campos: object) -> dict:
    return {"dpid": VOLUME_1, "valor": 30, **campos}


def _cena(**campos: object) -> dict:
    return {"nome": "Noite", "passos": [_passo()], **campos}


def _problemas(dados: object) -> set[tuple[str, str]]:
    with pytest.raises(CenasInvalidas) as erro:
        validar(dados)
    return set(erro.value.problemas)


def _uma(passos: Sequence[dict], nome: str = "Noite") -> tuple[Cena, ...]:
    return validar([{"nome": nome, "passos": list(passos)}])


class Barramento:
    """The bus a scene sets, as a double: it records, it refuses and it blocks on command.

    O barramento que uma cena ajusta, como dublê: ele registra, recusa e trava sob comando.
    """

    def __init__(self) -> None:
        self.ajustes: list[tuple[int, object]] = []
        self.respostas: dict[int, str] = {}
        self.explosoes: dict[int, BaseException] = {}
        self.travar_em = 0
        self.liberar = asyncio.Event()

    async def __call__(self, dpid: int, valor: object) -> str | None:
        self.ajustes.append((dpid, valor))
        if dpid == self.travar_em:
            await self.liberar.wait()
        erro = self.explosoes.get(dpid)
        if erro is not None:
            raise erro
        return self.respostas.get(dpid)


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
def barramento() -> Barramento:
    return Barramento()


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
        "nove cenas, e a secao 8 numera oito",
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
        [_cena(passos={"dpid": VOLUME_1})],
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
        "dpid fora da secao 8",
        [_cena(passos=[_passo(dpid=999)])],
        {("cenas[0].passos[0].dpid", CENA_DP_DESCONHECIDO)},
    ),
    (
        "dpid escrito como texto",
        [_cena(passos=[_passo(dpid=str(VOLUME_1))])],
        {("cenas[0].passos[0].dpid", CENA_DP_DESCONHECIDO)},
    ),
    (
        "dpid que e o true do json",
        [_cena(passos=[_passo(dpid=True)])],
        {("cenas[0].passos[0].dpid", CENA_DP_DESCONHECIDO)},
    ),
    (
        "passo que escreve o online da zona 1",
        [_cena(passos=[_passo(dpid=ONLINE_1, valor=True)])],
        {("cenas[0].passos[0].dpid", CENA_DP_SOMENTE_LEITURA)},
    ),
    (
        "passo que escreve o tocando da zona 1",
        [_cena(passos=[_passo(dpid=TOCANDO_1, valor="Nada")])],
        {("cenas[0].passos[0].dpid", CENA_DP_SOMENTE_LEITURA)},
    ),
    (
        "passo que escreve os nomes das cenas",
        [_cena(passos=[_passo(dpid=mapa.NOMES_CENAS, valor='{"c":[]}')])],
        {("cenas[0].passos[0].dpid", CENA_DP_SOMENTE_LEITURA)},
    ),
    (
        "cena que dispara uma cena, o laco escrito em dado",
        [_cena(passos=[_passo(dpid=mapa.CENA, valor="cena2")])],
        {("cenas[0].passos[0].dpid", CENA_DP_PROIBIDO)},
    ),
    (
        "volume acima de 100",
        [_cena(passos=[_passo(valor=101)])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "volume que e o true do json",
        [_cena(passos=[_passo(valor=True)])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "play que nao e booleano",
        [_cena(passos=[_passo(dpid=PLAY_1, valor="on")])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "preset fora do enum da secao 8",
        [_cena(passos=[_passo(dpid=PRESET_1, valor="cmd9")])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "grupo fora dos dez valores do enum",
        [_cena(passos=[_passo(dpid=mapa.GRUPO, valor=f"grupo{mapa.GRUPOS + 1}")])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "entrada vazia",
        [_cena(passos=[_passo(dpid=ENTRADA_1, valor="")])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "entrada com caractere de controle",
        [_cena(passos=[_passo(dpid=ENTRADA_1, valor="wifi\r")])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "entrada com surrogado solto, que o utf-8 nao escreve",
        [_cena(passos=[_passo(dpid=ENTRADA_1, valor="\ud800")])],
        {("cenas[0].passos[0].valor", CENA_VALOR_INVALIDO)},
    ),
    (
        "espera negativa",
        [_cena(passos=[_passo(espera_ms=-1)])],
        {("cenas[0].passos[0].espera_ms", CENA_ESPERA_INVALIDA)},
    ),
    (
        "espera acima do teto",
        [_cena(passos=[_passo(espera_ms=ESPERA_MAXIMA_MS + 1)])],
        {("cenas[0].passos[0].espera_ms", CENA_ESPERA_INVALIDA)},
    ),
    (
        "espera que e o true do json",
        [_cena(passos=[_passo(espera_ms=True)])],
        {("cenas[0].passos[0].espera_ms", CENA_ESPERA_INVALIDA)},
    ),
    (
        "espera escrita como texto",
        [_cena(passos=[_passo(espera_ms="500")])],
        {("cenas[0].passos[0].espera_ms", CENA_ESPERA_INVALIDA)},
    ),
    (
        "oito nomes que nao cabem nos 255 bytes do dp 134",
        [_cena(nome="n" * 29) for _ in range(MAXIMO)],
        {("cenas", mapa.NOMES_LONGOS)},
    ),
    (
        "nome com surrogado solto, que o dp 134 nao sabe escrever",
        [_cena(nome="\ud800")],
        {("cenas", mapa.NOME_NAO_GRAVAVEL)},
    ),
)


@pytest.mark.parametrize(("rotulo", "dados", "esperado"), ATAQUES, ids=[a[0] for a in ATAQUES])
def test_a_cena_quebrada_e_recusada_pelo_campo_e_nada_mais(rotulo, dados, esperado):
    assert _problemas(dados) == esperado


def test_todo_codigo_do_vocabulario_tem_um_ataque():
    """A code nobody attacks is a code the panel translates for a case that never happens.

    Um código que ninguém ataca é um código que o painel traduz para um caso que nunca ocorre.
    """
    atacados = {codigo for _, _, esperado in ATAQUES for _, codigo in esperado}
    assert atacados == set(CODIGOS)


def test_todo_codigo_e_estavel_e_unico():
    assert len(set(CODIGOS)) == len(CODIGOS)
    assert all(codigo.startswith("cena") or codigo in mapa.CODIGOS_DE_NOMES for codigo in CODIGOS)
    assert all(codigo.replace("_", "").isalnum() for codigo in CODIGOS)


def test_uma_recusa_e_um_value_error():
    # Why: whoever refuses to boot on a broken file catches ValueError, and config turns this
    # into its own refusal; the subclass has to stay under it.
    # Por que: quem recusa o boot com arquivo quebrado captura ValueError, e o config
    # transforma isto na recusa dele; a subclasse precisa ficar debaixo dele.
    assert issubclass(CenasInvalidas, ValueError)


def test_a_lista_quebrada_responde_todo_problema_de_uma_vez():
    dados = [
        _cena(nome="x" * (NOME_MAXIMO + 1), passos=[_passo(dpid=ONLINE_1, valor=True)]),
        _cena(passos=[_passo(espera_ms=-5)]),
    ]
    assert _problemas(dados) == {
        ("cenas[0].nome", CENA_NOME_INVALIDO),
        ("cenas[0].passos[0].dpid", CENA_DP_SOMENTE_LEITURA),
        ("cenas[1].passos[0].espera_ms", CENA_ESPERA_INVALIDA),
    }


def test_a_cena_valida_vira_dado_tipado():
    cenas = _uma([_passo(espera_ms=500), _passo(dpid=PLAY_1, valor=True)])
    assert cenas == (
        Cena(
            nome="Noite",
            passos=(
                Passo(dpid=VOLUME_1, valor=30, espera_ms=500),
                Passo(dpid=PLAY_1, valor=True, espera_ms=0),
            ),
        ),
    )


def test_uma_instalacao_sem_cena_e_uma_lista_vazia():
    assert validar([]) == ()


def test_a_posicao_de_uma_cena_e_o_numero_dela(barramento):
    """Section 8 numbers cena1 to cena8, so an erased scene empties its slot instead of
    pulling the next one back into a number the customer already automated.

    A seção 8 numera cena1 a cena8, então uma cena apagada esvazia a vaga dela em vez de puxar
    a seguinte para um número que o cliente já automatizou.
    """
    cenas = validar([{"nome": "", "passos": []}, _cena(nome="Jantar")])
    executor = Executor(cenas, barramento)
    assert executor.cenas == cenas
    assert executor.nomes() == ("", "Jantar")
    assert executor.executar(1) == CENA_NAO_ENCONTRADA
    assert executor.cena_de(2).nome == "Jantar"


def test_a_entrada_de_uma_zona_aceita_o_valor_que_o_hardware_declara():
    """Section 14: the inputs come from plm_support, so the map cannot judge one; the shape
    is judged here and the bus refuses the value the speaker does not have when it runs.

    Seção 14: as entradas vêm do plm_support, então o mapa não julga uma; a forma é julgada
    aqui e o barramento recusa o valor que a caixa não tem quando o passo roda.
    """
    cenas = _uma([_passo(dpid=ENTRADA_1, valor="bluetooth")])
    assert cenas[0].passos[0] == Passo(dpid=ENTRADA_1, valor="bluetooth", espera_ms=0)


def test_oito_nomes_no_limite_exato_do_dp_134_passam():
    nomes = ["n" * 28 for _ in range(MAXIMO)]
    texto = mapa.nomes_json(mapa.NOMES_CENAS, nomes)
    assert len(texto.encode("utf-8")) == mapa.TEXTO_MAXIMO_BYTES
    cenas = validar([_cena(nome=nome) for nome in nomes])
    assert [cena.nome for cena in cenas] == nomes


def test_o_teto_do_dp_134_e_de_bytes_e_nao_de_letras():
    """The bench fixed the names of six zones in 255 bytes, and an accented letter costs two
    of them; counting characters would publish a JSON the bridge cannot read.

    A bancada fixou os nomes de seis zonas em 255 bytes, e uma letra acentuada custa dois
    deles; contar caracteres publicaria um JSON que a ponte não consegue ler.
    """
    nomes = ["n" * 28 for _ in range(MAXIMO - 1)] + ["ç" + "n" * 27]
    assert all(len(nome) == 28 for nome in nomes)
    assert _problemas([_cena(nome=nome) for nome in nomes]) == {("cenas", mapa.NOMES_LONGOS)}


@pytest.mark.parametrize(
    ("valor", "esperado"),
    [
        ("cena1", 1),
        ("cena8", MAXIMO),
        ("cena9", None),
        ("CENA1", None),
        ("", None),
        (1, None),
        (True, None),
        (None, None),
    ],
)
def test_o_numero_da_cena_vem_do_enum_do_dp_131(valor, esperado):
    assert numero_de(valor) == esperado


async def test_os_passos_correm_na_ordem_com_as_esperas_declaradas(barramento, sono):
    cenas = _uma(
        [
            _passo(espera_ms=500),
            _passo(dpid=ENTRADA_1, valor="wifi", espera_ms=250),
            _passo(dpid=PLAY_1, valor=True),
        ]
    )
    executor = Executor(cenas, barramento, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert barramento.ajustes == [(VOLUME_1, 30), (ENTRADA_1, "wifi"), (PLAY_1, True)]
    assert sono.esperas == [0.5, 0.25]


async def test_a_espera_do_ultimo_passo_nao_e_dormida(barramento, sono):
    executor = Executor(_uma([_passo(espera_ms=ESPERA_MAXIMA_MS)]), barramento, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert sono.esperas == []


async def test_executar_responde_antes_de_o_primeiro_passo_ir_ao_barramento(barramento, sono):
    """Fire and forget: whoever asked gets the answer at once, and a scene of ten seconds of
    waits does not hold the socket or the route that started it.

    Disparar e esquecer: quem pediu recebe a resposta na hora, e uma cena de dez segundos de
    espera não segura o socket nem a rota que a começou.
    """
    executor = Executor(_uma([_passo(), _passo(dpid=PLAY_1, valor=True)]), barramento, dormir=sono)
    assert executor.executar(1) is None
    assert barramento.ajustes == []
    await _terminar(executor, 1)
    assert barramento.ajustes == [(VOLUME_1, 30), (PLAY_1, True)]


async def test_um_passo_recusado_nao_para_a_cena(barramento, sono, caplog):
    """A projector that is off must not stop the lights of the same scene.

    Um projetor desligado não pode parar as luzes da mesma cena.
    """
    barramento.respostas[ENTRADA_1] = "zona_offline"
    cenas = _uma([_passo(dpid=ENTRADA_1, valor="wifi"), _passo(), _passo(dpid=PLAY_1, valor=True)])
    executor = Executor(cenas, barramento, dormir=sono)
    with caplog.at_level(logging.WARNING, logger="iphub.cenas"):
        assert executor.executar(1) is None
        await _terminar(executor, 1)
    assert barramento.ajustes == [(ENTRADA_1, "wifi"), (VOLUME_1, 30), (PLAY_1, True)]
    assert "zona_offline" in caplog.text


@pytest.mark.parametrize(
    "erro",
    [TimeoutError("sem resposta"), Explosao("fora de Exception")],
    ids=["exception", "fora de Exception"],
)
async def test_um_passo_que_estoura_nao_para_a_cena(barramento, sono, erro):
    barramento.explosoes[ENTRADA_1] = erro
    cenas = _uma([_passo(dpid=ENTRADA_1, valor="wifi"), _passo(dpid=PLAY_1, valor=True)])
    executor = Executor(cenas, barramento, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert barramento.ajustes == [(ENTRADA_1, "wifi"), (PLAY_1, True)]


async def test_a_mesma_cena_nao_roda_duas_vezes(barramento, sono):
    """Two runs of one scene interleaved would leave the volume of whichever step landed
    last, which is not what the file says.

    Duas execuções de uma cena intercaladas deixariam o volume do passo que chegou por último,
    que não é o que o arquivo diz.
    """
    barramento.travar_em = VOLUME_1
    executor = Executor(_uma([_passo(), _passo(dpid=PLAY_1, valor=True)]), barramento, dormir=sono)
    assert executor.executar(1) is None
    await asyncio.sleep(0)
    assert executor.executar(1) == CENA_EM_CURSO
    assert executor.em_curso(1) is True
    barramento.liberar.set()
    await _terminar(executor, 1)
    assert barramento.ajustes == [(VOLUME_1, 30), (PLAY_1, True)]


async def test_a_cena_que_terminou_roda_de_novo(barramento, sono):
    executor = Executor(_uma([_passo()]), barramento, dormir=sono)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert executor.executar(1) is None
    await _terminar(executor, 1)
    assert barramento.ajustes == [(VOLUME_1, 30), (VOLUME_1, 30)]


@pytest.mark.parametrize("numero", [0, -1, 2, MAXIMO + 1, True, "1", 1.0, None, "cena1"])
async def test_o_numero_que_nao_e_uma_cena_e_recusado_sem_estourar(barramento, numero):
    executor = Executor(_uma([_passo()]), barramento)
    assert executor.executar(numero) == CENA_NAO_ENCONTRADA
    assert executor.em_curso(numero) is False
    assert executor.cena_de(numero) is None


async def test_trocar_a_lista_nao_muda_a_cena_em_curso(barramento, sono):
    """A run that started with one file must not finish with half of the next one.

    Uma execução que começou com um arquivo não pode terminar com metade do seguinte.
    """
    barramento.travar_em = VOLUME_1
    executor = Executor(_uma([_passo(), _passo(dpid=PLAY_1, valor=True)]), barramento, dormir=sono)
    assert executor.executar(1) is None
    await asyncio.sleep(0)
    executor.trocar(_uma([_passo(dpid=PRESET_1, valor="cmd3")], nome="Outra"))
    barramento.liberar.set()
    await _terminar(executor, 1)
    assert barramento.ajustes == [(VOLUME_1, 30), (PLAY_1, True)]
    assert executor.nomes() == ("Outra",)


async def test_parar_tira_a_cena_do_fio_sem_deixar_tarefa(barramento, sono):
    barramento.travar_em = VOLUME_1
    executor = Executor(_uma([_passo(), _passo(dpid=PLAY_1, valor=True)]), barramento, dormir=sono)
    assert executor.executar(1) is None
    await asyncio.sleep(0)
    await executor.parar()
    assert executor.em_curso(1) is False
    assert barramento.ajustes == [(VOLUME_1, 30)]
    assert [t for t in asyncio.all_tasks() if t.get_name().startswith("cena:")] == []


def test_os_nomes_das_cenas_cabem_no_dp_134(barramento):
    cenas = validar([_cena(nome=f"Cena {numero}") for numero in range(1, MAXIMO + 1)])
    executor = Executor(cenas, barramento)
    assert len(executor.nomes()) == MAXIMO
    assert mapa.nomes_cabem(mapa.NOMES_CENAS, executor.nomes())


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
    cenas = _uma([_passo(espera_ms=500), _passo(dpid=ENTRADA_1, valor="wifi")])
    config.salvar(config.Config(cenas=cenas), dir_data)
    assert config.carregar(dir_data).cenas == cenas


def test_uma_config_sem_cenas_e_uma_instalacao_sem_cena(dir_data: Path):
    _gravar_cru(dir_data, {"idioma": "pt"})
    assert config.carregar(dir_data).cenas == ()


@pytest.mark.parametrize(
    ("rotulo", "cenas"),
    [
        ("um passo que escreve um dp de report", [_cena(passos=[_passo(dpid=ONLINE_1)])]),
        ("uma cena que dispara uma cena", [_cena(passos=[_passo(dpid=mapa.CENA, valor="cena1")])]),
        ("nove cenas", [_cena() for _ in range(MAXIMO + 1)]),
        ("cenas que nao sao lista", {"cena1": []}),
    ],
    ids=["dp de report", "laco de cena", "nove cenas", "nao e lista"],
)
def test_o_config_editado_na_mao_e_recusado_como_o_painel_seria(dir_data: Path, rotulo, cenas):
    """The route that saves a scene is one door into this field and the file is the other.

    A rota que salva uma cena é uma porta para este campo e o arquivo é a outra.
    """
    _gravar_cru(dir_data, {"cenas": cenas})
    with pytest.raises(config.ConfigIncompativel) as erro:
        config.carregar(dir_data)
    assert str(dir_data) in str(erro.value)

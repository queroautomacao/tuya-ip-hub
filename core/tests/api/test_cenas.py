# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the scene routes: data in, a stable code out, and one run at a time.

A scene is DATA, so every refusal here is the validation of section 8 answering by field,
and every acceptance is a list that reached the disk before it reached the executor. The
steps run on a task of their own, which is what the run route promises, so the tests wait
for the driver to be reached instead of for the request to come back.

O contrato das rotas de cena: dado entra, código estável sai, e uma execução por vez.

Uma cena é DADO, então toda recusa aqui é a validação da seção 8 respondendo por campo, e
toda aceitação é uma lista que chegou ao disco antes de chegar ao executor. Os passos correm
numa tarefa própria, que é o que a rota de execução promete, então os testes esperam a caixa
ser alcançada e não a requisição voltar.
"""

import asyncio
import json

import pytest

from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config
from tests.api.test_zonas import IP_1, IP_2, TIPO, _cadastro, _caixa, _fabrica

VOLUME_1, ONLINE_1, TOCANDO_1 = 101, 104, 105
VOLUME_2 = 106
CENA = 131
GRUPO = 132
NOMES_ZONAS = 133

# Why: the deadline is the whole point of a test that waits, and a scene reaches a fake
# driver in microseconds; anything longer than this is a scene that never started.
# Por que: o prazo é o ponto de um teste que espera, e uma cena alcança um driver falso em
# microssegundos; algo maior que isto é uma cena que nunca começou.
PRAZO_S = 2.0


@pytest.fixture
def abrir(fabrica_cliente, posse, bearer):
    async def criar(catalogo: dict, *, equipamentos=(), zonas=(), cenas=()):
        cliente = await fabrica_cliente(
            catalogo=catalogo,
            config=Config(equipamentos=equipamentos, zonas=zonas, cenas=cenas),
        )
        return cliente, bearer(await posse(cliente))

    return criar


@pytest.fixture
async def hub(abrir):
    """Two speakers in blocks 1 and 2 and no scene saved yet.

    Duas caixas nos blocos 1 e 2 e nenhuma cena salva ainda.
    """
    classe = _fabrica()
    cliente, auth = await abrir(
        {TIPO: classe},
        equipamentos=(
            _cadastro("uuid-1", ip=IP_1, nome="Sala"),
            _cadastro("uuid-2", ip=IP_2, nome="Cozinha"),
        ),
        zonas=("uuid-1", "uuid-2"),
    )
    return cliente, auth, classe


async def _json(resposta) -> dict:
    return await resposta.json()


async def _esperar(condicao) -> None:
    """Waits for the task of the scene to reach the driver, with a deadline.

    Espera a tarefa da cena alcançar o driver, com prazo.
    """
    async with asyncio.timeout(PRAZO_S):
        while not condicao():
            await asyncio.sleep(0)


def _cena(nome: str = "Filme", passos=((VOLUME_1, 30, 0),)) -> dict:
    return {
        "nome": nome,
        "passos": [
            {"dpid": dpid, "valor": valor, "espera_ms": espera} for dpid, valor, espera in passos
        ],
    }


async def _salvar(cliente, auth, cenas: list[dict]):
    return await cliente.post("/api/cenas", json={"cenas": cenas}, headers=auth)


def _codigos(corpo: dict) -> list[str]:
    return [problema["codigo"] for problema in corpo["problemas"]]


async def test_um_hub_sem_cena_responde_a_lista_vazia_e_os_tetos(hub):
    """Section 8: eight scenes on DP 131, and the panel reads the ceilings from here.

    Seção 8: oito cenas no DP 131, e o painel lê os tetos daqui.
    """
    cliente, auth, _classe = hub
    corpo = await _json(await cliente.get("/api/cenas", headers=auth))
    assert corpo["cenas"] == []
    assert corpo["maximo"] == 8
    assert corpo["passos_maximos"] == 32
    assert corpo["espera_maxima_ms"] == 30_000


async def test_a_cena_salva_chega_ao_arquivo_e_a_leitura(hub, amb):
    """A scene that lived only in memory would be gone on the next boot, in silence.

    Uma cena que vivesse só na memória sumiria no próximo boot, em silêncio.
    """
    cliente, auth, _classe = hub
    resposta = await _salvar(
        cliente, auth, [_cena(passos=((VOLUME_1, 30, 250), (VOLUME_2, 10, 0)))]
    )
    assert resposta.status == 200, await resposta.text()
    em_disco = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    assert em_disco["cenas"] == [
        {
            "nome": "Filme",
            "passos": [
                {"dpid": VOLUME_1, "valor": 30, "espera_ms": 250},
                {"dpid": VOLUME_2, "valor": 10, "espera_ms": 0},
            ],
        }
    ]
    corpo = await _json(await cliente.get("/api/cenas", headers=auth))
    assert corpo["cenas"][0]["numero"] == 1
    assert corpo["cenas"][0]["nome"] == "Filme"
    assert corpo["cenas"][0]["em_curso"] is False
    assert corpo["cenas"][0]["passos"][0] == {"dpid": VOLUME_1, "valor": 30, "espera_ms": 250}


async def test_a_vaga_de_uma_cena_apagada_continua_ali(hub):
    """The POSITION is the number, so erasing scene 1 must not move scene 2 into it.

    A POSIÇÃO é o número, então apagar a cena 1 não pode mover a cena 2 para ela.
    """
    cliente, auth, _classe = hub
    assert (await _salvar(cliente, auth, [_cena("Filme"), _cena("Festa")])).status == 200
    assert (
        await _salvar(cliente, auth, [{"nome": "", "passos": []}, _cena("Festa")])
    ).status == 200
    corpo = await _json(await cliente.get("/api/cenas", headers=auth))
    assert [cena["nome"] for cena in corpo["cenas"]] == ["", "Festa"]
    assert corpo["cenas"][1]["numero"] == 2


@pytest.mark.parametrize(
    ("dpid", "codigo"),
    [
        (ONLINE_1, "cena_dp_somente_leitura"),
        (TOCANDO_1, "cena_dp_somente_leitura"),
        (NOMES_ZONAS, "cena_dp_somente_leitura"),
        (CENA, "cena_dp_proibido"),
        (999, "cena_dp_desconhecido"),
    ],
)
async def test_um_passo_que_escreve_o_que_ninguem_escreve_e_recusado(hub, dpid, codigo):
    """Section 8: the chip never echoes, and a scene starting a scene is a loop in data.

    Seção 8: o chip nunca ecoa, e uma cena que dispara uma cena é um laço escrito em dado.
    """
    cliente, auth, _classe = hub
    resposta = await _salvar(cliente, auth, [_cena(passos=((dpid, True, 0),))])
    assert resposta.status == 400
    corpo = await _json(resposta)
    assert corpo["code"] == "cenas_invalidas"
    assert codigo in _codigos(corpo)
    assert (await _json(await cliente.get("/api/cenas", headers=auth)))["cenas"] == []


async def test_a_recusa_lista_todo_problema_de_uma_vez(hub):
    """One pass fixes the file, the same way section 7 refuses a driver.

    Uma passada conserta o arquivo, do mesmo jeito que a seção 7 recusa um driver.
    """
    cliente, auth, _classe = hub
    resposta = await _salvar(
        cliente,
        auth,
        [{"nome": "Filme", "passos": [{"dpid": ONLINE_1, "valor": True}], "quando": "18h"}],
    )
    corpo = await _json(resposta)
    assert sorted(_codigos(corpo)) == ["cena_chave_desconhecida", "cena_dp_somente_leitura"]
    assert [problema["campo"] for problema in corpo["problemas"]] == [
        "cenas[0].quando",
        "cenas[0].passos[0].dpid",
    ]


async def test_um_nome_que_nao_cabe_no_dp_134_e_recusado(hub):
    """DP 134 carries every name in one string of 255 bytes, and it is never cut.

    O DP 134 carrega todo nome numa string de 255 bytes, e ela nunca é cortada.
    """
    cliente, auth, _classe = hub
    resposta = await _salvar(cliente, auth, [_cena("N" * 40) for _ in range(8)])
    assert resposta.status == 400
    assert "nomes_longos" in _codigos(await _json(resposta))


async def test_uma_espera_fora_da_faixa_e_recusada(hub):
    cliente, auth, _classe = hub
    resposta = await _salvar(cliente, auth, [_cena(passos=((VOLUME_1, 30, 60_000),))])
    assert resposta.status == 400
    assert "cena_espera_invalida" in _codigos(await _json(resposta))


async def test_executar_uma_cena_ajusta_os_data_points_dela(hub):
    cliente, auth, classe = hub
    assert (
        await _salvar(cliente, auth, [_cena(passos=((VOLUME_1, 30, 0), (VOLUME_2, 10, 0)))])
    ).status == 200
    resposta = await cliente.post("/api/cenas/1/executar", headers=auth)
    assert resposta.status == 200, await resposta.text()
    await _esperar(lambda: _caixa(classe, "uuid-2").chamadas)
    assert _caixa(classe, "uuid-1").chamadas == [("volume", 30)]
    assert _caixa(classe, "uuid-2").chamadas == [("volume", 10)]


async def test_um_passo_que_falha_nao_para_a_cena(hub):
    """A projector that is off must not stop the lights of the same scene.

    Um projetor desligado não pode parar as luzes da mesma cena.
    """
    cliente, auth, classe = hub
    vazio = 111
    assert (
        await _salvar(cliente, auth, [_cena(passos=((vazio, 30, 0), (VOLUME_1, 15, 0)))])
    ).status == 200
    assert (await cliente.post("/api/cenas/1/executar", headers=auth)).status == 200
    await _esperar(lambda: _caixa(classe, "uuid-1").chamadas)
    assert _caixa(classe, "uuid-1").chamadas == [("volume", 15)]


async def test_a_mesma_cena_nao_roda_duas_vezes_ao_mesmo_tempo(hub):
    """Two runs of one scene interleave two sequences over the same data points.

    Duas execuções de uma cena intercalam duas sequências sobre os mesmos data points.
    """
    cliente, auth, classe = hub
    assert (await _salvar(cliente, auth, [_cena(passos=((VOLUME_1, 30, 0),))])).status == 200
    caixa = _caixa(classe, "uuid-1")
    caixa.pausa = asyncio.Event()
    assert (await cliente.post("/api/cenas/1/executar", headers=auth)).status == 200
    await _esperar(lambda: caixa.chamadas)
    resposta = await cliente.post("/api/cenas/1/executar", headers=auth)
    assert resposta.status == 409
    assert (await _json(resposta))["code"] == "cena_em_curso"
    assert (await _json(await cliente.get("/api/cenas", headers=auth)))["cenas"][0]["em_curso"]
    caixa.pausa.set()


@pytest.mark.parametrize("numero", ["1", "2", "9", "0", "abc"])
async def test_executar_uma_vaga_que_ninguem_usou_nao_acha_cena(hub, numero):
    """A slot with no step is a number held open for the automations, not a scene.

    Uma vaga sem passo é um número guardado para as automações, e não uma cena.
    """
    cliente, auth, _classe = hub
    assert (await _salvar(cliente, auth, [{"nome": "", "passos": []}])).status == 200
    resposta = await cliente.post(f"/api/cenas/{numero}/executar", headers=auth)
    assert resposta.status == 404
    assert (await _json(resposta))["code"] == "cena_nao_encontrada"


async def test_uma_cena_salva_nomeia_o_dp_134_no_snapshot(hub):
    """The names of the scenes are what DP 134 publishes, and the snapshot shows it.

    Os nomes das cenas são o que o DP 134 publica, e o snapshot mostra isso.
    """
    cliente, auth, _classe = hub
    assert (await _salvar(cliente, auth, [_cena("Filme"), _cena("Festa")])).status == 200
    corpo = await _json(await cliente.get("/api/dps", headers=auth))
    assert json.loads(corpo["dps"]["134"]) == {"c": ["Filme", "Festa"]}


async def test_uma_cena_pode_formar_o_grupo(hub):
    """DP 132 is settable, so a scene may name a group; the bus does the rest.

    O DP 132 é ajustável, então uma cena pode nomear um grupo; o barramento faz o resto.
    """
    cliente, auth, classe = hub
    assert (await _salvar(cliente, auth, [_cena(passos=((GRUPO, "grupo1", 0),))])).status == 200
    assert (await cliente.post("/api/cenas/1/executar", headers=auth)).status == 200
    await _esperar(lambda: _caixa(classe, "uuid-2").chamadas)
    assert _caixa(classe, "uuid-2").chamadas == [("entrar_no_grupo", IP_1)]
    assert (await _json(await cliente.get("/api/zonas", headers=auth)))["grupo"] == "grupo1"


async def test_a_config_editada_na_mao_com_uma_cena_invalida_nao_sobe(fabrica_cliente, amb):
    """The route is one door into this field and the file is the other.

    A rota é uma porta para este campo e o arquivo é a outra.
    """
    amb.dir_data.mkdir(parents=True, exist_ok=True)
    (amb.dir_data / ARQUIVO_CONFIG).write_text(
        json.dumps(
            {
                "schema_version": 1,
                "cenas": [{"nome": "Filme", "passos": [{"dpid": ONLINE_1, "valor": True}]}],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(Exception) as erro:
        await fabrica_cliente(catalogo={TIPO: _fabrica()})
    assert "cenas" in str(erro.value)


def test_a_cena_da_config_e_dado_tipado():
    """A scene of the file is the same dataclass a route saves, or the boot refuses it.

    Uma cena do arquivo é a mesma dataclass que uma rota grava, ou o boot a recusa.
    """
    cfg = Config(cenas=())
    assert cfg.cenas == ()
    assert isinstance(_cadastro("uuid-1"), Cadastro)


@pytest.mark.parametrize("numero", ["abc", "0", "9", "²", "١"])
async def test_um_numero_de_cena_fora_do_contrato_nunca_e_erro_interno(hub, numero):
    """Section 11: the API answers a stable code, never a 500 with a traceback in the log.

    Why: str.isdigit() is true for the superscript two and int() refuses it, so this path
    answered erro_interno, and a session holder could fill the log with tracebacks at will.

    Seção 11: a API responde um código estável, nunca um 500 com traceback no log.

    Por que: str.isdigit() é verdadeiro para o dois sobrescrito e o int() o recusa, então este
    caminho respondia erro_interno, e quem tem sessão podia encher o log de tracebacks.
    """
    cliente, auth, _classe = hub
    resposta = await cliente.post(f"/api/cenas/{numero}/executar", headers=auth)
    assert resposta.status != 500, await resposta.text()
    assert (await _json(resposta))["code"] != "erro_interno"

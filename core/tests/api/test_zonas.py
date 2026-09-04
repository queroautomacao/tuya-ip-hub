# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The contract of the zone routes: the order of the blocks, the data points and the group.

The numbers of section 8 are written by hand in this file. A test that asked the map for
them would agree with any change the map made to the contract, which is exactly what a
contract test exists to catch, and the panel reads these numbers out of these answers.

O contrato das rotas de zona: a ordem dos blocos, os data points e o grupo.

Os números da seção 8 estão escritos na mão neste arquivo. Um teste que os pedisse ao mapa
concordaria com qualquer mudança que o mapa fizesse no contrato, que é exatamente o que um
teste de contrato existe para pegar, e o painel lê estes números destas respostas.
"""

import asyncio
import json
from dataclasses import dataclass

import pytest

from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto

TIPO = "multiroom_falso"
OUTRO_TIPO = "multiroom_de_outra_marca"

CAPACIDADES = ("volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra")

# The numbers of section 8, written by hand on purpose.
# Os números da seção 8, escritos na mão de propósito.
VOLUME_1, PLAY_1, PRESET_1, ONLINE_1, TOCANDO_1, ENTRADA_1 = 101, 102, 103, 104, 105, 141
VOLUME_2, PLAY_2, PRESET_2, ONLINE_2, TOCANDO_2, ENTRADA_2 = 106, 107, 108, 109, 110, 142
CENA = 131
GRUPO = 132
NOMES_ZONAS = 133
NOMES_CENAS = 134
NOMES_GRUPOS = 135

IP_1 = "192.0.2.11"
IP_2 = "192.0.2.12"

FONTES = ("wifi", "line-in")


@dataclass(frozen=True)
class _Grupo:
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
) -> type[Driver]:
    """A multiroom driver that records what reached it, so a test proves what never did.

    Um driver multiroom que guarda o que chegou nele, para um teste provar o que nunca chegou.
    """

    class Falsa(Driver):
        MANIFESTO = _manifesto(tipo, categoria, capacidades)
        instancias: list["Falsa"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.chamadas: list[tuple[str, object]] = []
            self.pausa: asyncio.Event | None = None
            self.grupo = _Grupo()
            self.espelho: str | None = None
            self._defina(online=True, volume=20, fonte="wifi", fontes=FONTES, tocando=None)
            type(self).instancias.append(self)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            return await self._passo(acao, valor)

        async def _passo(self, nome: str, valor: object) -> str | None:
            self.chamadas.append((nome, valor))
            if self.pausa is not None:
                await self.pausa.wait()
            return None

        async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
            return await self._passo("entrar_no_grupo", ip_do_mestre)

        async def desfazer_grupo(self) -> str | None:
            return await self._passo("desfazer_grupo", None)

        async def volume_de_escravo(self, ip: object, valor: object) -> str | None:
            return await self._passo("volume_de_escravo", (ip, valor))

        async def ler_grupo(self) -> _Grupo:
            return self.grupo

        def marcar_grupo(self, dentro: bool) -> None:
            if not dentro:
                self.espelho = None

        def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
            self.espelho = tocando
            self._defina(tocando=tocando, reproduzindo=reproduzindo)

        escravo_alheio = False

        def e_escravo(self) -> bool:

            return self.escravo_alheio

        def saiu_do_grupo(self) -> bool:
            return False

    Falsa.instancias = []
    return Falsa


def _cadastro(identidade: str, tipo: str = TIPO, ip: str = IP_1, nome: str = "Sala") -> Cadastro:
    return Cadastro(identidade=identidade, tipo=tipo, nome=nome, ip=ip)


@pytest.fixture
def abrir(fabrica_cliente, posse, bearer):
    """A hub with the catalog, the registrations and the order the test wants.

    Um hub com o catálogo, os cadastros e a ordem que o teste quiser.
    """

    async def criar(catalogo: dict, *, equipamentos=(), zonas=(), cenas=()):
        cliente = await fabrica_cliente(
            catalogo=catalogo,
            config=Config(equipamentos=equipamentos, zonas=zonas, cenas=cenas),
        )
        return cliente, bearer(await posse(cliente))

    return criar


@pytest.fixture
async def duas(abrir):
    """Two speakers of the same kind in blocks 1 and 2, which is the smallest real group.

    Duas caixas do mesmo tipo nos blocos 1 e 2, que é o menor grupo real.
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


def _caixa(classe, identidade: str):
    return next(caixa for caixa in classe.instancias if caixa.cadastro.identidade == identidade)


async def _json(resposta) -> dict:
    return await resposta.json()


async def test_a_ordem_vazia_ainda_lista_os_seis_blocos(abrir):
    """Section 6: zero equipment is a normal state, and the POSITION is the contract.

    Seção 6: zero equipamento é estado normal, e a POSIÇÃO é o contrato.
    """
    cliente, auth = await abrir({TIPO: _fabrica()})
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    assert corpo["ok"] is True
    assert [bloco["zona"] for bloco in corpo["zonas"]] == [1, 2, 3, 4, 5, 6]
    assert all(bloco["identidade"] == "" and bloco["estado"] is None for bloco in corpo["zonas"])
    assert corpo["grupo"] == "solo"
    assert corpo["dp_grupo"] == GRUPO


async def test_cada_bloco_carrega_os_data_points_da_secao_8(abrir):
    """The panel reads the numbering from here, so it never carries a copy of section 8.

    O painel lê a numeração daqui, então ele nunca carrega uma cópia da seção 8.
    """
    cliente, auth = await abrir({TIPO: _fabrica()})
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    primeiro, segundo = corpo["zonas"][0], corpo["zonas"][1]
    assert primeiro["dps"] == {
        "volume": VOLUME_1,
        "play": PLAY_1,
        "preset": PRESET_1,
        "online": ONLINE_1,
        "tocando": TOCANDO_1,
        "entrada": ENTRADA_1,
    }
    assert segundo["dps"] == {
        "volume": VOLUME_2,
        "play": PLAY_2,
        "preset": PRESET_2,
        "online": ONLINE_2,
        "tocando": TOCANDO_2,
        "entrada": ENTRADA_2,
    }
    assert corpo["zonas"][5]["dps"]["volume"] == 126


async def test_a_ordem_salva_chega_ao_arquivo_e_a_leitura(abrir, amb):
    """A block that lives only in memory would move a speaker back on the next boot.

    Um bloco que vivesse só na memória moveria uma caixa de volta no próximo boot.
    """
    cliente, auth = await abrir(
        {TIPO: _fabrica()},
        equipamentos=(_cadastro("uuid-1"), _cadastro("uuid-2", ip=IP_2, nome="Cozinha")),
    )
    resposta = await cliente.post(
        "/api/zonas", json={"zonas": ["uuid-2", "", "uuid-1"]}, headers=auth
    )
    assert resposta.status == 200, await resposta.text()
    assert (await _json(resposta))["zonas"] == ["uuid-2", "", "uuid-1"]
    em_disco = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    assert em_disco["zonas"] == ["uuid-2", "", "uuid-1"]
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    assert [bloco["identidade"] for bloco in corpo["zonas"]] == ["uuid-2", "", "uuid-1", "", "", ""]
    assert corpo["zonas"][0]["nome"] == "Cozinha"
    assert corpo["zonas"][0]["tipo"] == TIPO
    assert corpo["zonas"][0]["estado"]["online"] is True
    assert corpo["zonas"][0]["entradas"] == list(FONTES)
    assert corpo["zonas"][1]["estado"] is None


async def test_uma_ordem_maior_que_o_contrato_e_recusada(abrir):
    """Section 8 numbers six blocks and there is no seventh.

    A seção 8 numera seis blocos e não existe um sétimo.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),))
    resposta = await cliente.post("/api/zonas", json={"zonas": [""] * 7}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "zonas_demais"


async def test_uma_identidade_que_ninguem_cadastrou_nao_ocupa_um_bloco(abrir):
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),))
    resposta = await cliente.post("/api/zonas", json={"zonas": ["uuid-9"]}, headers=auth)
    assert resposta.status == 404
    assert (await _json(resposta))["code"] == "eq_nao_encontrado"


async def test_a_mesma_caixa_nao_ocupa_dois_blocos(abrir):
    """One speaker in two blocks answers the volume of two zones on the bus.

    Uma caixa em dois blocos responde o volume de duas zonas no barramento.
    """
    cliente, auth = await abrir({TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),))
    resposta = await cliente.post("/api/zonas", json={"zonas": ["uuid-1", "uuid-1"]}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "zona_repetida"


async def test_o_snapshot_traz_o_reportavel_e_nunca_o_que_e_so_envio(duas):
    """Section 8: the chip never echoes, so a preset and a scene are never reported.

    Seção 8: o chip nunca ecoa, então um preset e uma cena nunca são reportados.
    """
    cliente, auth, _classe = duas
    corpo = await _json(await cliente.get("/api/dps", headers=auth))
    dps = corpo["dps"]
    assert dps[str(ONLINE_1)] is True
    assert dps[str(VOLUME_1)] == 20
    assert dps[str(GRUPO)] == "solo"
    assert json.loads(dps[str(NOMES_ZONAS)]) == {"z": ["Sala", "Cozinha"]}
    assert str(PRESET_1) not in dps
    assert str(CENA) not in dps


async def test_o_snapshot_descreve_a_tabela_da_secao_8(duas):
    """The scene editor of the panel reads the table from here, and never guesses it.

    O editor de cenas do painel lê a tabela daqui, e nunca a adivinha.
    """
    cliente, auth, _classe = duas
    corpo = await _json(await cliente.get("/api/dps", headers=auth))
    tabela = {item["dpid"]: item for item in corpo["mapa"]}
    assert tabela[VOLUME_1] == {
        "dpid": VOLUME_1,
        "zona": 1,
        "funcao": "volume",
        "tipo": "value",
        "sentido": "rw",
        "valores": [],
    }
    assert tabela[ONLINE_1]["sentido"] == "reporte"
    assert tabela[PRESET_1]["sentido"] == "envio"
    assert tabela[PRESET_1]["valores"] == [f"cmd{n}" for n in range(1, 9)]
    assert tabela[CENA]["valores"] == [f"cena{n}" for n in range(1, 9)]
    assert tabela[NOMES_CENAS]["tipo"] == "string"
    assert tabela[NOMES_GRUPOS]["sentido"] == "reporte"
    # Why: section 14, only the inputs the hardware declares are offered.
    # Por que: seção 14, só as entradas que o hardware declara são oferecidas.
    assert tabela[ENTRADA_1]["valores"] == list(FONTES)
    assert tabela[ENTRADA_2 + 1]["valores"] == []


async def test_um_set_de_data_point_chega_a_caixa_do_bloco(duas):
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/dp/{VOLUME_1}", json={"v": 40}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    assert _caixa(classe, "uuid-1").chamadas == [("volume", 40)]
    assert _caixa(classe, "uuid-2").chamadas == []


async def test_um_set_de_data_point_de_report_nao_chega_a_caixa(duas):
    """Section 8: a report is only ever born of real state, so 104 takes no set.

    Seção 8: um report só nasce de estado real, então o 104 não aceita set.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/dp/{ONLINE_1}", json={"v": True}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "dp_somente_leitura"
    assert _caixa(classe, "uuid-1").chamadas == []


# Why: str.isdigit() is true for the superscript two and int() refuses it, so this path used
# to answer 500 with a traceback in the log, which a session holder could repeat at will.
# Por que: str.isdigit() é verdadeiro para o dois sobrescrito e o int() o recusa, então este
# caminho respondia 500 com traceback no log, que quem tem sessão podia repetir à vontade.
@pytest.mark.parametrize(
    "caminho", ["/api/dp/999", "/api/dp/abc", "/api/dp/0", "/api/dp/\u00b2", "/api/dp/\u0661"]
)
async def test_um_data_point_fora_do_contrato_e_recusado(duas, caminho):
    cliente, auth, classe = duas
    resposta = await cliente.post(caminho, json={"v": 10}, headers=auth)
    assert resposta.status == 404
    assert (await _json(resposta))["code"] == "dp_desconhecido"
    assert _caixa(classe, "uuid-1").chamadas == []


@pytest.mark.parametrize("valor", [300, -1, True, "40", None, [40], {"v": 40}])
async def test_um_valor_fora_do_tipo_do_data_point_e_recusado(duas, valor):
    """A value DP takes an integer of 0 to 100, and the JSON true is not one of them.

    Um DP value aceita um inteiro de 0 a 100, e o true do JSON não é um deles.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post(f"/api/dp/{VOLUME_1}", json={"v": valor}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "valor_invalido"
    assert _caixa(classe, "uuid-1").chamadas == []


async def test_um_set_num_bloco_vazio_responde_zona_offline(abrir):
    cliente, auth = await abrir({TIPO: _fabrica()})
    resposta = await cliente.post(f"/api/dp/{VOLUME_1}", json={"v": 40}, headers=auth)
    assert resposta.status == 503
    assert (await _json(resposta))["code"] == "zona_offline"


async def test_o_grupo_se_forma_pelo_mestre_e_cai_pelo_mestre(duas):
    """Section 14: the slave joins the master, and only the master takes the group down.

    Seção 14: o escravo entra no mestre, e só o mestre desfaz o grupo.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post("/api/grupo", json={"v": "grupo1"}, headers=auth)
    assert resposta.status == 200, await resposta.text()
    assert (await _json(resposta))["grupo"] == "grupo1"
    assert _caixa(classe, "uuid-2").chamadas == [("entrar_no_grupo", IP_1)]
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    assert [bloco["papel"] for bloco in corpo["zonas"][:2]] == ["mestre", "escravo"]
    resposta = await cliente.post("/api/grupo", json={"v": "solo"}, headers=auth)
    assert (await _json(resposta))["grupo"] == "solo"
    assert ("desfazer_grupo", None) in _caixa(classe, "uuid-1").chamadas
    assert ("desfazer_grupo", None) not in _caixa(classe, "uuid-2").chamadas


async def test_um_grupo_de_uma_caixa_so_nao_e_grupo(abrir):
    cliente, auth = await abrir(
        {TIPO: _fabrica()}, equipamentos=(_cadastro("uuid-1"),), zonas=("uuid-1",)
    )
    resposta = await cliente.post("/api/grupo", json={"v": "grupo1"}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "nao_suportado"


async def test_um_grupo_de_um_bloco_que_o_contrato_nao_tem_e_recusado(duas):
    """DP 132 carries nine groups and section 8 numbers six blocks; the wider enum loses.

    O DP 132 carrega nove grupos e a seção 8 numera seis blocos; o enum mais largo perde.
    """
    cliente, auth, classe = duas
    resposta = await cliente.post("/api/grupo", json={"v": "grupo9"}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "valor_invalido"
    assert _caixa(classe, "uuid-2").chamadas == []


async def test_um_grupo_misto_nunca_e_oferecido(abrir):
    """Section 14: a group only ever exists between speakers of the same domain.

    Seção 14: um grupo só existe entre caixas do mesmo domínio.
    """
    cliente, auth = await abrir(
        {TIPO: _fabrica(), OUTRO_TIPO: _fabrica(OUTRO_TIPO)},
        equipamentos=(
            _cadastro("uuid-1"),
            _cadastro("uuid-outra", tipo=OUTRO_TIPO, ip=IP_2, nome="Quarto"),
        ),
        zonas=("uuid-1", "uuid-outra"),
    )
    resposta = await cliente.post("/api/grupo", json={"v": "grupo1"}, headers=auth)
    assert resposta.status == 400
    assert (await _json(resposta))["code"] == "nao_suportado"


async def test_remover_um_equipamento_esvazia_o_bloco_e_nao_empurra_o_resto(duas, amb):
    """A shift would move the speaker of zone 2 into zone 1 in every automation.

    Um empurrão moveria a caixa da zona 2 para a zona 1 em toda automação.
    """
    cliente, auth, _classe = duas
    resposta = await cliente.delete("/api/equipamentos/uuid-1", headers=auth)
    assert resposta.status == 200, await resposta.text()
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    assert [bloco["identidade"] for bloco in corpo["zonas"][:2]] == ["", "uuid-2"]
    em_disco = json.loads((amb.dir_data / ARQUIVO_CONFIG).read_text(encoding="utf-8"))
    assert em_disco["zonas"] == ["", "uuid-2"]


async def test_remover_o_mestre_derruba_o_grupo(duas):
    """A group led by an equipment nobody has is a group nobody can take down.

    Um grupo liderado por um equipamento que ninguém tem é um grupo que ninguém desfaz.
    """
    cliente, auth, _classe = duas
    assert (await cliente.post("/api/grupo", json={"v": "grupo1"}, headers=auth)).status == 200
    assert (await cliente.delete("/api/equipamentos/uuid-1", headers=auth)).status == 200
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    assert corpo["grupo"] == "solo"
    assert [bloco["papel"] for bloco in corpo["zonas"][:2]] == ["", ""]


async def test_uma_zona_escrava_de_grupo_alheio_nao_e_desenhada_como_solo(duas):
    """The panel draws no role badge for a solo zone, so calling this one solo left the
    operator with volume, transport and input controls that only ever answer no.

    O painel não desenha selo de papel para uma zona solo, então chamar esta de solo deixava o
    operador com controles de volume, transporte e entrada que só respondem não.
    """
    cliente, auth, classe = duas
    _caixa(classe, "uuid-1").escravo_alheio = True
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    assert [bloco["papel"] for bloco in corpo["zonas"][:2]] == ["escravo", ""]


async def test_o_dp_de_cena_responde_o_mesmo_que_a_rota_de_cenas(duas):
    """Section 11: DP 131 runs a scene, so it answers the codes of the scene executor and not
    a 500 with a traceback for a scene that simply does not exist.

    Seção 11: o DP 131 executa uma cena, então ele responde os códigos do executor de cenas e
    não um 500 com traceback para uma cena que simplesmente não existe.
    """
    cliente, auth, _classe = duas
    resposta = await cliente.post("/api/dp/131", json={"v": "cena1"}, headers=auth)
    assert resposta.status == 404, await resposta.text()
    assert (await _json(resposta))["code"] == "cena_nao_encontrada"


def test_todo_codigo_do_executor_de_cenas_tem_status_no_dp_de_cena():
    """The other executor code reaches DP 131 the same way, and a code with no status here is
    a 500 with erro_interno for something the scenes route answers honestly.

    O outro código do executor chega ao DP 131 do mesmo jeito, e um código sem status aqui é um
    500 com erro_interno para algo que a rota de cenas responde honestamente.
    """
    from iphub import cenas as modulo_cenas
    from iphub.api.zonas import STATUS_POR_CODIGO

    assert STATUS_POR_CODIGO[modulo_cenas.CENA_NAO_ENCONTRADA] == 404
    assert STATUS_POR_CODIGO[modulo_cenas.CENA_EM_CURSO] == 409


async def test_uma_ordem_salva_com_bloco_que_nao_e_multiroom_sobe_com_ele_vazio(abrir):
    """The route validates an order and config.json does not, so a file edited by hand, or
    left behind by an equipment that changed tipo, must not boot a zone nothing can command.

    A rota valida uma ordem e o config.json não, então um arquivo editado na mão, ou deixado
    por um equipamento que trocou de tipo, não pode subir uma zona que ninguém comanda.
    """
    projetor = "projetor_falso"
    cliente, auth = await abrir(
        {TIPO: _fabrica(), projetor: _fabrica(projetor, categoria="projetor")},
        equipamentos=(
            _cadastro("uuid-1", ip=IP_1, nome="Sala"),
            _cadastro("uuid-2", tipo=projetor, ip=IP_2, nome="Projetor"),
        ),
        zonas=("uuid-1", "uuid-2"),
    )
    corpo = await _json(await cliente.get("/api/zonas", headers=auth))
    assert corpo["zonas"][0]["identidade"] == "uuid-1"
    # The route refuses a block that is not a multiroom equipment, and the saved order has to
    # meet the same rule: a projector in a zone block publishes a zone nothing can command.
    # A rota recusa um bloco que não é equipamento multiroom, e a ordem salva precisa cumprir
    # a mesma regra: um projetor num bloco de zona publica uma zona que ninguém comanda.
    assert corpo["zonas"][1]["identidade"] == ""


async def test_um_corpo_json_fundo_demais_e_corpo_invalido_e_nunca_erro_interno(duas):
    """Section 11: a body this daemon cannot read answers a stable code, never a 500 with a
    traceback in the log.

    Seção 11: um corpo que este daemon não consegue ler responde um código estável, nunca um
    500 com traceback no log.
    """
    cliente, auth, _classe = duas
    # Why: the scenes route takes a body of 64 kB, so a body deep enough to exhaust the
    # recursion of the parser fits inside its ceiling and really reaches json.loads.
    # Por que: a rota de cenas aceita corpo de 64 kB, então um corpo fundo o bastante para
    # esgotar a recursão do parser cabe no teto dela e chega mesmo ao json.loads.
    fundo = "[" * 12000 + "]" * 12000
    resposta = await cliente.post(
        "/api/cenas",
        data=fundo,
        headers={**auth, "Content-Type": "application/json"},
    )
    assert resposta.status != 500, await resposta.text()
    assert (await _json(resposta))["code"] == "corpo_invalido"

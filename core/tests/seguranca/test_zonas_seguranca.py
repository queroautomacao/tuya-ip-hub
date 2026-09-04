# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 9 over the zone and scene routes, with every test attacking a rule.

Nobody without a session commands a zone, nobody from another site commands one either, and
the four headers are on every answer, refusals included. Past the gate the attacks are the
ones section 8 pays for: a block occupied by an equipment that is not a multiroom one, a
scene step that writes a data point nobody may write, and a scene name that does not fit the
255 bytes of DP 134. Each one is checked on the FILE as well, because a refusal that already
wrote is not a refusal.

Seção 9 sobre as rotas de zona e de cena, com todo teste atacando uma regra.

Ninguém sem sessão comanda uma zona, ninguém de outro site comanda também, e os quatro
cabeçalhos estão em toda resposta, inclusive nas recusas. Passado o portão, os ataques são os
que a seção 8 paga: um bloco ocupado por um equipamento que não é multiroom, um passo de cena
que escreve um data point que ninguém escreve, e um nome de cena que não cabe nos 255 bytes
do DP 134. Cada um é conferido também no ARQUIVO, porque uma recusa que já gravou não é
recusa.
"""

import json

import pytest

from iphub.api.cenas import CORPO_MAXIMO_CENAS
from iphub.config import ARQUIVO as ARQUIVO_CONFIG
from iphub.config import Cadastro, Config
from iphub.drivers.base import Driver
from iphub.drivers.manifesto import Manifesto
from iphub.portao import CABECALHOS

TIPO = "multiroom_falso"
TIPO_DE_PROJETOR = "projetor_falso"

VOLUME_1 = 101
ONLINE_1 = 104
CENA = 131
NOMES_ZONAS = 133

ALHEIA = "http://evil.example.com"

ROTAS = (
    ("GET", "/api/zonas"),
    ("POST", "/api/zonas"),
    ("GET", "/api/dps"),
    ("POST", f"/api/dp/{VOLUME_1}"),
    ("POST", "/api/grupo"),
    ("GET", "/api/cenas"),
    ("POST", "/api/cenas"),
    ("POST", "/api/cenas/1/executar"),
)


def _manifesto(tipo: str, categoria: str, capacidades: tuple[str, ...]) -> Manifesto:
    textos = {"descricao": "Aparelho de teste"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Aparelho", "en": "Device"},
        categoria=categoria,
        capacidades=capacidades,
        textos={"pt": dict(textos), "en": dict(textos)},
    )


class Caixa(Driver):
    """A multiroom driver that records what reached it, so a test proves what never did.

    Um driver multiroom que guarda o que chegou nele, para um teste provar o que nunca chegou.
    """

    MANIFESTO = _manifesto(TIPO, "multiroom", ("volume", "fonte", "tocar", "pausar", "agrupar"))
    instancias: list["Caixa"] = []

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self.chamadas: list[tuple[str, object]] = []
        self._defina(online=True, volume=20, fonte="wifi", fontes=("wifi",))
        type(self).instancias.append(self)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        self.chamadas.append((acao, valor))
        return None

    async def entrar_no_grupo(self, ip_do_mestre: object) -> str | None:
        self.chamadas.append(("entrar_no_grupo", ip_do_mestre))
        return None

    async def desfazer_grupo(self) -> str | None:
        self.chamadas.append(("desfazer_grupo", None))
        return None

    async def volume_de_escravo(self, ip: object, valor: object) -> str | None:
        self.chamadas.append(("volume_de_escravo", (ip, valor)))
        return None

    async def ler_grupo(self) -> None:
        return None

    def marcar_grupo(self, dentro: bool) -> None:
        return None

    def espelhar(self, tocando: str | None, reproduzindo: bool | None = None) -> None:
        return None

    escravo_alheio = False

    def e_escravo(self) -> bool:

        return self.escravo_alheio

    def saiu_do_grupo(self) -> bool:
        return False


class Projetor(Driver):
    """Not a multiroom equipment, so it never occupies a block of section 8.

    Não é equipamento multiroom, então ele nunca ocupa um bloco da seção 8.
    """

    MANIFESTO = _manifesto(TIPO_DE_PROJETOR, "projetor", ("ligar", "desligar"))

    def __init__(self, cadastro: Cadastro) -> None:
        super().__init__(cadastro)
        self._defina(online=True, ligado=False)

    async def executar(self, acao: str, valor: object = None) -> str | None:
        return None


CATALOGO = {TIPO: Caixa, TIPO_DE_PROJETOR: Projetor}
EQUIPAMENTOS = (
    Cadastro(identidade="uuid-1", tipo=TIPO, nome="Sala", ip="192.0.2.11"),
    Cadastro(identidade="uuid-projetor", tipo=TIPO_DE_PROJETOR, nome="Sala", ip="192.0.2.20"),
)


@pytest.fixture
async def hub(fabrica_cliente, posse, bearer):
    Caixa.instancias = []
    cliente = await fabrica_cliente(
        catalogo=CATALOGO, config=Config(equipamentos=EQUIPAMENTOS, zonas=("uuid-1",))
    )
    return cliente, bearer(await posse(cliente))


def _do_disco(amb) -> dict:
    caminho = amb.dir_data / ARQUIVO_CONFIG
    return json.loads(caminho.read_text(encoding="utf-8")) if caminho.is_file() else {}


def _caixa() -> Caixa:
    return Caixa.instancias[0]


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_nenhuma_rota_de_zona_responde_sem_sessao(hub, metodo, caminho):
    cliente, _auth = hub
    resposta = await cliente.request(metodo, caminho, json={"v": 40})
    assert resposta.status == 401
    assert (await resposta.json())["ok"] is False
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome
    assert _caixa().chamadas == []


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_nenhuma_rota_de_zona_responde_a_outro_site(hub, metodo, caminho):
    """Section 9: a present Origin that is not this host is 403, which closes CSRF.

    Seção 9: um Origin presente que não é este host é 403, o que fecha o CSRF.
    """
    cliente, auth = hub
    resposta = await cliente.request(
        metodo, caminho, json={"v": 40}, headers={**auth, "Origin": ALHEIA}
    )
    assert resposta.status == 403
    assert await resposta.json() == {"ok": False, "code": "origem_nao_permitida"}
    assert _caixa().chamadas == []


@pytest.mark.parametrize(("metodo", "caminho"), ROTAS)
async def test_os_quatro_cabecalhos_estao_na_resposta_com_sessao(hub, metodo, caminho):
    cliente, auth = hub
    resposta = await cliente.request(metodo, caminho, json={"v": 40}, headers=auth)
    for nome, valor in CABECALHOS.items():
        assert resposta.headers.get(nome) == valor, nome


async def test_um_equipamento_que_nao_e_multiroom_nao_ocupa_um_bloco(hub, amb):
    """Section 6: a zone IS a multiroom equipment; a projector has no volume of a zone.

    Seção 6: uma zona É um equipamento multiroom; um projetor não tem volume de zona.
    """
    cliente, auth = hub
    resposta = await cliente.post(
        "/api/zonas", json={"zonas": ["uuid-1", "uuid-projetor"]}, headers=auth
    )
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "eq_nao_multiroom"
    assert _do_disco(amb).get("zonas", []) != ["uuid-1", "uuid-projetor"]
    corpo = await (await cliente.get("/api/zonas", headers=auth)).json()
    assert [bloco["identidade"] for bloco in corpo["zonas"]][:2] == ["uuid-1", ""]


@pytest.mark.parametrize("ordem", [["uuid-9"], "uuid-1", [1], [["uuid-1"]], {"1": "uuid-1"}])
async def test_uma_ordem_que_nao_e_lista_de_identidade_e_recusada(hub, amb, ordem):
    cliente, auth = hub
    resposta = await cliente.post("/api/zonas", json={"zonas": ordem}, headers=auth)
    assert resposta.status in (400, 404)
    assert (await resposta.json())["ok"] is False
    assert _do_disco(amb).get("zonas", []) in ([], ["uuid-1"])


@pytest.mark.parametrize("dpid", [ONLINE_1, NOMES_ZONAS, 103, CENA + 2])
async def test_um_passo_de_cena_nao_escreve_o_que_ninguem_escreve(hub, amb, dpid):
    """Section 8: a report is only ever born of real state, so a scene never writes one.

    Seção 8: um report só nasce de estado real, então uma cena nunca escreve um.
    """
    cliente, auth = hub
    corpo = {"cenas": [{"nome": "Filme", "passos": [{"dpid": dpid, "valor": True}]}]}
    resposta = await cliente.post("/api/cenas", json=corpo, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "cenas_invalidas"
    assert _do_disco(amb).get("cenas", []) == []


async def test_uma_cena_nao_dispara_uma_cena(hub, amb):
    """Two scenes naming each other would be a hub that never stops.

    Duas cenas nomeando uma à outra seriam um hub que nunca para.
    """
    cliente, auth = hub
    corpo = {"cenas": [{"nome": "Laco", "passos": [{"dpid": CENA, "valor": "cena1"}]}]}
    resposta = await cliente.post("/api/cenas", json=corpo, headers=auth)
    assert resposta.status == 400
    codigos = [problema["codigo"] for problema in (await resposta.json())["problemas"]]
    assert codigos == ["cena_dp_proibido"]
    assert _do_disco(amb).get("cenas", []) == []


async def test_um_nome_de_cena_que_nao_cabe_no_dp_134_e_recusado(hub, amb):
    cliente, auth = hub
    cena = {"nome": "N" * 40, "passos": [{"dpid": VOLUME_1, "valor": 10}]}
    resposta = await cliente.post("/api/cenas", json={"cenas": [cena] * 8}, headers=auth)
    assert resposta.status == 400
    codigos = [problema["codigo"] for problema in (await resposta.json())["problemas"]]
    assert "nomes_longos" in codigos
    assert _do_disco(amb).get("cenas", []) == []


async def test_um_set_de_data_point_de_report_nunca_alcanca_a_caixa(hub):
    cliente, auth = hub
    resposta = await cliente.post(f"/api/dp/{ONLINE_1}", json={"v": True}, headers=auth)
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "dp_somente_leitura"
    assert _caixa().chamadas == []


async def test_um_corpo_maior_que_o_teto_da_rota_e_recusado(hub, amb):
    """A body that does not fit is refused whole, and nothing of it is saved.

    Um corpo que não cabe é recusado inteiro, e nada dele é gravado.
    """
    cliente, auth = hub
    cena = {"nome": "Filme", "passos": [{"dpid": VOLUME_1, "valor": 10, "espera_ms": 0}]}
    gigante = json.dumps({"cenas": [cena], "sobra": "a" * CORPO_MAXIMO_CENAS})
    resposta = await cliente.post(
        "/api/cenas",
        data=gigante,
        headers={**auth, "Content-Type": "application/json"},
    )
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "corpo_invalido"
    assert _do_disco(amb).get("cenas", []) == []


async def test_um_corpo_que_nao_e_objeto_nao_comanda_nada(hub):
    cliente, auth = hub
    for bruto in ("[]", "40", '"40"', "null", "{"):
        resposta = await cliente.post(
            f"/api/dp/{VOLUME_1}",
            data=bruto,
            headers={**auth, "Content-Type": "application/json"},
        )
        assert resposta.status in (400, 404), bruto
        assert (await resposta.json())["ok"] is False
    assert _caixa().chamadas == []

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The schedule routes: the list is saved whole, refused by field, and runs scenes on the clock."""

from datetime import datetime
from zoneinfo import ZoneInfo

from iphub.api.comum import DESPERTADOR, config_de
from iphub.cenas import Cena
from iphub.config import Config

ROTAS = (("GET", "/api/agendamentos"), ("POST", "/api/agendamentos"))
AGENDA = {"cena": 1, "hora": "07:30", "dias": [0, 1, 2, 3, 4]}


async def test_as_rotas_pedem_sessao(cliente):
    for metodo, caminho in ROTAS:
        resposta = await cliente.request(metodo, caminho, json={"agendamentos": []})
        assert resposta.status == 401, caminho


async def test_salvar_e_ler_com_o_fuso(cliente, posse, bearer, amb):
    token = await posse(cliente)
    vazio = await (await cliente.get("/api/agendamentos", headers=bearer(token))).json()
    assert vazio["agendamentos"] == [] and vazio["maximo"] == 32 and vazio["fuso"] == ""
    assert len(vazio["agora"]) == 5 and vazio["fuso_efetivo"]
    resposta = await cliente.post(
        "/api/agendamentos",
        json={"fuso": "America/Sao_Paulo", "agendamentos": [AGENDA]},
        headers=bearer(token),
    )
    assert resposta.status == 200, await resposta.text()
    lido = await resposta.json()
    assert lido["fuso"] == "America/Sao_Paulo" and lido["fuso_efetivo"] == "America/Sao_Paulo"
    assert lido["agendamentos"] == [{**AGENDA, "ativo": True}]
    cfg = config_de(cliente.server.app)
    assert cfg.fuso == "America/Sao_Paulo" and cfg.agendamentos[0].cena == 1
    # It survives a restart: the file holds it.
    from iphub.config import carregar

    assert carregar(amb.dir_data).agendamentos == cfg.agendamentos


async def test_um_fuso_que_a_caixa_nao_conhece_e_recusado(cliente, posse, bearer):
    token = await posse(cliente)
    resposta = await cliente.post(
        "/api/agendamentos",
        json={"fuso": "Marte/Olympus", "agendamentos": []},
        headers=bearer(token),
    )
    assert resposta.status == 400
    assert (await resposta.json())["code"] == "fuso_invalido"


async def test_a_lista_e_recusada_por_campo_e_nada_e_gravado(cliente, posse, bearer):
    token = await posse(cliente)
    resposta = await cliente.post(
        "/api/agendamentos",
        json={"agendamentos": [{**AGENDA, "hora": "25:00"}, {**AGENDA, "cena": 99}]},
        headers=bearer(token),
    )
    assert resposta.status == 400
    corpo = await resposta.json()
    assert corpo["code"] == "agendamentos_invalidos"
    assert {p["campo"] for p in corpo["problemas"]} == {
        "agendamentos[0].hora",
        "agendamentos[1].cena",
    }
    assert config_de(cliente.server.app).agendamentos == ()


async def test_o_relogio_do_hub_roda_a_cena_na_hora(fabrica_cliente, relogio):
    """The clock reaches the executor of the scenes: a step lands on the driver."""
    from iphub.agenda import Agendamento
    from iphub.cenas import Passo
    from iphub.config import Cadastro
    from iphub.drivers.base import Driver
    from iphub.drivers.manifesto import Manifesto

    chamadas: list[tuple[str, object]] = []

    class Falso(Driver):
        MANIFESTO = Manifesto(
            tipo="falso",
            rotulo={"pt": "Falso", "en": "Fake"},
            categoria="audio",
            capacidades=("volume",),
            textos={"pt": {"descricao": "x"}, "en": {"descricao": "x"}},
        )

        def __init__(self, cadastro) -> None:
            super().__init__(cadastro)
            self._defina(online=True, volume=10)

        async def executar(self, acao: str, valor: object = None) -> str | None:
            chamadas.append((acao, valor))
            return None

    momento = datetime(2026, 9, 14, 7, 30, tzinfo=ZoneInfo("America/Sao_Paulo")).timestamp()
    relogio.agora = momento - 60
    cliente = await fabrica_cliente(
        catalogo={"falso": Falso},
        config=Config(
            equipamentos=(Cadastro(identidade="uuid-1", tipo="falso", ip="192.0.2.10"),),
            cenas=(
                Cena(nome="Acordar", passos=(Passo("uuid-1", "volume", 30, 0),), intervalo_ms=0),
            ),
            fuso="America/Sao_Paulo",
            agendamentos=(Agendamento(1, "07:30", (0,)),),
        ),
        agora=relogio,
    )
    despertador = cliente.server.app[DESPERTADOR]
    assert despertador.tique() == []
    relogio.agora = momento
    assert despertador.tique() == [1]
    assert despertador.tique() == []
    import asyncio

    for _ in range(100):
        if chamadas:
            break
        await asyncio.sleep(0.01)
    assert chamadas == [("volume", 30)]

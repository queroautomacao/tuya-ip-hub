# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6 under attack: the gate refuses before the driver and one bad driver stays alone.

Seção 6 sob ataque: o portão recusa antes do driver e um driver ruim fica sozinho.
"""

import asyncio

import pytest

from iphub.config import Cadastro
from iphub.drivers.base import CONTRATO_QUEBRADO, DETALHES, TIPO_DESCONHECIDO, Driver
from iphub.drivers.gestor import (
    ERRO_APARELHO,
    EquipamentoDesconhecido,
    Gestor,
    IdentidadeDuplicada,
)
from iphub.drivers.manifesto import Auth, Estado, Manifesto


def _manifesto(
    tipo: str = "exemplo",
    *,
    capacidades: tuple[str, ...] = ("ligar", "volume"),
    auth: Auth = Auth.NENHUMA,
) -> Manifesto:
    textos = {"descricao": "Exemplo", "auth_ajuda": "Ajuda"}
    return Manifesto(
        tipo=tipo,
        rotulo={"pt": "Exemplo", "en": "Example"},
        categoria="outro",
        capacidades=capacidades,
        auth=auth,
        textos={"pt": dict(textos), "en": dict(textos)},
    )


def _cadastro(
    identidade: str = "uuid-1", tipo: str = "exemplo", ip: str = "192.0.2.10"
) -> Cadastro:
    return Cadastro(identidade=identidade, tipo=tipo, nome="Sala", ip=ip)


def _fabrica(manifesto: Manifesto | None = None, **comportamento: object) -> type[Driver]:
    """A driver with knobs, so a test makes it break exactly where it wants to attack.

    Um driver com botões, para um teste quebrá-lo exatamente onde ele quer atacar.
    """

    class Falso(Driver):
        MANIFESTO = manifesto if manifesto is not None else _manifesto()
        instancias: list["Falso"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            if comportamento.get("estoura_ao_nascer"):
                raise RuntimeError("nao nasci")
            super().__init__(cadastro)
            self.executados: list[tuple[str, object]] = []
            self.autenticacoes = 0
            self.iniciado = False
            self.parado = False
            type(self).instancias.append(self)

        async def iniciar(self) -> None:
            self.iniciado = True
            if comportamento.get("estoura_no_iniciar"):
                raise RuntimeError("nao abri")

        async def parar(self) -> None:
            self.parado = True
            if comportamento.get("estoura_no_parar"):
                raise RuntimeError("nao fechei")

        async def executar(self, acao: str, valor: object = None) -> str | None:
            self.executados.append((acao, valor))
            if comportamento.get("estoura_no_executar"):
                raise RuntimeError("quebrei")
            return comportamento.get("resposta")

        async def autenticar(self) -> str:
            self.autenticacoes += 1
            if comportamento.get("estoura_no_autenticar"):
                raise RuntimeError("sem par")
            return comportamento.get("resultado", "pareado")

        def estado(self):
            if comportamento.get("estoura_no_estado"):
                raise RuntimeError("estado quebrado")
            if "estado_solto" in comportamento:
                return comportamento["estado_solto"]
            return super().estado()

    Falso.instancias = []
    return Falso


@pytest.fixture
async def monta():
    """Builds a running gestor and guarantees it is stopped when the test ends.

    Constrói um gestor rodando e garante que ele para quando o teste termina.
    """
    vivos: list[Gestor] = []

    async def criar(catalogo: dict, cadastros=(), **pecas) -> Gestor:
        gestor = Gestor(catalogo, cadastros, **pecas)
        vivos.append(gestor)
        await gestor.iniciar()
        return gestor

    yield criar
    for gestor in vivos:
        await gestor.parar()


async def test_acao_fora_das_capacidades_nunca_chega_ao_driver(monta):
    """Section 6: the manifest refuses, so no driver writes a method only to say no.

    Seção 6: o manifesto recusa, então nenhum driver escreve método só para dizer não.
    """
    classe = _fabrica(_manifesto(capacidades=("ligar",)))
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.executar("uuid-1", "volume", 30) == "nao_suportado"
    assert classe.instancias[0].executados == []


@pytest.mark.parametrize("acao", ["formatar_o_disco", "", "LIGAR", "ligar ", "__init__"])
async def test_acao_fora_do_vocabulario_nunca_chega_ao_driver(monta, acao):
    classe = _fabrica()
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.executar("uuid-1", acao) == "nao_suportado"
    assert classe.instancias[0].executados == []


async def test_acao_declarada_chega_ao_driver_com_o_valor(monta):
    classe = _fabrica()
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.executar("uuid-1", "volume", 30) is None
    assert classe.instancias[0].executados == [("volume", 30)]


async def test_identidade_desconhecida_responde_eq_nao_encontrado(monta):
    classe = _fabrica()
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.executar("uuid-9", "ligar") == "eq_nao_encontrado"
    assert classe.instancias[0].executados == []


async def test_driver_que_estoura_responde_erro_aparelho_e_o_gestor_segue(monta):
    """One driver that raises never takes the daemon down, and it answers the next command.

    Um driver que estoura nunca derruba o daemon, e ele responde ao comando seguinte.
    """
    classe = _fabrica(estoura_no_executar=True)
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.executar("uuid-1", "ligar") == "erro_aparelho"
    assert await gestor.executar("uuid-1", "ligar") == "erro_aparelho"
    assert len(classe.instancias[0].executados) == 2


@pytest.mark.parametrize("resposta", ["deu_ruim", "", "ok", 7, True, "NAO_SUPORTADO"])
async def test_codigo_inventado_pelo_driver_vira_erro_aparelho(monta, resposta):
    """Section 11: a code of the driver's own would reach the panel as an untranslated phrase.

    Seção 11: um código próprio do driver chegaria ao painel como frase sem tradução.
    """
    classe = _fabrica(resposta=resposta)
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.executar("uuid-1", "ligar") == "erro_aparelho"


@pytest.mark.parametrize("resposta", ["eq_offline", "invalid_value", "auth_pendente"])
async def test_codigo_estavel_do_driver_passa_intacto(monta, resposta):
    classe = _fabrica(resposta=resposta)
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.executar("uuid-1", "ligar") == resposta


async def test_cadastro_de_tipo_desconhecido_sobrevive_e_e_reportado_offline(monta):
    """Losing the registration of the integrator would be worse than reporting it offline.

    Perder o cadastro do integrador seria pior do que reportá-lo offline.
    """
    gestor = await monta({}, [_cadastro(tipo="driver_que_saiu")])
    assert gestor.cadastros == (_cadastro(tipo="driver_que_saiu"),)
    estado = gestor.estados()["uuid-1"]
    assert estado.online is False
    assert estado.detalhe == TIPO_DESCONHECIDO
    # Why: section 11, the tipo is a name the integrator typed and it would reach the screen
    # as a phrase the panel cannot translate; the log is where it belongs.
    # Por que: seção 11, o tipo é um nome que o integrador digitou e chegaria à tela como uma
    # frase que o painel não sabe traduzir; o log é o lugar dele.
    assert "driver_que_saiu" not in estado.detalhe
    assert gestor.manifesto("uuid-1") is None
    assert await gestor.executar("uuid-1", "ligar") == "nao_suportado"


async def test_driver_que_estoura_ao_nascer_e_reportado_e_nao_derruba_o_gestor(monta):
    classe = _fabrica(estoura_ao_nascer=True)
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    estado = gestor.estados()["uuid-1"]
    assert estado.online is False
    assert estado.detalhe == ERRO_APARELHO
    assert "nao nasci" not in estado.detalhe
    assert await gestor.executar("uuid-1", "ligar") == "eq_offline"


async def test_driver_que_estoura_no_iniciar_nao_para_os_outros(monta):
    ruim = _fabrica(_manifesto("ruim"), estoura_no_iniciar=True)
    bom = _fabrica(_manifesto("bom"))
    gestor = await monta(
        {"ruim": ruim, "bom": bom}, [_cadastro("uuid-1", "ruim"), _cadastro("uuid-2", "bom")]
    )
    assert ruim.instancias[0].iniciado is True
    assert bom.instancias[0].iniciado is True
    assert gestor.estados()["uuid-1"].online is False


async def test_estados_devolve_um_estado_tipado_por_cadastro(monta):
    classe = _fabrica()
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    estados = gestor.estados()
    assert set(estados) == {"uuid-1"}
    assert isinstance(estados["uuid-1"], Estado)
    assert not isinstance(estados["uuid-1"], dict)


async def test_manifesto_de_identidade_desconhecida_e_none(monta):
    gestor = await monta({"exemplo": _fabrica()}, [_cadastro()])
    assert gestor.manifesto("uuid-1").tipo == "exemplo"
    assert gestor.manifesto("uuid-9") is None


async def test_atualizar_cadastro_reconstroi_o_driver_com_o_endereco_novo(monta):
    """The driver read the address when it was born, so a new address is a new driver.

    O driver leu o endereço quando nasceu, então um endereço novo é um driver novo.
    """
    classe = _fabrica()
    gestor = await monta({"exemplo": classe}, [_cadastro(ip="192.0.2.10")])
    assert await gestor.atualizar_cadastro(_cadastro(ip="192.0.2.11")) == (
        _cadastro(ip="192.0.2.11"),
    )
    velho, novo = classe.instancias
    assert velho.parado is True
    assert novo.cadastro.ip == "192.0.2.11"


async def test_cadastrar_identidade_repetida_e_recusado_e_nada_muda(monta):
    classe = _fabrica()
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    with pytest.raises(IdentidadeDuplicada) as erro:
        await gestor.cadastrar(_cadastro(ip="192.0.2.99"))
    assert erro.value.codigo == "identidade_duplicada"
    assert gestor.cadastros == (_cadastro(),)
    assert len(classe.instancias) == 1


def test_construir_com_identidades_repetidas_estoura():
    with pytest.raises(IdentidadeDuplicada):
        Gestor({}, [_cadastro(), _cadastro(ip="192.0.2.99")])


async def test_remover_e_atualizar_desconhecidos_estouram(monta):
    gestor = await monta({"exemplo": _fabrica()}, [_cadastro()])
    for chamada in (gestor.remover("uuid-9"), gestor.atualizar_cadastro(_cadastro("uuid-9"))):
        with pytest.raises(EquipamentoDesconhecido) as erro:
            await chamada
        assert erro.value.codigo == "eq_nao_encontrado"
    assert gestor.cadastros == (_cadastro(),)


@pytest.mark.parametrize("resultado", ["pareado", "aguardando", "falhou"])
async def test_autenticar_devolve_o_que_o_driver_respondeu(monta, resultado):
    classe = _fabrica(_manifesto(auth=Auth.CODIGO), resultado=resultado)
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.autenticar("uuid-1") == resultado
    assert classe.instancias[0].autenticacoes == 1


@pytest.mark.parametrize("resultado", ["sim", "", "PAREADO", None, 1, "erro_aparelho"])
async def test_resultado_de_pareamento_fora_do_contrato_vira_falhou(monta, resultado):
    classe = _fabrica(_manifesto(auth=Auth.CODIGO), resultado=resultado)
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.autenticar("uuid-1") == "falhou"


async def test_driver_que_estoura_ao_parear_responde_falhou(monta):
    classe = _fabrica(_manifesto(auth=Auth.CODIGO), estoura_no_autenticar=True)
    gestor = await monta({"exemplo": classe}, [_cadastro()])
    assert await gestor.autenticar("uuid-1") == "falhou"


async def test_driver_que_nunca_implementou_autenticar_responde_falhou(monta):
    """The base raises to name the guilty driver; the gestor keeps the daemon on its feet.

    A base estoura para nomear o driver culpado; o gestor mantém o daemon de pé.
    """

    class Esquecido(Driver):
        MANIFESTO = _manifesto("esquecido", auth=Auth.POPUP_NO_APARELHO)

    gestor = await monta({"esquecido": Esquecido}, [_cadastro(tipo="esquecido")])
    assert await gestor.autenticar("uuid-1") == "falhou"
    assert await gestor.executar("uuid-1", "ligar") == "nao_suportado"


async def test_autenticar_identidade_desconhecida_estoura(monta):
    gestor = await monta({"exemplo": _fabrica()}, [_cadastro()])
    with pytest.raises(EquipamentoDesconhecido):
        await gestor.autenticar("uuid-9")


async def test_parar_fecha_todo_driver_mesmo_com_um_que_estoura():
    ruim = _fabrica(_manifesto("ruim"), estoura_no_parar=True)
    bom = _fabrica(_manifesto("bom"))
    gestor = Gestor(
        {"ruim": ruim, "bom": bom}, [_cadastro("uuid-1", "ruim"), _cadastro("uuid-2", "bom")]
    )
    await gestor.iniciar()
    await gestor.parar()
    assert ruim.instancias[0].parado is True
    assert bom.instancias[0].parado is True
    await gestor.parar()


async def test_driver_que_estoura_no_estado_nao_derruba_a_listagem_dos_outros(monta):
    """Section 6: the gestor is the enforcer of estado(), so one bad driver stays alone.

    Seção 6: o gestor é o fiscal do estado(), então um driver ruim fica sozinho.
    """
    ruim = _fabrica(_manifesto("ruim"), estoura_no_estado=True)
    bom = _fabrica(_manifesto("bom"))
    gestor = await monta(
        {"ruim": ruim, "bom": bom}, [_cadastro("uuid-1", "ruim"), _cadastro("uuid-2", "bom")]
    )
    estados = gestor.estados()
    assert estados["uuid-1"] == Estado(online=False, detalhe=CONTRATO_QUEBRADO)
    assert isinstance(estados["uuid-2"], Estado)
    assert estados["uuid-2"].detalhe == ""


@pytest.mark.parametrize("solto", [{"online": True}, "online", 7, None])
async def test_estado_que_nao_e_o_dataclass_vira_contrato_quebrado(monta, solto):
    """A loose dict in one driver would turn the listing of EVERY equipment into a 500.

    Um dict solto num driver transformaria a listagem de TODO equipamento num 500.
    """
    ruim = _fabrica(_manifesto("ruim"), estado_solto=solto)
    bom = _fabrica(_manifesto("bom"))
    gestor = await monta(
        {"ruim": ruim, "bom": bom}, [_cadastro("uuid-1", "ruim"), _cadastro("uuid-2", "bom")]
    )
    estados = gestor.estados()
    assert estados["uuid-1"] == Estado(online=False, detalhe=CONTRATO_QUEBRADO)
    assert isinstance(estados["uuid-2"], Estado) and estados["uuid-2"].detalhe == ""
    assert await gestor.executar("uuid-2", "ligar") is None


async def test_todo_detalhe_que_o_gestor_publica_e_um_codigo_do_vocabulario(monta):
    """Section 11: detalhe is empty or one code of DETALHES, never a phrase to read.

    Seção 11: o detalhe é vazio ou um código de DETALHES, nunca uma frase para ler.
    """
    nasceu_morto = _fabrica(_manifesto("nasceu_morto"), estoura_ao_nascer=True)
    sem_estado = _fabrica(_manifesto("sem_estado"), estoura_no_estado=True)
    solto = _fabrica(_manifesto("solto"), estado_solto={"online": True})
    gestor = await monta(
        {"nasceu_morto": nasceu_morto, "sem_estado": sem_estado, "solto": solto},
        [
            _cadastro("uuid-1", "nasceu_morto"),
            _cadastro("uuid-2", "sem_estado"),
            _cadastro("uuid-3", "solto"),
            _cadastro("uuid-4", "driver_que_saiu"),
        ],
    )
    detalhes = {i: estado.detalhe for i, estado in gestor.estados().items()}
    assert len(detalhes) == 4
    for identidade, detalhe in detalhes.items():
        assert detalhe in DETALHES, f"{identidade} published {detalhe!r}"


async def test_trocar_catalogo_refaz_so_o_tipo_nomeado(monta):
    """Section 7: a driver that was saved is built again, and every other keeps its session.

    Seção 7: um driver que foi salvo é montado de novo, e todo outro mantém a sessão dele.
    """
    velho = _fabrica(_manifesto("matriz"))
    vizinho = _fabrica(_manifesto("projetor"))
    gestor = await monta(
        {"matriz": velho, "projetor": vizinho},
        [_cadastro("uuid-1", "matriz"), _cadastro("uuid-2", "projetor")],
    )
    novo = _fabrica(_manifesto("matriz"))
    await gestor.trocar_catalogo({"matriz": novo, "projetor": vizinho}, refazer=("matriz",))
    assert velho.instancias[0].parado is True
    assert len(novo.instancias) == 1 and novo.instancias[0].iniciado is True
    # Why: rebuilding a driver nobody asked about would drop the connection of every device on
    # the installation to publish a file none of them uses.
    # Por que: refazer um driver que ninguém pediu derrubaria a conexão de todo aparelho da
    # instalação para publicar um arquivo que nenhum deles usa.
    assert len(vizinho.instancias) == 1 and vizinho.instancias[0].parado is False
    assert await gestor.executar("uuid-1", "ligar") is None
    assert novo.instancias[0].executados == [("ligar", None)]
    assert velho.instancias[0].executados == []


async def test_um_tipo_desconhecido_vive_quando_o_driver_chega(monta):
    """A registration outlives the driver it names, so the JSON saved later revives it.

    Um cadastro sobrevive ao driver que ele nomeia, então o JSON salvo depois o revive.
    """
    gestor = await monta({}, [_cadastro("uuid-1", "matriz")])
    assert gestor.estados()["uuid-1"].detalhe == TIPO_DESCONHECIDO
    chegou = _fabrica(_manifesto("matriz"))
    await gestor.trocar_catalogo({"matriz": chegou}, refazer=("matriz",))
    assert len(chegou.instancias) == 1
    assert gestor.estados()["uuid-1"].detalhe == ""
    assert await gestor.executar("uuid-1", "ligar") is None


def _fabrica_de_sessao(fio: dict) -> type[Driver]:
    """A driver that keeps on the wire the session it opened, until the test lets it go.

    Um driver que mantém no fio a sessão que abriu, até o teste soltá-lo.
    """

    class Falso(Driver):
        MANIFESTO = _manifesto("matriz")
        instancias: list["Falso"] = []

        def __init__(self, cadastro: Cadastro) -> None:
            super().__init__(cadastro)
            self.no_poll = asyncio.Event()
            self.liberar = asyncio.Event()
            type(self).instancias.append(self)

        async def iniciar(self) -> None:
            if fio["em_poll"]:
                fio["colisao"] = True
            fio["sessoes"] += 1

        async def parar(self) -> None:
            fio["sessoes"] -= 1

        async def atualizar(self) -> None:
            fio["polls"] += 1
            fio["em_poll"] = True
            self.no_poll.set()
            try:
                await self.liberar.wait()
            finally:
                fio["em_poll"] = False

    Falso.instancias = []
    return Falso


def _fio() -> dict:
    return {"em_poll": False, "colisao": False, "polls": 0, "sessoes": 0}


async def test_trocar_o_driver_nao_abre_sessao_por_cima_do_poll_em_voo(monta):
    """Section 14: a matrix and a projector accept ONE connection at a time, so the driver
    being replaced has to be off the wire before the new one opens anything. Stopping the old
    driver is not enough while its poll is still in flight.

    Seção 14: uma matriz e um projetor aceitam UMA conexão por vez, então o driver que está
    sendo trocado precisa estar fora do fio antes de o novo abrir qualquer coisa. Parar o
    driver velho não basta enquanto o poll dele ainda está em voo.
    """
    fio = _fio()
    velho = _fabrica_de_sessao(fio)
    novo = _fabrica_de_sessao(fio)
    gestor = await monta({"matriz": velho}, [_cadastro(tipo="matriz")])
    parado = velho.instancias[0]
    gestor.visitar_agora("uuid-1")
    async with asyncio.timeout(5):
        await parado.no_poll.wait()
    await gestor.trocar_catalogo({"matriz": novo}, refazer=("matriz",))
    assert len(novo.instancias) == 1
    assert fio["colisao"] is False
    assert fio["em_poll"] is False


async def test_uma_visita_fora_da_vez_nao_dobra_o_poll_de_um_equipamento(monta):
    """Two polls of one equipment at the same time are two sessions on the wire, which is the
    same defect by another door: the scheduled visit and the one asked for out of turn.

    Dois polls de um equipamento ao mesmo tempo são duas sessões no fio, que é o mesmo defeito
    por outra porta: a visita agendada e a pedida fora da vez.
    """
    fio = _fio()
    classe = _fabrica_de_sessao(fio)
    gestor = await monta({"matriz": classe}, [_cadastro(tipo="matriz")])
    driver = classe.instancias[0]
    gestor.visitar_agora("uuid-1")
    async with asyncio.timeout(5):
        await driver.no_poll.wait()
    gestor.visitar_agora("uuid-1")
    await asyncio.sleep(0.01)
    assert fio["polls"] == 1
    driver.liberar.set()

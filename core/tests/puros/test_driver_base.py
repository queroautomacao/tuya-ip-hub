# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6 under attack: the base must refuse, not pretend, and never publish a dict.

Seção 6 sob ataque: a base tem de recusar, não fingir, e nunca publicar um dict.
"""

from dataclasses import dataclass, field, replace

import pytest

from iphub.drivers.base import (
    CODIGOS,
    RESULTADOS,
    AutenticacaoNaoImplementada,
    Driver,
)
from iphub.drivers.manifesto import CAPACIDADES, Auth, Estado, Manifesto

VALIDO = Manifesto(
    tipo="exemplo_nu",
    rotulo={"pt": "Exemplo", "en": "Example"},
    categoria="outro",
    capacidades=("ligar",),
    textos={
        "pt": {"descricao": "Exemplo", "auth_ajuda": "Ajuda"},
        "en": {"descricao": "Example", "auth_ajuda": "Help"},
    },
)


@dataclass(frozen=True)
class CadastroFalso:
    identidade: str = "uuid-de-teste"
    ip: str = "192.0.2.10"
    campos: dict[str, str] = field(default_factory=dict)
    segredos: dict[str, str] = field(default_factory=dict)


def _driver(auth: Auth = Auth.NENHUMA) -> Driver:
    class Nu(Driver):
        MANIFESTO = replace(VALIDO, auth=auth)

    return Nu(CadastroFalso())


@pytest.mark.parametrize("auth", [a for a in Auth if a is not Auth.NENHUMA])
async def test_autenticar_herdado_estoura_em_vez_de_fingir(auth):
    """Section 6: the base REFUSES the inherited default, it never answers pareado.

    Seção 6: a base RECUSA o padrão herdado, ela nunca responde pareado.
    """
    with pytest.raises(AutenticacaoNaoImplementada) as erro:
        await _driver(auth).autenticar()
    assert auth in str(erro.value)


async def test_autenticar_sem_auth_responde_pareado():
    resultado = await _driver().autenticar()
    assert resultado == "pareado"
    assert resultado in RESULTADOS


@pytest.mark.parametrize("acao", CAPACIDADES)
async def test_executar_da_base_nunca_finge_sucesso(acao):
    """The default answers a stable code for every action of the vocabulary, never None.

    O padrão responde um código estável para toda ação do vocabulário, nunca None.
    """
    resposta = await _driver().executar(acao, 50)
    assert resposta == "nao_suportado"
    assert resposta in CODIGOS


async def test_executar_de_acao_inventada_tambem_e_recusada():
    assert await _driver().executar("formatar_o_disco") == "nao_suportado"


def test_estado_e_o_dataclass_tipado_nunca_um_dict():
    estado = _driver().estado()
    assert isinstance(estado, Estado)
    assert not isinstance(estado, dict)
    assert estado.online is False


def test_defina_troca_o_estado_inteiro_e_preserva_o_resto():
    driver = _driver()
    anterior = driver.estado()
    driver._defina(online=True, volume=42)
    driver._defina(mudo=True)
    assert driver.estado().online is True
    assert driver.estado().volume == 42
    assert driver.estado().mudo is True
    # Why: a reader that kept the old object must not see it change under its hands.
    # Por que: um leitor que guardou o objeto antigo não pode vê-lo mudar nas mãos dele.
    assert anterior.online is False
    assert anterior.volume is None
    assert driver.estado() is not anterior


def test_defina_recusa_campo_que_o_estado_nao_tem():
    """A field the panel would read has to be born in Estado, with a test, section 6.

    Um campo que o painel leria precisa nascer no Estado, com teste, seção 6.
    """
    with pytest.raises(TypeError):
        _driver()._defina(modo_clima="frio")


def test_dois_equipamentos_nao_dividem_estado():
    um, outro = _driver(), _driver()
    um._defina(online=True, volume=7)
    assert outro.estado().volume is None
    assert outro.estado().online is False


async def test_ciclo_de_vida_da_base_e_neutro():
    driver = _driver()
    await driver.iniciar()
    await driver.atualizar()
    await driver.parar()
    assert driver.estado() == Estado(online=False)


async def test_driver_sem_manifesto_falha_alto():
    """A class that forgot the manifest breaks loudly, it does not answer half a contract.

    Uma classe que esqueceu o manifesto quebra alto, ela não responde meio contrato.
    """

    class SemManifesto(Driver):
        pass

    with pytest.raises(AttributeError):
        await SemManifesto(CadastroFalso()).autenticar()


def test_o_cadastro_fica_acessivel_ao_driver():
    cadastro = CadastroFalso(campos={"porta": "4352"}, segredos={"senha": "abc"})

    class Nu(Driver):
        MANIFESTO = VALIDO

    driver = Nu(cadastro)
    assert driver.cadastro.identidade == "uuid-de-teste"
    assert driver.cadastro.campos["porta"] == "4352"

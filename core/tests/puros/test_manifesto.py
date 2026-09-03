# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Section 6 under attack: every rule of the manifest gets a manifest that breaks it.

Seção 6 sob ataque: toda regra do manifesto ganha um manifesto que a quebra.
"""

import json
from dataclasses import FrozenInstanceError, replace

import pytest

from iphub.drivers.manifesto import (
    CAPACIDADES,
    CATEGORIAS,
    IDIOMAS,
    MOTORES,
    Auth,
    Campo,
    Descoberta,
    Estado,
    Manifesto,
    ManifestoInvalido,
    TipoCampo,
    validar,
)

TEXTOS_PT = {"descricao": "Receiver de exemplo", "campo_porta": "Porta TCP"}
TEXTOS_EN = {"descricao": "Example receiver", "campo_porta": "TCP port"}


def _manifesto(**mudancas) -> Manifesto:
    """A manifest that passes, so each test changes exactly the rule it attacks.

    Um manifesto que passa, para cada teste mudar exatamente a regra que ataca.
    """
    valido = Manifesto(
        tipo="receiver_exemplo",
        rotulo={"pt": "Receiver de exemplo", "en": "Example receiver"},
        categoria="receiver",
        capacidades=("ligar", "desligar", "volume"),
        auth=Auth.NENHUMA,
        descoberta=Descoberta(ssdp_st=("urn:exemplo:aparelho:1",)),
        config_campos=(Campo(nome="porta", tipo=TipoCampo.INTEIRO, padrao="23"),),
        textos={"pt": dict(TEXTOS_PT), "en": dict(TEXTOS_EN)},
        motor="nativo",
    )
    return replace(valido, **mudancas)


def _campos_quebrados(mudancas: dict) -> set[str]:
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(**mudancas))
    return {problema.split(":")[0] for problema in erro.value.problemas}


def test_manifesto_valido_passa():
    assert validar(_manifesto()) is None


def test_vocabulario_da_secao_6_e_o_do_documento():
    assert "agrupar" in CAPACIDADES
    assert "multiroom" in CATEGORIAS
    assert MOTORES == ("nativo", "declarativo")
    assert set(IDIOMAS) == {"pt", "en"}


@pytest.mark.parametrize(
    ("mudancas", "esperado"),
    [
        ({"tipo": ""}, {"tipo"}),
        ({"tipo": "Receiver Exemplo"}, {"tipo"}),
        ({"tipo": "receiver-exemplo"}, {"tipo"}),
        ({"tipo": 7}, {"tipo"}),
        ({"categoria": "geladeira"}, {"categoria"}),
        ({"motor": "lua"}, {"motor"}),
        ({"auth": "codigo"}, {"auth", "textos"}),
        ({"descoberta": {"ssdp_st": ()}}, {"descoberta"}),
        ({"descoberta": Descoberta(ssdp_st="urn:exemplo:aparelho:1")}, {"ssdp_st"}),
        ({"descoberta": Descoberta(ssdp_st=("urn:exemplo:aparelho:1", 7))}, {"ssdp_st"}),
        ({"descoberta": Descoberta(ssdp_fabricantes=["exemplo"])}, {"ssdp_fabricantes"}),
        ({"descoberta": Descoberta(mdns_servicos=(None,))}, {"mdns_servicos"}),
        ({"capacidades": ("ligar", "voar")}, {"capacidades"}),
        ({"capacidades": ("ligar", "ligar")}, {"capacidades"}),
        ({"capacidades": ["ligar"]}, {"capacidades"}),
        ({"rotulo": {"pt": "Exemplo"}}, {"rotulo"}),
        ({"rotulo": {"pt": "Exemplo", "en": "Example", "zh": "Yi"}}, {"rotulo"}),
        ({"rotulo": {"pt": "Exemplo", "en": "   "}}, {"rotulo"}),
        ({"rotulo": {"pt": "Exemplo", "en": 7}}, {"rotulo"}),
        ({"textos": {"pt": dict(TEXTOS_PT)}}, {"textos"}),
        ({"textos": {"pt": dict(TEXTOS_PT), "en": "Example"}}, {"textos"}),
        ({"textos": {"pt": {"campo_porta": "Porta"}, "en": {"campo_porta": "Port"}}}, {"textos"}),
        ({"textos": {"pt": {"descricao": "A"}, "en": {"descricao": "B"}}}, {"textos"}),
        ({"textos": {"pt": {**TEXTOS_PT, "extra": "A"}, "en": dict(TEXTOS_EN)}}, {"textos"}),
        ({"textos": {"pt": {**TEXTOS_PT, "descricao": 7}, "en": dict(TEXTOS_EN)}}, {"textos"}),
        ({"config_campos": (Campo(nome="porta"), Campo(nome="porta"))}, {"config_campos"}),
        ({"config_campos": (Campo(nome=""),)}, {"config_campos", "textos"}),
        ({"config_campos": (Campo(nome="porta", tipo="texto"),)}, {"config_campos"}),
        ({"config_campos": [Campo(nome="porta")]}, {"config_campos"}),
        ({"config_campos": (Campo(nome="porta", padrao=4352),)}, {"config_campos"}),
        ({"config_campos": (Campo(nome="porta", padrao=None),)}, {"config_campos"}),
        ({"config_campos": (Campo(nome="porta", obrigatorio=1),)}, {"config_campos"}),
        ({"config_campos": (Campo(nome="porta", obrigatorio="sim"),)}, {"config_campos"}),
    ],
)
def test_regra_quebrada_e_apontada_e_nada_mais(mudancas, esperado):
    assert _campos_quebrados(mudancas) == esperado


def test_assinatura_de_descoberta_que_nao_e_tupla_de_texto_nao_embarca():
    """Section 6: a broken signature must fail at load, not when the sweep plan is built.

    Seção 6: uma assinatura quebrada precisa falhar na carga, não ao montar o plano.
    """
    torta = Descoberta(ssdp_st="urn:exemplo:aparelho:1", mdns_servicos=(7,))
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(descoberta=torta))
    assert {p.split(":")[0] for p in erro.value.problemas} == {"ssdp_st", "mdns_servicos"}


def test_padrao_de_campo_que_nao_e_texto_nao_embarca():
    """The panel reads padrao as text: a port default of 4352 empties the form of EVERY driver.

    O painel lê padrao como texto: um padrão de porta 4352 esvazia o formulário de TODO driver.
    """
    numerico = (Campo(nome="porta", tipo=TipoCampo.INTEIRO, padrao=4352),)
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(config_campos=numerico))
    assert "padrao" in str(erro.value)
    assert validar(_manifesto(config_campos=(Campo(nome="porta", padrao="4352"),))) is None


def test_obrigatorio_que_e_numero_nao_passa_por_bandeira():
    """True written as 1 is not smuggled through: the panel matches a flag, never a number.

    True escrito como 1 não passa contrabandeado: o painel casa bandeira, nunca número.
    """
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(config_campos=(Campo(nome="porta", obrigatorio=1),)))
    assert "obrigatorio" in str(erro.value)
    assert validar(_manifesto(config_campos=(Campo(nome="porta", obrigatorio=True),))) is None


def test_campo_chamado_ip_e_recusado():
    """The ip is the address the discovery re-resolves, never a field of the registration.

    O ip é o endereço que a descoberta re-resolve, nunca um campo do cadastro.
    """
    campos = (Campo(nome="ip"),)
    textos = {
        "pt": {"descricao": "A", "campo_ip": "IP"},
        "en": {"descricao": "B", "campo_ip": "IP"},
    }
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(config_campos=campos, textos=textos))
    assert any("ip" in problema for problema in erro.value.problemas)
    assert {p.split(":")[0] for p in erro.value.problemas} == {"config_campos"}


def test_agrupar_so_vale_em_multiroom():
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(capacidades=("agrupar",)))
    assert "multiroom" in str(erro.value)
    assert validar(_manifesto(capacidades=("agrupar",), categoria="multiroom")) is None


@pytest.mark.parametrize("auth", [a for a in Auth if a is not Auth.NENHUMA])
def test_auth_diferente_de_nenhuma_exige_texto_de_ajuda(auth):
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(auth=auth))
    assert "auth_ajuda" in str(erro.value)
    com_ajuda = {
        "pt": {**TEXTOS_PT, "auth_ajuda": "Digite o codigo do aparelho"},
        "en": {**TEXTOS_EN, "auth_ajuda": "Type the code shown on the device"},
    }
    assert validar(_manifesto(auth=auth, textos=com_ajuda)) is None


def test_todo_problema_sai_de_uma_vez():
    """A contributor fixes the driver in one pass, not one error per run.

    Um contribuidor conserta o driver numa passada, não um erro por execução.
    """
    with pytest.raises(ManifestoInvalido) as erro:
        validar(
            Manifesto(
                tipo="Receiver Exemplo",
                rotulo={"pt": "Exemplo"},
                categoria="geladeira",
                capacidades=("voar", "agrupar", "agrupar"),
                auth=Auth.CODIGO,
                config_campos=(Campo(nome="porta"), Campo(nome="porta")),
                textos={},
                motor="lua",
            )
        )
    assert {p.split(":")[0] for p in erro.value.problemas} == {
        "tipo",
        "categoria",
        "motor",
        "capacidades",
        "rotulo",
        "config_campos",
        "textos",
    }
    assert erro.value.tipo == "Receiver Exemplo"
    assert isinstance(erro.value, ValueError)


def test_textos_pt_e_en_precisam_das_mesmas_chaves():
    torto = {"pt": {**TEXTOS_PT, "so_em_pt": "A"}, "en": dict(TEXTOS_EN)}
    with pytest.raises(ManifestoInvalido) as erro:
        validar(_manifesto(textos=torto))
    assert "so_em_pt" in str(erro.value)


@pytest.mark.parametrize("classe", [Campo, Descoberta, Manifesto])
def test_o_contrato_e_congelado(classe):
    valores = {
        Campo: Campo(nome="porta"),
        Descoberta: Descoberta(),
        Manifesto: _manifesto(),
    }
    with pytest.raises(FrozenInstanceError):
        valores[classe].tipo_inexistente = "x"


def test_campo_e_descoberta_sao_hashaveis():
    """They travel inside a frozen manifest and inside sets of the discovery plan.

    Eles viajam dentro de um manifesto congelado e dentro de conjuntos do plano de descoberta.
    """
    assert len({Campo(nome="porta"), Campo(nome="porta"), Campo(nome="senha")}) == 2
    assert len({Descoberta(), Descoberta(ssdp_st=("a",))}) == 2


def test_auth_e_tipo_de_campo_viajam_como_texto():
    """The API answers them as they are, so the panel matches a string, never a number.

    A API os responde como estão, então o painel casa uma string, nunca um número.
    """
    assert json.loads(json.dumps({"auth": Auth.CODIGO, "tipo": TipoCampo.SEGREDO})) == {
        "auth": "codigo",
        "tipo": "segredo",
    }


def test_estado_nasce_offline_e_sem_opiniao():
    estado = Estado(online=False)
    assert estado.ligado is None
    assert estado.volume is None
    assert estado.mudo is None
    assert estado.fonte is None
    assert estado.fontes == ()
    assert estado.tocando is None
    assert estado.detalhe == ""


def test_estado_carrega_a_escala_unica_de_volume():
    assert Estado(online=True, volume=100).volume == 100
    assert Estado(online=True, volume=0).volume == 0

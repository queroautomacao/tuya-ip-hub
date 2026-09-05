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
    LISTAS,
    LISTAS_MAXIMO,
    MODOS_AR,
    MOTORES,
    ROTULO_MAXIMO,
    TECLAS,
    VALOR_DE_LISTA_MAXIMO,
    VENTOS,
    Auth,
    Campo,
    Descoberta,
    Estado,
    Manifesto,
    ManifestoInvalido,
    Sugestao,
    TipoCampo,
    item_valido,
    por_lista,
    produto_de,
    template_de,
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
    assert {"tecla", "atalho", "modo", "vento", "temperatura"} <= set(CAPACIDADES)
    assert {"multiroom", "ar_condicionado", "amplificador"} <= set(CATEGORIAS)
    assert MOTORES == ("nativo", "declarativo")
    assert set(IDIOMAS) == {"pt", "en"}
    assert TECLAS[:4] == ("mais", "menos", "canal_mais", "canal_menos")
    assert "digito_0" in TECLAS and "digito_9" in TECLAS and len(TECLAS) == 28
    assert MODOS_AR == ("auto", "frio", "quente", "vento", "seco")
    assert VENTOS == ("auto", "baixo", "medio", "alto")


def test_o_produto_e_o_template_nascem_da_categoria():
    """Section 8: an air conditioner is the product of air; a TV or a projector draws the
    tv template and everything else the audio one.

    Seção 8: um ar condicionado é o produto de ar; uma TV ou um projetor desenha o template
    tv e todo o resto o de áudio.
    """
    assert produto_de("ar_condicionado") == "ar"
    assert {produto_de(c) for c in CATEGORIAS if c != "ar_condicionado"} == {"av"}
    assert template_de("tv") == "tv"
    assert template_de("projetor") == "tv"
    assert {template_de(c) for c in ("receiver", "multiroom", "amplificador", "matriz")} == {"au"}


# Why: a capability spoken in words (a key, a mode, a fan speed) is declared with its words,
# so the panel and the command channel of section 8 offer only what the driver really sends.
# Por que: uma capacidade falada em palavras (tecla, modo, vento) é declarada com as palavras
# dela, então o painel e o canal de comando da seção 8 oferecem só o que o driver manda.
@pytest.mark.parametrize(
    ("mudancas", "esperado"),
    [
        ({"capacidades": ("ligar", "tecla")}, {"teclas"}),
        ({"capacidades": ("ligar", "tecla"), "teclas": ("mais", "voar")}, {"teclas"}),
        ({"capacidades": ("ligar", "tecla"), "teclas": ("mais", "mais")}, {"teclas"}),
        ({"teclas": ("mais",)}, {"teclas"}),
        ({"capacidades": ("ligar", "tecla"), "teclas": ["mais"]}, {"teclas"}),
        ({"capacidades": ("ligar", "temperatura")}, {"capacidades"}),
        ({"capacidades": ("ligar", "vento")}, {"capacidades"}),
        (
            {"categoria": "ar_condicionado", "capacidades": ("ligar", "modo")},
            {"modos"},
        ),
        (
            {"categoria": "ar_condicionado", "capacidades": ("ligar", "modo"), "modos": ("gelo",)},
            {"modos"},
        ),
        (
            {"categoria": "ar_condicionado", "capacidades": ("ligar", "vento"), "ventos": ("x",)},
            {"ventos"},
        ),
    ],
)
def test_uma_capacidade_falada_em_palavras_exige_as_palavras(mudancas, esperado):
    assert _campos_quebrados(mudancas) == esperado


def test_um_ar_condicionado_com_o_vocabulario_inteiro_passa():
    manifesto = _manifesto(
        categoria="ar_condicionado",
        capacidades=("ligar", "desligar", "temperatura", "modo", "vento"),
        modos=MODOS_AR,
        ventos=VENTOS,
    )
    assert validar(manifesto) is None


def test_uma_tv_com_teclas_passa_e_um_receiver_le_modo_sem_vocabulario():
    assert validar(_manifesto(capacidades=("ligar", "tecla"), teclas=("mais", "ok"))) is None
    assert validar(_manifesto(capacidades=("ligar", "modo", "atalho"))) is None


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
    assert estado.temperatura is None
    assert estado.modo is None
    assert estado.vento is None
    assert estado.detalhe == ""


def test_estado_carrega_a_escala_unica_de_volume():
    assert Estado(online=True, volume=100).volume == 100
    assert Estado(online=True, volume=0).volume == 0


def _com_sugestoes(*sugestoes: Sugestao, **mudancas) -> Manifesto:
    return _manifesto(
        capacidades=("ligar", "desligar", "volume", "atalho"), sugestoes=sugestoes, **mudancas
    )


def test_um_driver_sugere_itens_para_a_lista_que_uma_capacidade_dele_le():
    """Section 8: what a driver suggests fills a list of the registration, so it is judged by
    the rule the registration judges an item by, and offered only for a list something reads.

    Seção 8: o que um driver sugere preenche uma lista do cadastro, então é julgado pela regra
    que o cadastro julga um item, e oferecido só para uma lista que alguém lê.
    """
    manifesto = _com_sugestoes(Sugestao("atalhos", "Radio", "http://10.0.0.2/radio"))
    assert validar(manifesto) is None
    assert por_lista(manifesto) == {
        "atalhos": (Sugestao("atalhos", "Radio", "http://10.0.0.2/radio"),)
    }
    assert por_lista(_manifesto()) == {}


@pytest.mark.parametrize(
    "sugestao",
    [
        Sugestao("radios", "Radio", "http://10.0.0.2/radio"),
        Sugestao("atalhos", "", "http://10.0.0.2/radio"),
        Sugestao("atalhos", "Radio", ""),
        Sugestao("atalhos", "Radio, a boa", "http://10.0.0.2/radio"),
        Sugestao("atalhos", "R" * (ROTULO_MAXIMO + 1), "http://10.0.0.2/radio"),
        Sugestao("atalhos", "Radio", "x" * (VALOR_DE_LISTA_MAXIMO + 1)),
        Sugestao("atalhos", "Radio\n", "http://10.0.0.2/radio"),
    ],
)
def test_uma_sugestao_que_o_cadastro_recusaria_nao_embarca(sugestao):
    assert "sugestoes" in _campos_quebrados(
        {"capacidades": ("ligar", "desligar", "volume", "atalho"), "sugestoes": (sugestao,)}
    )


def test_sugestao_para_uma_lista_que_nenhuma_capacidade_le_nao_embarca():
    """A list the manifest never acts on is a list the app draws and the daemon refuses.

    Uma lista sobre a qual o manifesto nunca age é uma lista que o app desenha e o daemon recusa.
    """
    assert "sugestoes" in _campos_quebrados(
        {"capacidades": ("ligar", "desligar"), "sugestoes": (Sugestao("atalhos", "R", "preset:1"),)}
    )


def test_um_ar_condicionado_nao_sugere_modo_porque_le_o_vocabulario():
    quebrados = _campos_quebrados(
        {
            "categoria": "ar_condicionado",
            "capacidades": ("ligar", "desligar", "modo", "vento", "temperatura"),
            "modos": ("frio",),
            "ventos": ("auto",),
            "sugestoes": (Sugestao("modos", "Frio", "frio"),),
        }
    )
    assert "sugestoes" in quebrados


def test_sugestoes_acima_do_teto_da_lista_nao_embarcam():
    demais = tuple(
        Sugestao("atalhos", f"Radio {n}", f"http://10.0.0.2/r{n}")
        for n in range(LISTAS_MAXIMO["atalhos"] + 1)
    )
    assert "sugestoes" in _campos_quebrados(
        {"capacidades": ("ligar", "desligar", "volume", "atalho"), "sugestoes": demais}
    )


def test_a_regra_de_um_item_de_lista_e_a_mesma_do_cadastro():
    """One rule, in the module the vocabulary lives in; the registration re-exports it instead
    of writing a second one that would drift.

    Uma regra, no módulo em que o vocabulário mora; o cadastro a reexporta em vez de escrever
    uma segunda que divergiria.
    """
    from iphub import config

    assert config.item_valido is item_valido
    assert config.LISTAS == LISTAS
    assert config.LISTAS_MAXIMO == LISTAS_MAXIMO
    assert (config.ROTULO_MAXIMO, config.VALOR_DE_LISTA_MAXIMO) == (
        ROTULO_MAXIMO,
        VALOR_DE_LISTA_MAXIMO,
    )

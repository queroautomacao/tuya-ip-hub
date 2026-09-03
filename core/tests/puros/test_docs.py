# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The public documents against the repository: what ships is what the README says ships.

Section 13 of CLAUDE.md lists the milestones and the README repeats them for whoever arrives
from outside, so the repeated copy rots in silence: it announced milestone 1 and no device
controlled while the hub already carried the driver catalog, the discovery and the
declarative engine. Here the delivery of a milestone is decided by the code, by the routes
the daemon registers and by the modules that exist, and the documents have to agree with it.

Os documentos públicos contra o repositório: o que embarca é o que o README diz que embarca.

A seção 13 do CLAUDE.md lista os marcos e o README os repete para quem chega de fora, então a
cópia repetida apodrece em silêncio: anunciava o marco 1 e nenhum aparelho controlado
enquanto o hub já carregava o catálogo de drivers, a descoberta e o motor declarativo. Aqui a
entrega de um marco é decidida pelo código, pelas rotas que o daemon registra e pelos módulos
que existem, e os documentos precisam concordar com ela.
"""

import json
import re
from collections.abc import Iterator
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]

README = RAIZ / "README.md"
CONTRIBUIR = RAIZ / "CONTRIBUTING.md"
ROTAS = RAIZ / "core" / "iphub" / "api" / "__init__.py"
APP = RAIZ / "core" / "iphub" / "app.py"

CATALOGO_EMBARCADO = "core/iphub/drivers/catalogo_json/"

METADES = {"en": "## English", "pt": "## Português"}
TITULO_STATUS = {"en": "### Project status", "pt": "### Estado do projeto"}
PALAVRA_MARCO = {"en": "milestone", "pt": "marco"}

ENTREGUE = frozenset({"delivered", "entregue"})
PLANEJADO = frozenset({"planned", "planejado"})

# Why: a claim the repository outgrew and that no numeric rule catches, because it is a
# sentence and not a milestone number; each one is paired with the milestone that makes it
# false, so it goes away with the delivery instead of staying pinned forever.
# Por que: uma afirmação que o repositório deixou para trás e que nenhuma regra numérica pega,
# porque é uma frase e não um número de marco; cada uma vem com o marco que a torna falsa,
# então ela cai com a entrega em vez de ficar presa para sempre.
AFIRMACOES_VENCIDAS = (
    (2, "No device is controlled yet"),
    (2, "Nenhum aparelho é controlado ainda"),
    (3, "arriving with milestone 3"),
    (3, "que chega com o marco 3"),
)


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8")


def _provas() -> tuple[set[int], set[int]]:
    """The milestones the code decides, and which of them are delivered.

    Os marcos que o código decide, e quais deles estão entregues.
    """
    rotas = _ler(ROTAS)
    app = _ler(APP)
    provas = {
        0: (RAIZ / "Dockerfile").is_file() and '"/health"' in rotas,
        1: '"/api/posse"' in rotas,
        2: '"/api/equipamentos"' in rotas and (RAIZ / "core/iphub/drivers/base.py").is_file(),
        3: (
            '"/api/drivers"' in rotas
            and (RAIZ / "core/iphub/drivers/declarativo/motor.py").is_file()
        ),
        4: "/dpbus" in rotas or "/dpbus" in app,
    }
    return {numero for numero, prova in provas.items() if prova}, set(provas)


def _metades(caminho: Path) -> dict[str, str]:
    texto = _ler(caminho)
    inicios = {}
    for idioma, titulo in METADES.items():
        indice = texto.find("\n" + titulo + "\n")
        assert indice >= 0, f"{caminho.name} has no {titulo} half"
        inicios[idioma] = indice
    ordenados = sorted(inicios.items(), key=lambda par: par[1])
    metades = {}
    for posicao, (idioma, inicio) in enumerate(ordenados):
        seguinte = posicao + 1
        fim = ordenados[seguinte][1] if seguinte < len(ordenados) else len(texto)
        metades[idioma] = texto[inicio:fim]
    return metades


def _celulas(metade: str) -> Iterator[list[str]]:
    for linha in metade.splitlines():
        crua = linha.strip()
        if crua.startswith("|"):
            yield [celula.strip() for celula in crua.strip("|").split("|")]


def _mapa_de_marcos(metade: str, idioma: str) -> dict[int, bool]:
    mapa = {}
    for celulas in _celulas(metade):
        if not celulas[0].isdigit():
            continue
        marcas = [c for c in celulas[1:] if c in ENTREGUE or c in PLANEJADO]
        assert len(marcas) == 1, f"{idioma}: milestone {celulas[0]} carries no single status"
        mapa[int(celulas[0])] = marcas[0] in ENTREGUE
    assert mapa, f"{idioma}: no milestone row found in the roadmap"
    return mapa


def _secao_de_status(metade: str, idioma: str) -> str:
    titulo = TITULO_STATUS[idioma]
    inicio = metade.find(titulo)
    assert inicio >= 0, f"{idioma}: no {titulo} section"
    resto = metade[inicio + len(titulo) :]
    corte = resto.find("\n|")
    return resto if corte < 0 else resto[:corte]


def test_o_roadmap_do_readme_marca_o_que_o_codigo_ja_entrega():
    entregues, decididos = _provas()
    for idioma, metade in _metades(README).items():
        mapa = _mapa_de_marcos(metade, idioma)
        for numero in sorted(decididos & set(mapa)):
            esperado = numero in entregues
            assert mapa[numero] is esperado, (
                f"{idioma}: milestone {numero} is "
                f"{'delivered' if esperado else 'not delivered'} in the code and the README "
                f"says the opposite"
            )


def test_o_roadmap_nao_entrega_um_marco_depois_de_um_que_falta():
    for idioma, metade in _metades(README).items():
        mapa = _mapa_de_marcos(metade, idioma)
        faltando = [numero for numero in sorted(mapa) if not mapa[numero]]
        if not faltando:
            continue
        adiantados = [numero for numero in sorted(mapa) if numero > faltando[0] and mapa[numero]]
        assert not adiantados, (
            f"{idioma}: milestone {faltando[0]} is not delivered and {adiantados} claim to be"
        )


def test_as_duas_metades_do_roadmap_dizem_a_mesma_coisa():
    metades = _metades(README)
    mapas = {idioma: _mapa_de_marcos(metade, idioma) for idioma, metade in metades.items()}
    assert mapas["pt"] == mapas["en"], f"the roadmap halves disagree: {mapas}"


def test_o_status_do_readme_nomeia_o_marco_mais_alto_entregue():
    entregues, _ = _provas()
    alvo = max(entregues)
    for idioma, metade in _metades(README).items():
        prosa = _secao_de_status(metade, idioma)
        numeros = []
        for linha in prosa.splitlines():
            if PALAVRA_MARCO[idioma] in linha.lower():
                numeros += [int(digito) for digito in re.findall(r"\b\d\b", linha)]
        assert numeros, f"{idioma}: the project status names no milestone"
        assert max(numeros) == alvo, (
            f"{idioma}: the project status stops at milestone {max(numeros)} "
            f"and milestone {alvo} is delivered"
        )


def test_nenhum_documento_promete_para_depois_o_que_ja_embarca():
    entregues, _ = _provas()
    ofensores = [
        f"{caminho.name}: {frase}"
        for caminho in (README, CONTRIBUIR)
        for marco, frase in AFIRMACOES_VENCIDAS
        if marco in entregues and frase in _ler(caminho)
    ]
    assert not ofensores, "claim about a delivered milestone:\n" + "\n".join(ofensores)


def test_contribuir_aponta_a_pasta_do_catalogo_embarcado():
    pasta = RAIZ / CATALOGO_EMBARCADO
    assert pasta.is_dir(), f"{CATALOGO_EMBARCADO} does not exist"
    for idioma, metade in _metades(CONTRIBUIR).items():
        assert CATALOGO_EMBARCADO in metade, (
            f"{idioma}: CONTRIBUTING does not say where an embedded driver lives"
        )


def test_contribuir_nomeia_todo_transporte_que_o_catalogo_embarcado_traz():
    pasta = RAIZ / CATALOGO_EMBARCADO
    transportes = set()
    for arquivo in sorted(pasta.glob("*.json")):
        transportes |= set(json.loads(_ler(arquivo))["transporte"])
    assert transportes, f"no embedded driver in {CATALOGO_EMBARCADO}"
    for idioma, metade in _metades(CONTRIBUIR).items():
        faltando = [t for t in sorted(transportes) if t.upper() not in metade]
        assert not faltando, f"{idioma}: CONTRIBUTING names no example for {faltando}"


def test_readme_e_contribuir_tem_as_mesmas_secoes_nas_duas_linguas():
    for caminho in (README, CONTRIBUIR):
        contagem = {
            idioma: sum(1 for linha in metade.splitlines() if linha.startswith("### "))
            for idioma, metade in _metades(caminho).items()
        }
        assert len(set(contagem.values())) == 1, f"{caminho.name} halves differ: {contagem}"

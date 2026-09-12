# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The public documents against the repository: the two halves of a bilingual document carry
the same sections, and the device matrix only names drivers that exist and signs what it
claims to have verified.
"""

import re
from collections.abc import Iterator
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
README = RAIZ / "README.md"
CONTRIBUIR = RAIZ / "CONTRIBUTING.md"
SEGURANCA = RAIZ / "SECURITY.md"
MATRIZ = RAIZ / "docs" / "MATRIZ.md"

METADES = {"en": "## English", "pt": "## Português"}


def _metades(caminho: Path) -> dict[str, str]:
    texto = caminho.read_text(encoding="utf-8")
    inicio_en = texto.find(METADES["en"])
    inicio_pt = texto.find(METADES["pt"])
    assert 0 <= inicio_en < inicio_pt, f"{caminho.name}: the two halves are not in place"
    return {"en": texto[inicio_en:inicio_pt], "pt": texto[inicio_pt:]}


def test_os_documentos_bilingues_tem_as_mesmas_secoes_nas_duas_linguas():
    for caminho in (README, CONTRIBUIR, SEGURANCA):
        contagem = {
            idioma: sum(1 for linha in metade.splitlines() if linha.startswith("### "))
            for idioma, metade in _metades(caminho).items()
        }
        assert len(set(contagem.values())) == 1, f"{caminho.name} halves differ: {contagem}"


# A row of the table: brand, model, driver, state, who, notes; the driver is the third cell.
LINHA_DA_MATRIZ = re.compile(r"^\|(?P<celulas>.+)\|\s*$")
ESTADOS_DA_MATRIZ = ("verificado", "simulado", "declarado")
ASSINATURA = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?, \d{4}-\d{2}-\d{2}$")


def _linhas_de_aparelho() -> Iterator[list[str]]:
    for linha in MATRIZ.read_text(encoding="utf-8").splitlines():
        casado = LINHA_DA_MATRIZ.match(linha)
        if casado is None:
            continue
        celulas = [c.strip() for c in casado.group("celulas").split("|")]
        if len(celulas) == 6 and celulas[3].strip("`") in ESTADOS_DA_MATRIZ:
            yield celulas


def test_a_matriz_so_cita_driver_que_existe_no_catalogo():
    from iphub.drivers import catalogo

    tipos = set(catalogo.carregar())
    citados = {celulas[2].strip("`") for celulas in _linhas_de_aparelho()}
    assert citados, "the matrix has no device row to check"
    assert citados <= tipos, f"the matrix names drivers that do not exist: {citados - tipos}"


def test_toda_linha_verificada_da_matriz_e_assinada():
    for celulas in _linhas_de_aparelho():
        if celulas[3].strip("`") != "verificado":
            continue
        assert ASSINATURA.fullmatch(celulas[4]), (
            f"a verified row of {celulas[0]} {celulas[1]} is signed {celulas[4]!r}, "
            "and the matrix asks for a GitHub username and a date as YYYY-MM-DD"
        )

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""The art of the brands against the drivers that name them.

The home page draws one chip per brand of the catalog, with the file of that brand when the
installation is authorised to carry it and the name written otherwise. Nothing keeps the two
sides together on its own: a driver of a new brand would land with no chip art and nobody
would notice, and a file left behind by a driver that was removed would sit there forever.
These tests are that link, and the list of the brands with no art yet is declared in the
document beside the files, so a gap is a decision somebody wrote instead of an accident.
"""

import re
import unicodedata
from pathlib import Path

RAIZ = Path(__file__).resolve().parents[3]
MARCAS = RAIZ / "painel" / "src" / "marcas"
LEIA_ME = MARCAS / "LEIA-ME.md"
EXTENSOES = (".svg", ".png", ".webp")

# The one line of the document the suite reads, in the two languages it is written in.
SEM_ARQUIVO = re.compile(r"^No file yet \(Ainda sem arquivo\):(?P<lista>.*)$", re.MULTILINE)


def _apelido(marca: str) -> str:
    """The slug of a brand, by the same rule the panel names the file with (marcas.ts)."""
    sem_acento = unicodedata.normalize("NFD", marca)
    sem_acento = "".join(c for c in sem_acento if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "-", sem_acento.strip().lower()).strip("-")


def _marcas_do_catalogo() -> dict[str, str]:
    from iphub.drivers import catalogo

    return {
        _apelido(driver.MANIFESTO.marca): driver.MANIFESTO.marca
        for driver in catalogo.carregar().values()
        if driver.MANIFESTO.marca
    }


def _arquivos() -> dict[str, str]:
    return {
        _apelido(caminho.stem): caminho.name
        for caminho in MARCAS.iterdir()
        if caminho.suffix.lower() in EXTENSOES
    }


def _declaradas_sem_arte() -> set[str]:
    achado = SEM_ARQUIVO.search(LEIA_ME.read_text(encoding="utf-8"))
    assert achado is not None, f"{LEIA_ME.name} does not carry the line of the brands with no art"
    return {parte.strip() for parte in achado.group("lista").split(",") if parte.strip()}


def test_toda_marca_do_catalogo_tem_arte_ou_esta_declarada_sem_ela():
    """A brand of a driver is drawn or is written down as not drawn, never simply forgotten."""
    arquivos = _arquivos()
    declaradas = _declaradas_sem_arte()
    faltando = {
        apelido: nome
        for apelido, nome in _marcas_do_catalogo().items()
        if apelido not in arquivos and apelido not in declaradas
    }
    assert not faltando, (
        f"these brands of the catalog have no art and are not declared in {LEIA_ME.name}: "
        f"{sorted(faltando.values())}"
    )


def test_a_lista_de_marcas_sem_arte_nao_guarda_marca_que_ja_ganhou_arquivo():
    """A list that outlives the gap it describes is a list nobody trusts."""
    arquivos = _arquivos()
    velhas = sorted(apelido for apelido in _declaradas_sem_arte() if apelido in arquivos)
    assert not velhas, (
        f"{LEIA_ME.name} still lists as having no art: {velhas}, and each already has a file"
    )


def test_a_lista_de_marcas_sem_arte_so_cita_marca_de_driver_que_existe():
    marcas = set(_marcas_do_catalogo())
    fora = sorted(apelido for apelido in _declaradas_sem_arte() if apelido not in marcas)
    assert not fora, f"{LEIA_ME.name} lists brands no driver claims: {fora}"


def test_todo_arquivo_de_marca_pertence_a_uma_marca_do_catalogo():
    """Art of a brand this hub does not reach is art nobody asked for."""
    marcas = set(_marcas_do_catalogo())
    orfaos = sorted(nome for apelido, nome in _arquivos().items() if apelido not in marcas)
    assert not orfaos, f"these files match no brand of the catalog: {orfaos}"

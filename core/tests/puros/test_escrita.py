# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Repository-wide writing rules from CLAUDE.md sections 10 and 11.

Regras de escrita do repositório inteiro, seções 10 e 11 do CLAUDE.md.
"""

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parents[3]

DIRS_IGNORADOS = frozenset(
    {
        ".git",
        "node_modules",
        "dist",
        ".venv",
        "venv",
        "interno",
        "data",
        "__pycache__",
        ".pytest_cache",
        ".ruff_cache",
        ".mypy_cache",
    }
)
ARQUIVOS_IGNORADOS = frozenset({"LICENSE"})
EXTENSOES_TEXTO = frozenset(
    {
        ".py",
        ".ts",
        ".tsx",
        ".md",
        ".json",
        ".yml",
        ".yaml",
        ".toml",
        ".sh",
        ".css",
        ".html",
        ".txt",
    }
)
NOMES_TEXTO = frozenset({"Dockerfile", "NOTICE", ".gitignore", ".dockerignore", ".editorconfig"})
EXTENSOES_COM_SPDX = frozenset(
    {".py", ".ts", ".tsx", ".sh", ".css", ".html", ".yml", ".yaml", ".toml"}
)
NOMES_COM_SPDX = frozenset({"Dockerfile", ".gitignore", ".dockerignore", ".editorconfig"})

TRAVESSOES = ("\u2014", "\u2013")
SPDX_LICENCA = "SPDX-License-Identifier: AGPL-3.0-only"
SPDX_COPYRIGHT = "Copyright (C) 2026 Quero Automação Ltda"


def _ignorar_dir(caminho: Path) -> bool:
    return caminho.name in DIRS_IGNORADOS or caminho.name.endswith(".egg-info")


def _do_git() -> list[Path] | None:
    """The repository decides what is part of it; the walk is only the fallback.

    O repositório decide o que faz parte dele; a varredura é só o plano B.
    """
    try:
        saida = subprocess.run(
            [
                "git",
                "-C",
                str(RAIZ),
                "ls-files",
                "-z",
                "--cached",
                "--others",
                "--exclude-standard",
            ],
            capture_output=True,
            check=True,
            timeout=30,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    caminhos = [RAIZ / nome for nome in saida.decode("utf-8").split("\0") if nome]
    return [c for c in caminhos if c.is_file()] or None


def _varrer() -> Iterator[Path]:
    pendentes = [RAIZ]
    while pendentes:
        atual = pendentes.pop()
        for filho in sorted(atual.iterdir()):
            if filho.is_dir():
                if not _ignorar_dir(filho):
                    pendentes.append(filho)
            elif filho.is_file():
                yield filho


def _arquivos() -> Iterator[Path]:
    do_git = _do_git()
    yield from do_git if do_git is not None else _varrer()


def _e_texto(caminho: Path) -> bool:
    if caminho.name in ARQUIVOS_IGNORADOS:
        return False
    return caminho.suffix in EXTENSOES_TEXTO or caminho.name in NOMES_TEXTO


def _exige_spdx(caminho: Path) -> bool:
    return caminho.suffix in EXTENSOES_COM_SPDX or caminho.name in NOMES_COM_SPDX


def _ler(caminho: Path) -> str:
    return caminho.read_text(encoding="utf-8", errors="replace")


def _relativo(caminho: Path) -> str:
    return str(caminho.relative_to(RAIZ))


def test_nenhum_travessao_em_arquivo_de_texto():
    ofensores = []
    for arquivo in _arquivos():
        if not _e_texto(arquivo):
            continue
        for numero, linha in enumerate(_ler(arquivo).splitlines(), start=1):
            if any(t in linha for t in TRAVESSOES):
                ofensores.append(f"{_relativo(arquivo)}:{numero}")
    assert not ofensores, "em dash or en dash found in:\n" + "\n".join(ofensores)


def test_cabecalho_spdx_nas_tres_primeiras_linhas():
    fontes = [a for a in _arquivos() if _exige_spdx(a)]
    assert fontes, "no source file found under the repository root"
    ofensores = []
    for arquivo in fontes:
        inicio = _ler(arquivo).splitlines()[:3]
        if not any(SPDX_LICENCA in linha for linha in inicio) or not any(
            SPDX_COPYRIGHT in linha for linha in inicio
        ):
            ofensores.append(_relativo(arquivo))
    assert not ofensores, "SPDX header missing in:\n" + "\n".join(ofensores)


@pytest.fixture
def i18n() -> dict[str, dict]:
    pasta = RAIZ / "painel" / "src" / "i18n"
    textos = {}
    for idioma in ("pt", "en"):
        arquivo = pasta / f"{idioma}.json"
        assert arquivo.is_file(), f"missing {_relativo(arquivo)}"
        textos[idioma] = json.loads(arquivo.read_text(encoding="utf-8"))
        assert isinstance(textos[idioma], dict), f"{_relativo(arquivo)} must be a flat object"
    return textos


def test_i18n_pt_e_en_tem_as_mesmas_chaves(i18n):
    assert set(i18n["pt"]) == set(i18n["en"])
    assert i18n["pt"], "i18n files must not be empty"


def test_i18n_todo_valor_e_texto_nao_vazio(i18n):
    ofensores = [
        f"{idioma}.{chave}"
        for idioma, textos in i18n.items()
        for chave, valor in textos.items()
        if not isinstance(valor, str) or not valor.strip()
    ]
    assert not ofensores, "empty or non-string i18n values:\n" + "\n".join(ofensores)

# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
import re
import tomllib
from pathlib import Path

from iphub.versao import SCHEMA_VERSION, VERSAO


def test_versao_e_semver():
    assert re.fullmatch(r"\d+\.\d+\.\d+", VERSAO)


def test_versao_do_pacote_vem_do_modulo():
    # Why: the installed metadata lags behind a version bump; the wiring is what matters.
    # Por que: o metadado instalado fica atrás de um bump de versão; o que importa é a ligação.
    pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
    dados = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    assert dados["project"]["dynamic"] == ["version"]
    assert dados["tool"]["setuptools"]["dynamic"]["version"] == {"attr": "iphub.versao.VERSAO"}


def test_schema_version_e_inteiro_positivo():
    assert type(SCHEMA_VERSION) is int
    assert SCHEMA_VERSION >= 1

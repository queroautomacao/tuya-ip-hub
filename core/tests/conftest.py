# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
"""Shared fixtures: a built panel on disk, an environment and an HTTP test client.

Fixtures compartilhadas: painel construído em disco, ambiente e cliente HTTP de teste.
"""

from pathlib import Path

import pytest

from iphub.ambiente import Ambiente
from iphub.app import criar_app

INDEX_HTML = '<!doctype html><title>Tuya IP Hub</title><div id="root"></div>\n'
APP_JS = 'console.log("painel");\n'


@pytest.fixture
def dir_painel(tmp_path: Path) -> Path:
    painel = tmp_path / "painel"
    (painel / "assets").mkdir(parents=True)
    (painel / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (painel / "assets" / "app.js").write_text(APP_JS, encoding="utf-8")
    return painel


@pytest.fixture
def amb(tmp_path: Path, dir_painel: Path) -> Ambiente:
    return Ambiente(bind="127.0.0.1", porta=8080, dir_data=tmp_path / "data", dir_painel=dir_painel)


@pytest.fixture
async def cliente(aiohttp_client, amb: Ambiente):
    return await aiohttp_client(criar_app(amb))

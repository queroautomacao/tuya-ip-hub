#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 Quero Automação Ltda
#
# License gate for the runtime dependencies of core (CLAUDE.md, section 10):
# MIT, BSD, ISC and Apache-2.0 enter, plus the Python Software Foundation
# License (Python itself and the aiohttp helpers); anything else fails. Only the runtime
# dependencies are installed, in a throwaway venv, so dev tooling never shows
# up in the table. Usage: scripts/licencas.sh (PYTHON=... picks the interpreter)
#
# Portão de licenças das dependências de execução do core (CLAUDE.md, seção 10):
# MIT, BSD, ISC e Apache-2.0 entram, mais a Python Software Foundation License
# (o próprio Python e as auxiliares do aiohttp); qualquer outra falha. Só as dependências
# de execução são instaladas, num venv descartável, para ferramenta de dev
# nunca aparecer na tabela. Uso: scripts/licencas.sh (PYTHON=... escolhe o interpretador)
set -euo pipefail

cd "$(dirname "$0")/.."

python="${PYTHON:-python3}"
venv="$(mktemp -d)"
trap 'rm -rf "$venv"' EXIT

"$python" -m venv "$venv"
# Why: tomllib and the wheels resolved must match requires-python, or the table lies.
# Por que: tomllib e os wheels resolvidos precisam bater com requires-python, ou a tabela mente.
"$venv/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)' \
    || { echo "FAIL python 3.12 or newer is required (set PYTHON=/path/to/python3.12)" >&2; exit 1; }
read -ra deps <<< "$("$venv/bin/python" -c "import tomllib;print(' '.join(tomllib.load(open('core/pyproject.toml','rb'))['project']['dependencies']))")"
"$venv/bin/pip" install --quiet --no-cache-dir --disable-pip-version-check "${deps[@]}" "pip-licenses>=5"

# Why: PSF is the license of Python itself and of aiohappyeyeballs and
# typing_extensions, dependencies of aiohttp, so it is accepted next to the
# four families above. pip-licenses matches spellings verbatim and does not
# parse license expressions, so the spellings in use today are listed
# ("Apache License 2.0" from multidict, "Apache-2.0 AND MIT" from aiohttp) and
# a new spelling fails closed until a person checks it and adds it here.
# Por que: PSF é a licença do próprio Python e da aiohappyeyeballs e da
# typing_extensions, dependências do aiohttp, então é aceita ao lado das
# quatro famílias acima. O pip-licenses compara grafias ao pé da letra e não
# interpreta expressão de licença, então as grafias em uso hoje estão listadas
# ("Apache License 2.0" da multidict, "Apache-2.0 AND MIT" do aiohttp) e uma
# grafia nova falha fechada até alguém conferir e acrescentar aqui.
permitidas="MIT License;MIT;BSD License;BSD-2-Clause;BSD-3-Clause;Apache Software License;Apache-2.0;Apache License 2.0;Apache-2.0 AND MIT;ISC License;ISC;Python Software Foundation License;PSF-2.0"
ignoradas=(pip setuptools pip-licenses prettytable wcwidth)

"$venv/bin/pip-licenses" --from=mixed --ignore-packages "${ignoradas[@]}"
"$venv/bin/pip-licenses" --from=mixed --ignore-packages "${ignoradas[@]}" --allow-only="$permitidas" >/dev/null
echo "PASS every runtime dependency carries an accepted license"

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const PASTA = dirname(fileURLToPath(import.meta.url));

test("every stylesheet is imported by the one the panel loads (toda folha de estilo é importada pela que o painel carrega)", () => {
  // Why: a sheet nobody imports is dead the day it is written, and nothing says so: the
  // screen simply draws the browser defaults, which is how a row of chips ended up as grey
  // rectangles glued to each other while the file that spaced them sat right there.
  // Por que: uma folha que ninguém importa nasce morta, e nada avisa: a tela simplesmente
  // desenha o padrão do navegador, que foi como uma fileira de fichas virou retângulos
  // cinzas colados uns nos outros com o arquivo que os espaçava ali do lado.
  const raiz = readFileSync(join(PASTA, "estilos.css"), "utf8");
  const importadas = new Set([...raiz.matchAll(/@import\s+"\.\/([^"]+)"/g)].map((achado) => achado[1]));
  const folhas = readdirSync(PASTA).filter((nome) => nome.startsWith("estilos-") && nome.endsWith(".css"));
  assert.ok(folhas.length > 0, "no stylesheet found next to estilos.css");
  const orfas = folhas.filter((nome) => !importadas.has(nome));
  assert.deepEqual(orfas, [], `stylesheets nobody imports: ${orfas.join(", ")}`);
  // And nothing is imported that is not there any more.
  // E nada é importado que não existe mais.
  const perdidas = [...importadas].filter((nome) => !folhas.includes(nome));
  assert.deepEqual(perdidas, [], `imports pointing at nothing: ${perdidas.join(", ")}`);
});

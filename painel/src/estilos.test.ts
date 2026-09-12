// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import assert from "node:assert/strict";
import { readFileSync, readdirSync } from "node:fs";
import { dirname, join } from "node:path";
import { test } from "node:test";
import { fileURLToPath } from "node:url";

const PASTA = dirname(fileURLToPath(import.meta.url));

test("every stylesheet is imported by the one the panel loads (toda folha de estilo é importada pela que o painel carrega)", () => {
  // A sheet nobody imports is dead the day it is written, and nothing says so: the
  // screen simply draws the browser defaults, which is how a row of chips ended up as grey
  // rectangles glued to each other while the file that spaced them sat right there.
  const raiz = readFileSync(join(PASTA, "estilos.css"), "utf8");
  const importadas = new Set([...raiz.matchAll(/@import\s+"\.\/([^"]+)"/g)].map((achado) => achado[1]));
  const folhas = readdirSync(PASTA).filter((nome) => nome.startsWith("estilos-") && nome.endsWith(".css"));
  assert.ok(folhas.length > 0, "no stylesheet found next to estilos.css");
  const orfas = folhas.filter((nome) => !importadas.has(nome));
  assert.deepEqual(orfas, [], `stylesheets nobody imports: ${orfas.join(", ")}`);
  // And nothing is imported that is not there any more.
  const perdidas = [...importadas].filter((nome) => !folhas.includes(nome));
  assert.deepEqual(perdidas, [], `imports pointing at nothing: ${perdidas.join(", ")}`);
});

// One rule list written twice in a sheet is not a duplicate, it is a silent override: the
// last copy wins every property the two disagree on, and the first is dead without a word. It
// is how the whole shell sheet ended up written twice, the second copy painting every logo of
// the panel as a filled rectangle. A selector repeated inside a group (".fichas.teclado"
// then ".teclado") is deliberate and stays allowed: only the same full list twice is caught.
function regrasDe(css: string): { escopo: string; seletor: string }[] {
  const limpo = css.replace(/\/\*[\s\S]*?\*\//g, "");
  const regras: { escopo: string; seletor: string }[] = [];
  const pilha: string[] = [];
  let prelude = "";
  for (const letra of limpo) {
    if (letra === "{") {
      const texto = prelude.trim().replace(/\s+/g, " ");
      if (texto.startsWith("@")) pilha.push(texto);
      else {
        regras.push({ escopo: pilha.join(" > "), seletor: texto });
        pilha.push("");
      }
      prelude = "";
    } else if (letra === "}") {
      pilha.pop();
      prelude = "";
    } else prelude += letra;
  }
  return regras;
}

function folhasDoPainel(): string[] {
  return readdirSync(PASTA).filter((nome) => nome.startsWith("estilos") && nome.endsWith(".css"));
}

test("no stylesheet writes the same rule list twice (nenhuma folha escreve a mesma lista de regras duas vezes)", () => {
  const repetidas: string[] = [];
  for (const nome of folhasDoPainel()) {
    const vistas = new Set<string>();
    for (const { escopo, seletor } of regrasDe(readFileSync(join(PASTA, nome), "utf8"))) {
      const chave = `${escopo}||${seletor}`;
      if (vistas.has(chave)) repetidas.push(`${nome}: ${seletor}${escopo ? ` (dentro de ${escopo})` : ""}`);
      vistas.add(chave);
    }
  }
  assert.deepEqual(repetidas, [], `rule lists written twice: ${repetidas.join("; ")}`);
});

// Var(--x) with no fallback and no definition is not an error the browser reports, it is
// a property that quietly drops: mask-image: var(--logo) became no mask at all, and the box
// under it stayed painted. A fallback is a decision and stays allowed.
test("every variable a stylesheet reads is defined (toda variável que uma folha lê está definida)", () => {
  const folhas = folhasDoPainel();
  const definidas = new Set<string>();
  for (const nome of folhas) {
    const css = readFileSync(join(PASTA, nome), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const achado of css.matchAll(/(--[a-z0-9-]+)\s*:/g)) definidas.add(achado[1]);
  }
  const soltas: string[] = [];
  for (const nome of folhas) {
    const css = readFileSync(join(PASTA, nome), "utf8").replace(/\/\*[\s\S]*?\*\//g, "");
    for (const achado of css.matchAll(/var\(\s*(--[a-z0-9-]+)\s*\)/g)) {
      if (!definidas.has(achado[1])) soltas.push(`${nome}: var(${achado[1]})`);
    }
  }
  assert.deepEqual(soltas, [], `variables read but never defined: ${soltas.join("; ")}`);
});

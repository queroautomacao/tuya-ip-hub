// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import assert from "node:assert/strict";
import { test } from "node:test";
import { ABAS, abaDa, caminhoDa, lerRota, type Rota } from "./rotas.ts";

test("an empty or bare hash is the home screen (hash vazio ou só barra é o início)", () => {
  for (const hash of ["", "#", "#/", "#//", "/"]) {
    assert.deepEqual(lerRota(hash), { tela: "inicio" });
  }
});

test("every tab has an address that reads back to it (toda aba tem endereço que volta a ela)", () => {
  for (const { aba, rota } of ABAS) {
    assert.deepEqual(lerRota(caminhoDa(rota)), rota);
    assert.equal(abaDa(lerRota(caminhoDa(rota))), aba);
  }
});

test("the identity of an equipment survives the round trip, whatever it carries (a identidade sobrevive à ida e volta, leve o que levar)", () => {
  // Why: an identity is a uuid, a MAC or a serial the device chose, so it may carry a slash,
  // a space, a percent sign or a colon, and the address must bring it back byte for byte.
  // Por que: uma identidade é um uuid, um MAC ou um serial que o aparelho escolheu, então ela
  // pode levar barra, espaço, porcento ou dois pontos, e o endereço precisa trazê-la de volta
  // byte a byte.
  for (const identidade of ["uuid-1", "AA:BB:CC:DD:EE:FF", "com/barra", "100%", "com espaço", "novo "]) {
    const rota: Rota = { tela: "equipamento", identidade };
    assert.deepEqual(lerRota(caminhoDa(rota)), rota);
    assert.equal(abaDa(rota), "inicio");
  }
});

test("the registration screen is never mistaken for an identity (a tela de cadastro nunca é confundida com identidade)", () => {
  assert.deepEqual(lerRota("#/equipamentos/novo"), { tela: "novo" });
  assert.equal(abaDa({ tela: "novo" }), "inicio");
  assert.equal(caminhoDa({ tela: "novo" }), "#/equipamentos/novo");
});

test("an address nobody knows lands on the home screen instead of a blank page (endereço desconhecido cai no início, não em página vazia)", () => {
  for (const hash of [
    "#/nada",
    "#/zonas/1",
    "#/equipamentos",
    "#/equipamentos/",
    "#/equipamentos/a/b",
    "#/conta/senha",
    "#/equipamentos/%",
    "#/equipamentos/%E0%A4%A",
  ]) {
    assert.deepEqual(lerRota(hash), { tela: "inicio" }, hash);
  }
});

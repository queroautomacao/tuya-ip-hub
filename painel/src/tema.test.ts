// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import assert from "node:assert/strict";
import { test } from "node:test";
import { TEMAS, atributoDo, ehTema, proximoTema } from "./tema.ts";

test("the theme cycles through the three and comes back (o tema roda pelos três e volta)", () => {
  assert.equal(proximoTema("auto"), "claro");
  assert.equal(proximoTema("claro"), "escuro");
  assert.equal(proximoTema("escuro"), "auto");
});

test("only the three names are a theme (só os três nomes são tema)", () => {
  for (const tema of TEMAS) assert.equal(ehTema(tema), true);
  for (const lixo of ["dark", "light", "", null, undefined, 1]) assert.equal(ehTema(lixo), false);
});

test("automatic leaves the stylesheet to the system (automático deixa a folha para o sistema)", () => {
  assert.equal(atributoDo("auto"), null);
  assert.equal(atributoDo("claro"), "light");
  assert.equal(atributoDo("escuro"), "dark");
});

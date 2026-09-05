// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import assert from "node:assert/strict";
import { test } from "node:test";
import type { LinhaDoLog } from "./api.ts";
import { ORIGENS, comoTexto, filtrar, horaDe } from "./log.ts";

function linhaDe(parcial: Partial<LinhaDoLog> = {}): LinhaDoLog {
  return { t: 1_700_000_000, nivel: "debug", origem: "driver", onde: "linkplay", texto: "oi", ...parcial };
}

test("the clock of a line is the one of the record, to the millisecond (o relógio de uma linha é o do registro, ao milissegundo)", () => {
  // Why: two lines a hundred milliseconds apart are a command and its answer, so the
  // milliseconds are the whole point of the column.
  // Por que: duas linhas a cem milissegundos uma da outra são um comando e a resposta dele,
  // então os milissegundos são todo o sentido da coluna.
  const meio = new Date(2026, 8, 5, 14, 3, 9, 42);
  assert.equal(horaDe(meio.getTime() / 1000), "14:03:09.042");
  assert.equal(horaDe(Number.NaN), "--:--:--.---");
});

test("the copied report carries the three facts of a line (o relato copiado leva os três fatos de uma linha)", () => {
  const quando = new Date(2026, 8, 5, 1, 2, 3, 4);
  const instante = quando.getTime() / 1000;
  const texto = comoTexto([
    linhaDe({ t: instante, texto: "setPlayerCmd:vol:30" }),
    linhaDe({ t: instante, nivel: "warning", onde: "socket", texto: "nada" }),
  ]);
  assert.deepEqual(texto.split("\n"), [
    "01:02:03.004 DEBUG   linkplay: setPlayerCmd:vol:30",
    "01:02:03.004 WARNING socket: nada",
  ]);
  assert.equal(comoTexto([]), "");
});

test("no origin chosen shows every origin (nenhuma origem escolhida mostra todas)", () => {
  // Why: a filter that starts empty and shows nothing is a screen that looks broken the
  // moment it opens.
  // Por que: um filtro que começa vazio e não mostra nada é uma tela que parece quebrada
  // assim que abre.
  const linhas = ORIGENS.map((origem) => linhaDe({ origem, texto: origem }));
  assert.equal(filtrar(linhas, [], "").length, ORIGENS.length);
  assert.deepEqual(
    filtrar(linhas, ["tuya"], "").map((linha) => linha.origem),
    ["tuya"],
  );
  assert.deepEqual(
    filtrar(linhas, ["tuya", "painel"], "").map((linha) => linha.origem),
    ["tuya", "painel"],
  );
});

test("the search reads the module and the message, and never the case (a busca lê o módulo e a mensagem, e nunca a caixa)", () => {
  const linhas = [
    linhaDe({ onde: "linkplay", texto: "setPlayerCmd:VOL:30" }),
    linhaDe({ onde: "socket", texto: "set dp 121" }),
  ];
  assert.equal(filtrar(linhas, [], "vol:30").length, 1);
  assert.equal(filtrar(linhas, [], "SOCKET").length, 1);
  assert.equal(filtrar(linhas, [], "  ").length, 2);
  assert.equal(filtrar(linhas, [], "nada disso").length, 0);
  // A search and an origin narrow together, never one instead of the other.
  // Busca e origem estreitam juntas, nunca uma no lugar da outra.
  assert.equal(filtrar(linhas, ["tuya"], "socket").length, 0);
});

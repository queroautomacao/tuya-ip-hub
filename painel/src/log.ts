// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: what the diary screen decides about a line (when it happened, whether a filter keeps it,
// how it reads in a report someone pastes into an issue) is logic and not drawing, so it lives
// here where a test reaches it without a browser.
// Por que: o que a tela do log decide sobre uma linha (quando aconteceu, se um filtro a
// mantém, como ela se lê num relato que alguém cola numa issue) é lógica e não desenho, então
// mora aqui, onde um teste a alcança sem navegador.

import type { LinhaDoLog } from "./api.ts";

// The origins the daemon writes, in the order the filter draws them.
// As origens que o daemon escreve, na ordem em que o filtro as desenha.
export const ORIGENS = ["driver", "tuya", "painel", "hub"] as const;
export type Origem = (typeof ORIGENS)[number];

function doisDigitos(numero: number): string {
  return numero < 10 ? `0${numero}` : String(numero);
}

// Why: the clock of the record and not of the reading, and to the millisecond, because two
// lines a hundred milliseconds apart are a command and its answer.
// Por que: o relógio do registro e não o da leitura, e ao milissegundo, porque duas linhas a
// cem milissegundos uma da outra são um comando e a resposta dele.
export function horaDe(instante: number): string {
  const quando = new Date(instante * 1000);
  if (Number.isNaN(quando.getTime())) return "--:--:--.---";
  const milesimos = String(quando.getMilliseconds()).padStart(3, "0");
  const horas = doisDigitos(quando.getHours());
  const minutos = doisDigitos(quando.getMinutes());
  const segundos = doisDigitos(quando.getSeconds());
  return `${horas}:${minutos}:${segundos}.${milesimos}`;
}

// Why: what the copy button puts on the clipboard is what lands in an issue, so it carries the
// same three facts the screen shows and nothing of the layout.
// Por que: o que o botão de copiar põe na área de transferência é o que cai numa issue, então
// leva os mesmos três fatos que a tela mostra e nada do layout.
export function comoTexto(linhas: LinhaDoLog[]): string {
  return linhas
    .map((linha) => {
      const nivel = linha.nivel.toUpperCase().padEnd(7);
      return `${horaDe(linha.t)} ${nivel} ${linha.onde}: ${linha.texto}`;
    })
    .join("\n");
}

// Why: no origin chosen means every origin, because a filter that starts empty and shows
// nothing is a screen that looks broken the moment it opens.
// Por que: nenhuma origem escolhida significa todas as origens, porque um filtro que começa
// vazio e não mostra nada é uma tela que parece quebrada assim que abre.
export function filtrar(
  linhas: LinhaDoLog[],
  origens: readonly Origem[],
  busca: string,
): LinhaDoLog[] {
  const procurado = busca.trim().toLowerCase();
  return linhas.filter((linha) => {
    if (origens.length > 0 && !origens.includes(linha.origem as Origem)) return false;
    if (procurado === "") return true;
    return `${linha.onde} ${linha.texto}`.toLowerCase().includes(procurado);
  });
}

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// What the diary screen decides about a line (when it happened, whether a filter keeps it,
// how it reads in a report someone pastes into an issue) is logic and not drawing, so it lives
// here where a test reaches it without a browser.

import type { LinhaDoLog } from "./api.ts";

// The origins the daemon writes, in the order the filter draws them.
export const ORIGENS = ["driver", "tuya", "panel", "hub"] as const;
export type Origem = (typeof ORIGENS)[number];

function doisDigitos(numero: number): string {
  return numero < 10 ? `0${numero}` : String(numero);
}

// The clock of the record and not of the reading, and to the millisecond, because two
// lines a hundred milliseconds apart are a command and its answer.
export function horaDe(instante: number): string {
  const quando = new Date(instante * 1000);
  if (Number.isNaN(quando.getTime())) return "--:--:--.---";
  const milesimos = String(quando.getMilliseconds()).padStart(3, "0");
  const horas = doisDigitos(quando.getHours());
  const minutos = doisDigitos(quando.getMinutes());
  const segundos = doisDigitos(quando.getSeconds());
  return `${horas}:${minutos}:${segundos}.${milesimos}`;
}

// What the copy button puts on the clipboard is what lands in an issue, so it carries the
// same three facts the screen shows and nothing of the layout.
export function comoTexto(linhas: LinhaDoLog[]): string {
  return linhas
    .map((linha) => {
      const nivel = linha.nivel.toUpperCase().padEnd(7);
      return `${horaDe(linha.t)} ${nivel} ${linha.onde}: ${linha.texto}`;
    })
    .join("\n");
}

// No origin chosen means every origin, because a filter that starts empty and shows
// nothing is a screen that looks broken the moment it opens.
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

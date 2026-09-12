// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The brand of a driver is a name, and the logo of that brand is a file the
// installation may or may not be authorised to carry. So the name is the fact and the file is
// optional: whatever is dropped in src/marcas is found at build time and drawn, and a brand
// with no file is written in the type of the panel instead. Nothing is fetched at runtime, so
// a hub with no internet draws exactly what a hub with internet draws.

import { apelidoDa } from "./marcas.ts";

const ARQUIVOS = import.meta.glob("./marcas/*.{svg,png,webp}", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

const POR_APELIDO = new Map(
  Object.entries(ARQUIVOS).map(([caminho, url]) => {
    const arquivo = caminho.slice(caminho.lastIndexOf("/") + 1);
    return [apelidoDa(arquivo.slice(0, arquivo.lastIndexOf("."))), url];
  }),
);

export function logoDa(marca: string): string | null {
  return POR_APELIDO.get(apelidoDa(marca)) ?? null;
}

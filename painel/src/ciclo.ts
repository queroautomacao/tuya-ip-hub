// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The equipment screen reads two routes on every cycle, and one of them failing
// must not erase what the other answered nor hide that it failed; keeping the merge here
// makes that a rule with a test instead of a sequence of effects in a component.

import type { Equipamento, ItemCatalogo } from "./equipamentos.ts";

export type Tentativa<T> = { ok: true; valor: T } | { ok: false; codigo: string };

export interface Leitura {
  // Null is "never read", which is not the same as an empty catalog; the panel would
  // otherwise tell the integrator that this image ships no driver because one request
  // failed once, and that is a false statement about the product.
  catalogo: ItemCatalogo[] | null;
  lista: Equipamento[] | null;
  erro: string | null;
}

export const LEITURA_INICIAL: Leitura = { catalogo: null, lista: null, erro: null };

export function aplicarCiclo(
  anterior: Leitura,
  catalogo: Tentativa<ItemCatalogo[]>,
  lista: Tentativa<Equipamento[]>,
): Leitura {
  let erro: string | null = null;
  if (!lista.ok) erro = lista.codigo;
  else if (!catalogo.ok) erro = catalogo.codigo;
  return {
    catalogo: catalogo.ok ? catalogo.valor : anterior.catalogo,
    lista: lista.ok ? lista.valor : anterior.lista,
    erro,
  };
}

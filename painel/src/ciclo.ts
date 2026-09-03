// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the equipment screen reads two routes on every cycle, and one of them failing
// must not erase what the other answered nor hide that it failed; keeping the merge here
// makes that a rule with a test instead of a sequence of effects in a component.
// Por que: a tela de equipamentos lê duas rotas a cada ciclo, e uma delas falhando não
// pode apagar o que a outra respondeu nem esconder que falhou; manter a junção aqui faz
// disso uma regra com teste em vez de uma sequência de efeitos num componente.

import type { Equipamento, ItemCatalogo } from "./equipamentos.ts";

export type Tentativa<T> = { ok: true; valor: T } | { ok: false; codigo: string };

export interface Leitura {
  // Why: null is "never read", which is not the same as an empty catalog; the panel would
  // otherwise tell the integrator that this image ships no driver because one request
  // failed once, and that is a false statement about the product.
  // Por que: null é "nunca lido", que não é o mesmo que catálogo vazio; senão o painel
  // diria ao integrador que esta imagem não traz driver porque uma requisição falhou uma
  // vez, e isso é uma afirmação falsa sobre o produto.
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

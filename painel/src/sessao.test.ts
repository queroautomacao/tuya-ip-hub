// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { test } from "node:test";

import { CHAVE_SESSAO, guardar, ler, limpar } from "./sessao.ts";

// Why: node has no localStorage, so the double is the global itself, put back
// after every test so one case never leaks into another.
// Por que: o node não tem localStorage, então o dublê é o próprio global, devolvido
// depois de cada teste para um caso nunca vazar no outro.
function comDescritor(descritor: PropertyDescriptor, corpo: () => void): void {
  const antes = Object.getOwnPropertyDescriptor(globalThis, "localStorage");
  Object.defineProperty(globalThis, "localStorage", { configurable: true, ...descritor });
  try {
    corpo();
  } finally {
    if (antes) Object.defineProperty(globalThis, "localStorage", antes);
    else delete (globalThis as unknown as Record<string, unknown>).localStorage;
  }
}

function comStorage(deposito: unknown, corpo: () => void): void {
  comDescritor({ value: deposito, writable: true }, corpo);
}

function memoria(): Storage {
  const mapa = new Map<string, string>();
  return {
    get length(): number {
      return mapa.size;
    },
    clear: (): void => mapa.clear(),
    getItem: (chave: string): string | null => mapa.get(chave) ?? null,
    key: (indice: number): string | null => [...mapa.keys()][indice] ?? null,
    removeItem: (chave: string): void => {
      mapa.delete(chave);
    },
    setItem: (chave: string, valor: string): void => {
      mapa.set(chave, valor);
    },
  } as Storage;
}

function bloqueado(): Storage {
  const estourar = (): never => {
    throw new Error("storage bloqueado");
  };
  return {
    get length(): number {
      return estourar();
    },
    clear: estourar,
    getItem: estourar,
    key: estourar,
    removeItem: estourar,
    setItem: estourar,
  } as unknown as Storage;
}

test("the token survives a round trip under the agreed key (o token sobrevive à ida e volta na chave combinada)", () => {
  const deposito = memoria();
  comStorage(deposito, () => {
    assert.equal(ler(), null);
    guardar("token-de-teste");
    assert.equal(ler(), "token-de-teste");
    assert.equal(deposito.getItem(CHAVE_SESSAO), "token-de-teste");
    limpar();
    assert.equal(ler(), null);
    assert.equal(deposito.getItem(CHAVE_SESSAO), null);
  });
});

test("an empty stored value reads as no session (valor vazio guardado é lido como nenhuma sessão)", () => {
  const deposito = memoria();
  deposito.setItem(CHAVE_SESSAO, "");
  comStorage(deposito, () => {
    assert.equal(ler(), null);
  });
});

test("a storage that throws on every call never breaks the panel (storage que estoura em toda chamada nunca quebra o painel)", () => {
  comStorage(bloqueado(), () => {
    assert.doesNotThrow(() => guardar("token-de-teste"));
    assert.equal(ler(), null);
    assert.doesNotThrow(() => limpar());
  });
});

test("a storage whose own access throws never breaks the panel (storage cujo acesso já estoura nunca quebra o painel)", () => {
  comDescritor(
    {
      get(): Storage {
        throw new Error("storage bloqueado");
      },
    },
    () => {
      assert.doesNotThrow(() => guardar("token-de-teste"));
      assert.equal(ler(), null);
      assert.doesNotThrow(() => limpar());
    },
  );
});

test("no storage at all is simply no session (sem storage nenhum é simplesmente nenhuma sessão)", () => {
  comStorage(undefined, () => {
    assert.doesNotThrow(() => guardar("token-de-teste"));
    assert.equal(ler(), null);
    assert.doesNotThrow(() => limpar());
  });
});

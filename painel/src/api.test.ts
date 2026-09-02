// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { beforeEach, test } from "node:test";

import {
  CODIGO_CORPO_INVALIDO,
  CODIGO_ERRO_HTTP,
  CODIGO_SEM_RESPOSTA,
  ErroApi,
  entrar,
  lerEstado,
  lerSessao,
  sair,
  senhaCurta,
  tomarPosse,
  trocarSenha,
} from "./api.ts";
import { guardar, ler, limpar } from "./sessao.ts";

type Fetch = typeof globalThis.fetch;

interface Registro {
  url: string;
  init: RequestInit;
}

// Why: node has no localStorage, and api.ts reads the token on every request.
// Por que: o node não tem localStorage, e o api.ts lê o token em toda requisição.
Object.defineProperty(globalThis, "localStorage", {
  configurable: true,
  writable: true,
  value: (() => {
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
  })(),
});

beforeEach(() => limpar());

function duble(corpo: string, status = 200): { fetch: Fetch; chamadas: Registro[] } {
  const chamadas: Registro[] = [];
  const espiao: Fetch = async (entrada, init) => {
    chamadas.push({ url: String(entrada), init: init ?? {} });
    return new Response(corpo, { status });
  };
  return { fetch: espiao, chamadas };
}

async function comFetch(substituto: Fetch, corpo: () => Promise<void>): Promise<void> {
  const original = globalThis.fetch;
  globalThis.fetch = substituto;
  try {
    await corpo();
  } finally {
    globalThis.fetch = original;
  }
}

function cabecalhosDe(registro: Registro): Record<string, string> {
  return (registro.init.headers ?? {}) as Record<string, string>;
}

const ESTADO = {
  ok: true,
  code: null,
  configurado: true,
  versao: "0.1.0",
  schema_version: 1,
  nome_instalacao: "bancada",
};
const CREDENCIAL = { ok: true, code: null, token: "token-novo", expira_em_s: 86400 };

test("lerEstado returns the body of a valid 200 (devolve o corpo de um 200 válido)", async () => {
  const { fetch, chamadas } = duble(JSON.stringify(ESTADO));
  await comFetch(fetch, async () => {
    assert.deepEqual(await lerEstado(), {
      configurado: true,
      versao: "0.1.0",
      schema_version: 1,
      nome_instalacao: "bancada",
    });
  });
  assert.equal(chamadas[0].url, "/api/estado");
  assert.equal(cabecalhosDe(chamadas[0]).Accept, "application/json");
  assert.equal(chamadas[0].init.cache, "no-store");
  assert.equal(cabecalhosDe(chamadas[0]).Authorization, undefined);
});

test("a 200 outside the contract is corpo_invalido (um 200 fora do contrato é corpo_invalido)", async () => {
  const { fetch } = duble('{"ok": true, "code": null}');
  await comFetch(fetch, async () => {
    await assert.rejects(lerEstado(), (erro: unknown) => {
      assert.ok(erro instanceof ErroApi);
      assert.equal(erro.code, CODIGO_CORPO_INVALIDO);
      return true;
    });
  });
});

test("a 401 hands back the code and drops the stored token (devolve o código e larga o token guardado)", async () => {
  guardar("token-velho");
  const { fetch, chamadas } = duble('{"ok": false, "code": "sessao_invalida"}', 401);
  await comFetch(fetch, async () => {
    await assert.rejects(lerSessao(), (erro: unknown) => {
      assert.ok(erro instanceof ErroApi);
      assert.equal(erro.code, "sessao_invalida");
      assert.equal(erro.status, 401);
      return true;
    });
  });
  assert.equal(cabecalhosDe(chamadas[0]).Authorization, "Bearer token-velho");
  assert.equal(ler(), null);
});

test("a 429 keeps the rate limit code (um 429 mantém o código do limite de tentativas)", async () => {
  const { fetch } = duble('{"ok": false, "code": "muitas_tentativas"}', 429);
  await comFetch(fetch, async () => {
    await assert.rejects(entrar("senha-de-teste"), (erro: unknown) => {
      assert.ok(erro instanceof ErroApi);
      assert.equal(erro.code, "muitas_tentativas");
      assert.equal(erro.status, 429);
      return true;
    });
  });
  assert.equal(ler(), null);
});

test("a body that is not JSON never leaks a parse error (corpo que não é JSON nunca vaza erro de parse)", async () => {
  await comFetch(duble("<html>painel</html>").fetch, async () => {
    await assert.rejects(lerEstado(), { code: CODIGO_CORPO_INVALIDO });
  });
  await comFetch(duble("erro interno", 500).fetch, async () => {
    await assert.rejects(lerEstado(), { code: CODIGO_ERRO_HTTP });
  });
});

test("a fetch that throws becomes sem_resposta (um fetch que estoura vira sem_resposta)", async () => {
  const explosivo: Fetch = async () => {
    throw new TypeError("network");
  };
  await comFetch(explosivo, async () => {
    await assert.rejects(lerEstado(), { code: CODIGO_SEM_RESPOSTA });
  });
});

test("tomarPosse sends the code and stores the session (envia o código e guarda a sessão)", async () => {
  const { fetch, chamadas } = duble(JSON.stringify(CREDENCIAL));
  await comFetch(fetch, async () => {
    assert.deepEqual(await tomarPosse("ABCD-EFGH-JKLM-NPQR", "senha-de-teste"), {
      token: "token-novo",
      expira_em_s: 86400,
    });
  });
  assert.equal(chamadas[0].url, "/api/posse");
  assert.equal(chamadas[0].init.method, "POST");
  assert.deepEqual(JSON.parse(String(chamadas[0].init.body)), {
    codigo: "ABCD-EFGH-JKLM-NPQR",
    senha: "senha-de-teste",
  });
  assert.equal(ler(), "token-novo");
});

test("trocarSenha sends both fields and keeps the caller signed in (envia os dois campos e mantém quem chamou dentro)", async () => {
  guardar("token-velho");
  const { fetch, chamadas } = duble(JSON.stringify(CREDENCIAL));
  await comFetch(fetch, async () => {
    await trocarSenha("senha-velha", "senha-nova-de-teste");
  });
  assert.equal(chamadas[0].url, "/api/senha");
  assert.deepEqual(JSON.parse(String(chamadas[0].init.body)), {
    senha_atual: "senha-velha",
    senha_nova: "senha-nova-de-teste",
  });
  assert.equal(ler(), "token-novo");
});

test("a credential without a token is refused (credencial sem token é recusada)", async () => {
  const { fetch } = duble('{"ok": true, "code": null, "expira_em_s": 10}');
  await comFetch(fetch, async () => {
    await assert.rejects(entrar("senha-de-teste"), { code: CODIGO_CORPO_INVALIDO });
  });
  assert.equal(ler(), null);
});

test("sair drops the token even when the daemon refuses (larga o token mesmo quando o daemon recusa)", async () => {
  guardar("token-velho");
  const { fetch } = duble('{"ok": false, "code": "erro_interno"}', 500);
  await comFetch(fetch, async () => {
    await assert.rejects(sair(), { code: "erro_interno" });
  });
  assert.equal(ler(), null);
});

test("a 401 senha_invalida keeps the live session (um 401 senha_invalida mantém a sessão viva)", async () => {
  guardar("token-vivo");
  const { fetch, chamadas } = duble('{"ok": false, "code": "senha_invalida"}', 401);
  await comFetch(fetch, async () => {
    await assert.rejects(trocarSenha("senha-errada", "senha-nova-de-teste"), {
      code: "senha_invalida",
    });
  });
  assert.equal(cabecalhosDe(chamadas[0]).Authorization, "Bearer token-vivo");
  // Why: mistyping the current password says nothing about the session, and signing
  // the user out over it throws away a credential the daemon still accepts.
  // Por que: errar a senha atual não diz nada sobre a sessão, e desconectar por isso
  // joga fora uma credencial que o daemon ainda aceita.
  assert.equal(ler(), "token-vivo");
});

test("a 401 nao_autenticado drops the token (um 401 nao_autenticado larga o token)", async () => {
  guardar("token-velho");
  const { fetch } = duble('{"ok": false, "code": "nao_autenticado"}', 401);
  await comFetch(fetch, async () => {
    await assert.rejects(lerSessao(), { code: "nao_autenticado" });
  });
  assert.equal(ler(), null);
});

test("senhaCurta counts code points, as the daemon does (conta pontos de código, como o daemon)", () => {
  assert.equal(senhaCurta("12345678"), false);
  assert.equal(senhaCurta("1234567"), true);
  // Why: four astral characters are eight UTF-16 units and four code points, so
  // String.length would let through a password the daemon answers senha_curta to.
  // Por que: quatro caracteres astrais são oito unidades UTF-16 e quatro pontos de
  // código, então o String.length deixaria passar uma senha que o daemon recusa com
  // senha_curta.
  assert.equal(senhaCurta("\u{1F600}\u{1F600}\u{1F600}\u{1F600}"), true);
  assert.equal(senhaCurta("\u{1F600}".repeat(8)), false);
  // Why: the count is code points and not grapheme clusters, because python len is
  // what the daemon runs; eight accented letters written as letter plus combining
  // mark are sixteen code points there and must not be refused here.
  // Por que: a contagem é de pontos de código e não de grafemas, porque o len do
  // python é o que o daemon roda; oito letras acentuadas escritas como letra mais
  // acento combinante são dezesseis pontos de código lá e não podem ser recusadas aqui.
  assert.equal(senhaCurta("e\u0301".repeat(4)), false);
});

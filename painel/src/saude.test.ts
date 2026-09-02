// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { test } from "node:test";

import { ehSaude, formatarUptime, lerSaude } from "./saude.ts";

type Fetch = typeof globalThis.fetch;

// Why: lerSaude is the only network path of the panel, so the double is the
// fetch global itself, saved and put back so one test never leaks into another.
// Por que: lerSaude é o único caminho de rede do painel, então o dublê é o
// próprio fetch global, salvo e devolvido para um teste nunca vazar no outro.
async function comFetch(duble: Fetch, corpo: () => Promise<void>): Promise<void> {
  const original = globalThis.fetch;
  globalThis.fetch = duble;
  try {
    await corpo();
  } finally {
    globalThis.fetch = original;
  }
}

function responde(corpo: string, status = 200): Fetch {
  return async () => new Response(corpo, { status });
}

test("formatarUptime shows days only from 24 h on (mostra dias só a partir de 24 h)", () => {
  assert.equal(formatarUptime(0), "00:00:00");
  assert.equal(formatarUptime(59), "00:00:59");
  assert.equal(formatarUptime(60), "00:01:00");
  assert.equal(formatarUptime(3600), "01:00:00");
  assert.equal(formatarUptime(86399), "23:59:59");
  assert.equal(formatarUptime(86400), "1d 00:00:00");
  assert.equal(formatarUptime(90061), "1d 01:01:01");
  assert.equal(formatarUptime(864000), "10d 00:00:00");
});

test("formatarUptime clamps negatives and floors fractions (trava negativos e trunca fração)", () => {
  assert.equal(formatarUptime(-5), "00:00:00");
  assert.equal(formatarUptime(61.9), "00:01:01");
});

test("ehSaude accepts only the /health contract (aceita só o contrato do /health)", () => {
  const valido = { ok: true, code: null, versao: "0.1.0", schema_version: 1, uptime_s: 12 };
  assert.equal(ehSaude(valido), true);
  assert.equal(ehSaude({ ...valido, code: "x" }), true);
  assert.equal(ehSaude(null), false);
  assert.equal(ehSaude("texto"), false);
  assert.equal(ehSaude([]), false);
  assert.equal(ehSaude({ ...valido, ok: "true" }), false);
  assert.equal(ehSaude({ ...valido, uptime_s: "12" }), false);
  assert.equal(ehSaude({ ...valido, versao: 1 }), false);
  assert.equal(ehSaude({ ...valido, code: 3 }), false);
  assert.equal(ehSaude({ ...valido, schema_version: "1" }), false);
  const { uptime_s: _omitido, ...semUptime } = valido;
  assert.equal(ehSaude(semUptime), false);
  const { versao: _omitida, ...semVersao } = valido;
  assert.equal(ehSaude(semVersao), false);
});

test("lerSaude turns a non-2xx answer into http_<status> (vira http_<status> numa resposta não 2xx)", async () => {
  await comFetch(responde('{"erro": true}', 503), async () => {
    await assert.rejects(lerSaude(), { message: "http_503" });
  });
});

test("lerSaude refuses a 200 outside the contract (recusa um 200 fora do contrato)", async () => {
  await comFetch(responde('{"ok": true}'), async () => {
    await assert.rejects(lerSaude(), { message: "corpo_invalido" });
  });
});

test("lerSaude resolves to the body of a valid 200 (devolve o corpo de um 200 válido)", async () => {
  const saude = { ok: true, code: null, versao: "0.1.0", schema_version: 1, uptime_s: 7 };
  await comFetch(responde(JSON.stringify(saude)), async () => {
    assert.deepEqual(await lerSaude(), saude);
  });
});

test("lerSaude refuses a body that is not JSON, without the parse error (recusa corpo que não é JSON, sem o erro de parse)", async () => {
  await comFetch(responde("<html>painel</html>"), async () => {
    await assert.rejects(lerSaude(), { message: "corpo_invalido" });
  });
});

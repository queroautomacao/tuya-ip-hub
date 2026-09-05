// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import { LEITURA_INICIAL, aplicarCiclo } from "./ciclo.ts";
import {
  CODIGOS_EQUIPAMENTO,
  DETALHES,
  camposVisiveis,
  controles,
  lerAchado,
  lerEquipamento,
  lerItemCatalogo,
  lerLista,
  linhasDoEstado,
  prepararAcao,
  rotuloDoTipo,
  textoDoManifesto,
  type Campo,
  type Equipamento,
  type EstadoEquipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";

function estadoDe(parcial: Partial<EstadoEquipamento> = {}): EstadoEquipamento {
  return {
    online: true,
    ligado: null,
    volume: null,
    mudo: null,
    fonte: null,
    fontes: [],
    reproduzindo: null,
    tocando: null,
    temperatura: null,
    modo: null,
    vento: null,
    detalhe: "",
    ...parcial,
  };
}

function itemDe(parcial: Partial<ItemCatalogo> = {}): ItemCatalogo {
  return {
    tipo: "projetor_exemplo",
    categoria: "projetor",
    motor: "nativo",
    auth: "codigo",
    capacidades: ["ligar", "desligar"],
    teclas: [],
    modos: [],
    ventos: [],
    produto: "av",
    template: "tv",
    rotulo: { pt: "Projetor de exemplo", en: "Example projector" },
    textos: { pt: { descricao: "Um projetor" }, en: { descricao: "A projector" } },
    config_campos: [],
    ...parcial,
  };
}

function campoDe(parcial: Partial<Campo> & { nome: string }): Campo {
  return { tipo: "texto", obrigatorio: false, padrao: "", ...parcial };
}

test("controles offers only what the manifest declares (oferece só o que o manifesto declara)", () => {
  const so_ligar = controles(["ligar"]);
  assert.deepEqual(so_ligar, [{ acao: "ligar", especie: "simples" }]);
  assert.deepEqual(controles([]), []);
  assert.deepEqual(controles(["voar", "desligar_tudo", "VOLUME"]), []);
  const misturado = controles(["comando_extra", "volume", "ligar", "ligar", "fonte"]);
  assert.deepEqual(
    misturado.map((controle) => controle.acao),
    ["ligar", "volume", "fonte", "comando_extra"],
  );
  assert.deepEqual(
    misturado.map((controle) => controle.especie),
    ["simples", "escala", "escolha", "texto"],
  );
});

test("prepararAcao refuses a value the action cannot take (recusa valor que a ação não aceita)", () => {
  const escala = { acao: "volume", especie: "escala" } as const;
  const escolha = { acao: "fonte", especie: "escolha" } as const;
  assert.deepEqual(prepararAcao({ acao: "ligar", especie: "simples" }, "", estadoDe()), {
    ok: true,
    valor: null,
  });
  assert.deepEqual(prepararAcao({ acao: "mudo", especie: "alternar" }, "", estadoDe()), {
    ok: true,
    valor: true,
  });
  assert.deepEqual(
    prepararAcao({ acao: "mudo", especie: "alternar" }, "", estadoDe({ mudo: true })),
    { ok: true, valor: false },
  );
  assert.deepEqual(prepararAcao(escala, " 0 ", estadoDe()), { ok: true, valor: 0 });
  assert.deepEqual(prepararAcao(escala, "100", estadoDe()), { ok: true, valor: 100 });
  for (const ruim of ["", "101", "-1", "1.5", "abc", "9999", "0x10", "1e2"]) {
    assert.deepEqual(prepararAcao(escala, ruim, estadoDe()), {
      ok: false,
      codigo: "invalid_value",
    });
  }
  assert.deepEqual(prepararAcao(escolha, " HDMI1 ", estadoDe()), { ok: true, valor: "HDMI1" });
  assert.deepEqual(prepararAcao(escolha, "   ", estadoDe()), {
    ok: false,
    codigo: "invalid_value",
  });
});

test("linhasDoEstado keeps false and zero and drops what is absent (mantém falso e zero e larga o ausente)", () => {
  assert.deepEqual(linhasDoEstado(estadoDe()), []);
  const cheio = estadoDe({
    ligado: false,
    volume: 0,
    mudo: false,
    fonte: "HDMI1",
    tocando: "faixa",
    detalhe: "tipo_desconhecido",
  });
  assert.deepEqual(
    linhasDoEstado(cheio).map((linha) => linha.campo),
    ["ligado", "volume", "mudo", "fonte", "tocando", "detalhe"],
  );
  assert.deepEqual(linhasDoEstado(estadoDe({ ligado: false })), [
    { campo: "ligado", especie: "logico", logico: false },
  ]);
  assert.deepEqual(linhasDoEstado(estadoDe({ volume: 0 })), [
    { campo: "volume", especie: "numero", numero: 0 },
  ]);
  assert.deepEqual(linhasDoEstado(estadoDe({ fonte: "", tocando: "", detalhe: "" })), []);
});

// Why: a SEGREDO is a device credential that never leaves the daemon; if some answer
// ever carried one back, the panel still refuses to put it on the screen.
// Por que: um SEGREDO é credencial de aparelho que nunca sai do daemon; se alguma
// resposta trouxer uma de volta, o painel ainda assim recusa pô-la na tela.
test("camposVisiveis never renders a secret field (nunca mostra um campo de segredo)", () => {
  const item = itemDe({
    config_campos: [
      campoDe({ nome: "porta", tipo: "inteiro", padrao: "4352" }),
      campoDe({ nome: "senha", tipo: "segredo" }),
    ],
  });
  const visiveis = camposVisiveis(item, { porta: "4352", senha: "segredo-do-aparelho", vazio: "" });
  assert.deepEqual(visiveis, [{ nome: "porta", valor: "4352" }]);
  assert.equal(JSON.stringify(visiveis).includes("segredo-do-aparelho"), false);
  assert.deepEqual(camposVisiveis(undefined, { porta: "4352" }), [
    { nome: "porta", valor: "4352" },
  ]);
});

test("the driver labels come from the manifest, never from the panel (os rótulos do driver vêm do manifesto)", () => {
  const item = itemDe();
  assert.equal(rotuloDoTipo(item, "pt", item.tipo), "Projetor de exemplo");
  assert.equal(rotuloDoTipo(item, "en", item.tipo), "Example projector");
  assert.equal(rotuloDoTipo(undefined, "pt", "tipo_sumido"), "tipo_sumido");
  assert.equal(textoDoManifesto(item, "en", "descricao"), "A projector");
  assert.equal(textoDoManifesto(item, "pt", "auth_ajuda"), "");
  assert.equal(textoDoManifesto(undefined, "pt", "descricao"), "");
  // Why: a manifest missing one language must show the other one, never the word
  // undefined on the screen of an installation.
  // Por que: um manifesto sem um dos idiomas precisa mostrar o outro, nunca a palavra
  // undefined na tela de uma instalação.
  const meio = itemDe({ rotulo: { en: "Only english" }, textos: { en: { descricao: "Only" } } });
  assert.equal(rotuloDoTipo(meio, "pt", meio.tipo), "Only english");
  assert.equal(textoDoManifesto(meio, "pt", "descricao"), "Only");
});

test("an answer outside the contract is refused whole (uma resposta fora do contrato é recusada inteira)", () => {
  const cru = {
    identidade: "uuid-1",
    tipo: "projetor_exemplo",
    nome: "Projetor",
    ip: "192.0.2.10",
    campos: { porta: "4352" },
    segredos_definidos: ["senha"],
    estado: { online: true, ligado: false, volume: null, fontes: ["HDMI1"], detalhe: "" },
  };
  const lido = lerEquipamento(cru);
  assert.equal(lido?.estado.mudo, null);
  assert.equal(lido?.estado.tocando, null);
  assert.deepEqual(lido?.estado.fontes, ["HDMI1"]);
  assert.equal(lerEquipamento({ ...cru, estado: { ...cru.estado, online: "sim" } }), null);
  assert.equal(lerEquipamento({ ...cru, estado: { ...cru.estado, volume: "40" } }), null);
  assert.equal(lerEquipamento({ ...cru, identidade: "" }), null);
  assert.equal(lerEquipamento({ ...cru, campos: { porta: 4352 } }), null);
  assert.equal(lerLista([cru, { ...cru, identidade: "" }], lerEquipamento), null);
  assert.equal(lerLista({ equipamentos: [] }, lerEquipamento), null);
  assert.deepEqual(lerLista([], lerEquipamento), []);
});

test("the catalog and the sweep are read against the contract (o catálogo e a varredura são lidos contra o contrato)", () => {
  const cru = {
    tipo: "projetor_exemplo",
    categoria: "projetor",
    motor: "nativo",
    auth: "codigo",
    capacidades: ["ligar"],
    rotulo: { pt: "Projetor", en: "Projector" },
    textos: { pt: { descricao: "d" }, en: { descricao: "d" } },
    config_campos: [{ nome: "porta", tipo: "inteiro", obrigatorio: false, padrao: "4352" }],
  };
  assert.equal(lerItemCatalogo(cru)?.config_campos[0].tipo, "inteiro");
  assert.equal(lerItemCatalogo({ ...cru, capacidades: "ligar" }), null);
  assert.equal(lerItemCatalogo({ ...cru, rotulo: { pt: 1 } }), null);
  assert.equal(lerItemCatalogo({ ...cru, config_campos: [{ nome: "porta", tipo: "senha" }] }), null);
  const achado = { tipo: "", identidade: "", ip: "192.0.2.10", porta: null, descricao: "servidor" };
  assert.deepEqual(lerAchado(achado), { ...achado, ja_cadastrado: false });
  assert.equal(lerAchado({ ...achado, ja_cadastrado: true })?.ja_cadastrado, true);
  assert.equal(lerAchado({ ...achado, ip: "" }), null);
  assert.equal(lerAchado({ ...achado, porta: "4352" }), null);
});

// Why: the API answers a stable code and never a human phrase, so a code with no entry
// in the dictionaries would reach the integrator as the code itself.
// Por que: a API responde um código estável e nunca uma frase humana, então um código
// sem entrada nos dicionários chegaria ao integrador como o próprio código.
test("every code of this milestone has a phrase in both dictionaries (todo código deste marco tem frase nos dois dicionários)", () => {
  for (const idioma of ["pt", "en"]) {
    const caminho = new URL(`./i18n/${idioma}.json`, import.meta.url);
    const textos = JSON.parse(readFileSync(caminho, "utf-8")) as Record<string, string>;
    for (const codigo of CODIGOS_EQUIPAMENTO) {
      const frase = textos[`erro_${codigo}`];
      assert.equal(typeof frase, "string", `${idioma}.erro_${codigo}`);
      assert.ok(frase.trim().length > 0, `${idioma}.erro_${codigo}`);
    }
  }
});

// Why: Estado.detalhe carries the empty string or ONE code of a fixed vocabulary, so a
// phrase the daemon composed, or a device answer, has nothing to do on this screen; the
// panel refuses to print what it cannot translate (section 11).
// Por que: Estado.detalhe carrega o texto vazio ou UM código de vocabulário fixo, então
// uma frase que o daemon compôs, ou uma resposta de aparelho, não tem o que fazer nesta
// tela; o painel recusa imprimir o que não sabe traduzir (seção 11).
test("detalhe is rendered as a code and never as a phrase (é desenhado como código e nunca como frase)", () => {
  for (const codigo of DETALHES) {
    assert.deepEqual(linhasDoEstado(estadoDe({ detalhe: codigo })), [
      { campo: "detalhe", especie: "codigo", codigo },
    ]);
  }
  for (const invadido of [
    "eq_offline: 192.0.2.10 nao respondeu",
    "tipo_desconhecido: projetor_sumido",
    "LAMP FAILURE ",
    "a".repeat(900),
    "eq offline",
    "EQ_OFFLINE",
  ]) {
    assert.deepEqual(linhasDoEstado(estadoDe({ detalhe: invadido })), [], invadido.slice(0, 30));
  }
});

// Why: one transient failure of the catalog read used to leave the panel telling the
// integrator that this image ships no driver, which is a false statement about the
// product, and no later cycle ever corrected it.
// Por que: uma falha passageira na leitura do catálogo deixava o painel dizendo ao
// integrador que esta imagem não traz driver, o que é uma afirmação falsa sobre o
// produto, e nenhum ciclo posterior corrigia isso.
test("a failed catalog read is never an empty catalog (catálogo que falhou nunca é catálogo vazio)", () => {
  const item = itemDe();
  const equipamento = { identidade: "uuid-1" } as Equipamento;
  const semCatalogo = aplicarCiclo(
    LEITURA_INICIAL,
    { ok: false, codigo: "sem_resposta" },
    { ok: true, valor: [equipamento] },
  );
  assert.equal(semCatalogo.catalogo, null);
  assert.equal(semCatalogo.erro, "sem_resposta");
  assert.deepEqual(semCatalogo.lista, [equipamento]);
  const depois = aplicarCiclo(semCatalogo, { ok: true, valor: [item] }, { ok: true, valor: [] });
  assert.deepEqual(depois.catalogo, [item]);
  assert.equal(depois.erro, null);
  // Why: a cycle that fails after a good one keeps what was read, so the screen does not
  // empty itself while the daemon is restarting.
  // Por que: um ciclo que falha depois de um bom mantém o que foi lido, então a tela não
  // se esvazia enquanto o daemon reinicia.
  const caiu = aplicarCiclo(
    depois,
    { ok: false, codigo: "sem_resposta" },
    { ok: false, codigo: "sem_resposta" },
  );
  assert.deepEqual(caiu.catalogo, [item]);
  assert.deepEqual(caiu.lista, []);
  assert.equal(caiu.erro, "sem_resposta");
});

// Why: the description of a sweep answer and the name of a device are text a stranger on
// the segment chose, so the stylesheet has to wrap it instead of letting it stretch the
// card past the edge of the screen.
// Por que: a descrição de uma resposta da varredura e o nome de um aparelho são texto que
// um estranho no segmento escolheu, então a folha de estilo precisa quebrá-lo em vez de
// deixá-lo esticar o cartão para fora da tela.
test("device text has a wrapping rule (texto de aparelho tem regra de quebra)", () => {
  const caminho = new URL("./estilos-equipamentos.css", import.meta.url);
  const css = readFileSync(caminho, "utf-8");
  const inicio = css.indexOf(".cartao dd,");
  assert.ok(inicio > 0, "no wrapping rule for device text");
  const bloco = css.slice(inicio, css.indexOf("}", inicio));
  for (const seletor of [".cartao dd", ".equipamento h3", ".achados p", ".discreto"]) {
    assert.ok(bloco.includes(seletor), seletor);
  }
  assert.ok(bloco.includes("overflow-wrap: anywhere"), "overflow-wrap");
});

// Why: detalhe is a code like any other, so a member of the vocabulary with no phrase in
// one of the dictionaries would reach the integrator as the code itself.
// Por que: detalhe é um código como outro qualquer, então um membro do vocabulário sem
// frase num dos dicionários chegaria ao integrador como o próprio código.
test("every detalhe of the vocabulary has a phrase in both dictionaries (todo detalhe tem frase nos dois dicionários)", () => {
  for (const idioma of ["pt", "en"]) {
    const caminho = new URL(`./i18n/${idioma}.json`, import.meta.url);
    const textos = JSON.parse(readFileSync(caminho, "utf-8")) as Record<string, string>;
    for (const codigo of DETALHES) {
      const frase = textos[`detalhe_${codigo}`];
      assert.equal(typeof frase, "string", `${idioma}.detalhe_${codigo}`);
      assert.ok(frase.trim().length > 0, `${idioma}.detalhe_${codigo}`);
    }
  }
});

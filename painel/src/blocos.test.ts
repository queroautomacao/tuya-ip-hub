// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { test } from "node:test";

import type { ItemCatalogo } from "./equipamentos.ts";
import {
  CODIGOS_BLOCOS,
  comIdentidade,
  controlesDoBloco,
  gruposPossiveis,
  lerLeituraDeBlocos,
  lerBloco,
  ordemDe,
  podeAgrupar,
  podeOcuparBloco,
  prepararVolume,
  tocando,
  type Bloco,
} from "./blocos.ts";

const DPS = { volume: 101, play: 102, preset: 103, online: 104, tocando: 105, entrada: 141 };

const CAPACIDADES = ["volume", "mudo", "fonte", "tocar", "pausar", "agrupar", "comando_extra"];

function blocoDe(parcial: Partial<Bloco> = {}): Bloco {
  return {
    bloco: 1,
    identidade: "uuid-1",
    nome: "Sala",
    tipo: "multiroom_linkplay",
    papel: "",
    entradas: ["wifi", "line-in"],
    dps: { ...DPS },
    estado: {
      online: true,
      ligado: null,
      volume: 20,
      mudo: null,
      fonte: "wifi",
      fontes: ["wifi", "line-in"],
      tocando: null,
      detalhe: "",
    },
    ...parcial,
  };
}

function vazia(bloco: number): Bloco {
  return blocoDe({
    bloco,
    identidade: "",
    nome: "",
    tipo: "",
    entradas: [],
    estado: null,
    dps: { ...DPS, volume: 101 + 5 * (bloco - 1) },
  });
}

function itemDe(parcial: Partial<ItemCatalogo> = {}): ItemCatalogo {
  return {
    tipo: "multiroom_linkplay",
    categoria: "multiroom",
    motor: "nativo",
    auth: "nenhuma",
    capacidades: [...CAPACIDADES],
    rotulo: { pt: "Caixa", en: "Speaker" },
    textos: { pt: { descricao: "Caixa" }, en: { descricao: "Speaker" } },
    config_campos: [],
    ...parcial,
  };
}

test("lerBloco takes a block and refuses an answer outside the contract (aceita um bloco e recusa resposta fora do contrato)", () => {
  const bruto = {
    bloco: 2,
    identidade: "uuid-2",
    nome: "Cozinha",
    tipo: "multiroom_linkplay",
    papel: "escravo",
    entradas: ["wifi"],
    dps: { volume: 106, play: 107, preset: 108, online: 109, tocando: 110, entrada: 142 },
    estado: null,
  };
  const lida = lerBloco(bruto);
  assert.ok(lida !== null);
  assert.equal(lida.bloco, 2);
  assert.equal(lida.papel, "escravo");
  assert.equal(lida.estado, null);
  assert.equal(lida.dps.entrada, 142);
  // Why: a data point the answer does not carry would leave a button with no number to write
  // to, and the panel would send a set to undefined.
  // Por que: um data point que a resposta não traz deixaria um botão sem número para escrever,
  // e o painel mandaria um set para undefined.
  assert.equal(lerBloco({ ...bruto, dps: { ...bruto.dps, entrada: undefined } }), null);
  assert.equal(lerBloco({ ...bruto, papel: "dono" }), null);
  assert.equal(lerBloco({ ...bruto, entradas: [1] }), null);
  assert.equal(lerBloco({ ...bruto, bloco: "2" }), null);
});

test("lerLeituraDeBlocos needs the group and the data point of it (precisa do grupo e do data point dele)", () => {
  const blocos = [blocoDe()];
  assert.equal(lerLeituraDeBlocos({ blocos, grupo: "solo", dp_grupo: 132 })?.dp_grupo, 132);
  assert.equal(lerLeituraDeBlocos({ blocos, grupo: "solo" }), null);
  assert.equal(lerLeituraDeBlocos({ blocos, grupo: 132, dp_grupo: 132 }), null);
  assert.equal(lerLeituraDeBlocos({ grupo: "solo", dp_grupo: 132 }), null);
});

// Why: a shift would move the speaker of block 2 into block 1 in every automation the customer
// already built on the platform, and nothing on the bus would say it happened.
// Por que: um empurrão moveria a caixa do bloco 2 para o bloco 1 em toda automação que o cliente
// já montou na plataforma, e nada no barramento diria que isso aconteceu.
test("comIdentidade empties a block in place and never shifts the rest (esvazia o bloco no lugar e nunca empurra o resto)", () => {
  const ordem = ["uuid-1", "uuid-2", "uuid-3"];
  assert.deepEqual(comIdentidade(ordem, 1, ""), ["", "uuid-2", "uuid-3"]);
  assert.deepEqual(comIdentidade(ordem, 2, ""), ["uuid-1", "", "uuid-3"]);
  // Why: one speaker in two blocks answers the volume of two blocks on the bus, so moving it
  // takes it off the block it used to occupy.
  // Por que: uma caixa em dois blocos responde o volume de dois blocos no barramento, então
  // movê-la a tira do bloco que ela ocupava.
  assert.deepEqual(comIdentidade(ordem, 1, "uuid-3"), ["uuid-3", "uuid-2", ""]);
  assert.deepEqual(comIdentidade(["", "", ""], 3, "uuid-9"), ["", "", "uuid-9"]);
});

test("ordemDe reads the identities in the order of the blocks (lê as identidades na ordem dos blocos)", () => {
  const blocos = [blocoDe(), vazia(2), blocoDe({ bloco: 3, identidade: "uuid-3" })];
  assert.deepEqual(ordemDe(blocos), ["uuid-1", "", "uuid-3"]);
});

test("podeOcuparBloco takes any equipment whose driver exists (aceita qualquer equipamento cujo driver existe)", () => {
  assert.equal(podeOcuparBloco(itemDe()), true);
  assert.equal(podeOcuparBloco(itemDe({ categoria: "projetor" })), true);
  assert.equal(podeOcuparBloco(itemDe({ capacidades: ["volume", "fonte"] })), true);
  assert.equal(podeOcuparBloco(undefined), false);
});

test("podeAgrupar reads multiroom from the manifest alone (lê multiroom só do manifesto)", () => {
  assert.equal(podeAgrupar(itemDe()), true);
  assert.equal(podeAgrupar(itemDe({ categoria: "projetor" })), false);
  assert.equal(podeAgrupar(itemDe({ capacidades: ["volume", "fonte"] })), false);
  assert.equal(podeAgrupar(undefined), false);
});

// Why: section 8, DP 102 is play/pause for a driver with transport and the power switch for
// any other equipment on the app, so a projector in a block gets a power key and a driver with
// only half of either pair gets nothing.
// Por que: seção 8, o DP 102 é play/pause para um driver com transporte e a chave de ligar para
// qualquer outro equipamento no app, então um projetor num bloco ganha uma tecla de energia e um
// driver com só metade de qualquer par não ganha nada.
test("controlesDoBloco gives DP 102 to power when there is no transport (dá o DP 102 ao ligar quando não há transporte)", () => {
  const play = (capacidades: string[]) =>
    controlesDoBloco(blocoDe(), itemDe({ capacidades })).find((controle) => controle.funcao === "play")?.especie;
  assert.equal(play(["tocar", "pausar", "ligar", "desligar"]), "alternar");
  assert.equal(play(["ligar", "desligar", "fonte"]), "ligar");
  assert.equal(play(["ligar", "fonte"]), undefined);
  assert.equal(play(["tocar"]), undefined);
});

// Why: section 14, a group only ever exists between speakers of the same domain; offering a
// mixed one is what leaves half of it playing and the other half silent.
// Por que: seção 14, um grupo só existe entre caixas do mesmo domínio; oferecer um misto é o
// que deixa metade dele tocando e a outra metade calada.
test("gruposPossiveis never offers a mixed group (nunca oferece um grupo misto)", () => {
  const catalogo = [itemDe(), itemDe({ tipo: "multiroom_de_outra_marca" })];
  const iguais = [blocoDe(), blocoDe({ bloco: 2, identidade: "uuid-2" })];
  assert.deepEqual(gruposPossiveis(iguais, catalogo), ["solo", "grupo1", "grupo2"]);
  const mistas = [blocoDe(), blocoDe({ bloco: 2, identidade: "uuid-2", tipo: "multiroom_de_outra_marca" })];
  assert.deepEqual(gruposPossiveis(mistas, catalogo), ["solo"]);
  // Why: a group of one is not a group, and a screen that offered it would publish a group
  // the customer cannot hear.
  // Por que: um grupo de um não é grupo, e uma tela que o oferecesse publicaria um grupo que o
  // cliente não escuta.
  assert.deepEqual(gruposPossiveis([blocoDe(), vazia(2)], catalogo), ["solo"]);
  assert.deepEqual(gruposPossiveis(iguais, []), ["solo"]);
});

// Why: section 6, a capability the manifest does not declare gets no button, because the
// daemon answers nao_suportado before the driver is touched.
// Por que: seção 6, uma capacidade que o manifesto não declara não ganha botão, porque o
// daemon responde nao_suportado antes de tocar no driver.
test("controlesDoBloco gives presets to multiroom alone (dá presets só ao multiroom)", () => {
  const matriz = itemDe({ categoria: "matriz", capacidades: ["ligar", "desligar", "comando_extra"] });
  assert.ok(!controlesDoBloco(blocoDe(), matriz).some((controle) => controle.funcao === "preset"));
  assert.ok(controlesDoBloco(blocoDe(), itemDe()).some((controle) => controle.funcao === "preset"));
});

test("controlesDoBloco offers only what the manifest declares (oferece só o que o manifesto declara)", () => {
  const todos = controlesDoBloco(blocoDe(), itemDe());
  assert.deepEqual(
    todos.map((controle) => [controle.funcao, controle.dpid, controle.especie]),
    [
      ["volume", 101, "escala"],
      ["play", 102, "alternar"],
      ["preset", 103, "preset"],
      ["entrada", 141, "escolha"],
    ],
  );
  const semTransporte = controlesDoBloco(blocoDe(), itemDe({ capacidades: ["volume", "tocar"] }));
  assert.deepEqual(
    semTransporte.map((controle) => controle.funcao),
    ["volume"],
  );
  const semEntradas = controlesDoBloco(blocoDe({ entradas: [] }), itemDe());
  assert.ok(!semEntradas.some((controle) => controle.funcao === "entrada"));
  assert.deepEqual(controlesDoBloco(vazia(2), itemDe()), []);
  assert.deepEqual(controlesDoBloco(blocoDe(), undefined), []);
});

test("prepararVolume refuses what the data point does not take (recusa o que o data point não aceita)", () => {
  assert.deepEqual(prepararVolume("0"), { ok: true, valor: 0 });
  assert.deepEqual(prepararVolume(" 100 "), { ok: true, valor: 100 });
  for (const bruto of ["101", "-1", "abc", "", "1.5", "300"]) {
    assert.deepEqual(prepararVolume(bruto), { ok: false, codigo: "valor_invalido" }, bruto);
  }
});

// Why: section 14, a slave answers stop even while the group plays, so the daemon mirrors what
// the master plays onto it and the screen reads that.
// Por que: seção 14, um escravo responde stop mesmo com o grupo tocando, então o daemon
// espelha nele o que o mestre toca e a tela lê isso.
test("tocando reads the mirrored state of a slave (lê o estado espelhado de um escravo)", () => {
  const escravo = blocoDe({ papel: "escravo" });
  assert.equal(tocando(escravo), false);
  assert.equal(tocando(blocoDe({ estado: { ...escravo.estado!, tocando: "Musica 1" } })), true);
  assert.equal(tocando(blocoDe({ estado: { ...escravo.estado!, tocando: "" } })), false);
  assert.equal(tocando(vazia(3)), false);
});

test("every code of a block has a phrase in both dictionaries (todo código de bloco tem frase nos dois dicionários)", () => {
  for (const idioma of ["pt", "en"]) {
    const caminho = new URL(`./i18n/${idioma}.json`, import.meta.url);
    const textos = JSON.parse(readFileSync(caminho, "utf-8")) as Record<string, string>;
    for (const codigo of CODIGOS_BLOCOS) {
      const frase = textos[`erro_${codigo}`];
      assert.equal(typeof frase, "string", `${idioma}.erro_${codigo}`);
      assert.ok(frase.trim().length > 0, `${idioma}.erro_${codigo}`);
    }
  }
});

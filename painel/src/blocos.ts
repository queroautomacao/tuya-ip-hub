// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a block is one of the six equipment numbers of the app (section 8), which any
// registered equipment may occupy, and nothing here decides which data point a block carries
// nor what it means: the daemon answers both, so the panel never keeps a second copy of the
// contract. What lives here is what a screen needs from that answer, as pure functions with
// tests.
// Por que: um bloco é um dos seis números de equipamento do app (seção 8), que qualquer
// equipamento cadastrado pode ocupar, e nada aqui decide qual data point um bloco carrega nem
// o que ele significa: o daemon responde os dois, então o painel nunca guarda uma segunda
// cópia do contrato. Aqui mora o que uma tela precisa daquela resposta, como funções puras
// com teste.

import {
  lerEstadoEquipamento,
  lerLista,
  type Capacidade,
  type EstadoEquipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";

export const SOLO = "solo";
export const PREFIXO_GRUPO = "grupo";

// Why: section 6, multiroom is a capability of the equipment, declared by the manifest as the
// category plus agrupar, and the panel reads the same two facts the daemon reads.
// Por que: seção 6, multiroom é capacidade do equipamento, declarada pelo manifesto como a
// categoria mais agrupar, e o painel lê os mesmos dois fatos que o daemon lê.
export const CATEGORIA_DE_GRUPO = "multiroom";
export const CAPACIDADE_DE_GRUPO = "agrupar";

export const PAPEIS = ["", "mestre", "escravo"] as const;
export type Papel = (typeof PAPEIS)[number];

// The functions of a block, in the order section 8 numbers them.
// As funções de um bloco, na ordem em que a seção 8 as numera.
export const FUNCOES_DO_BLOCO = [
  "volume",
  "play",
  "preset",
  "online",
  "tocando",
  "entrada",
] as const;

export type FuncaoDoBloco = (typeof FUNCOES_DO_BLOCO)[number];

// Why: the API answers a stable code and never a phrase, so each one needs an entry in both
// dictionaries, and a test asserts that it has one.
// Por que: a API responde um código estável e nunca uma frase, então cada um precisa de
// entrada nos dois dicionários, e um teste garante isso.
export const CODIGOS_BLOCOS = [
  "blocos_demais",
  "bloco_repetido",
  "identidade_invalida",
  "dp_desconhecido",
  "dp_somente_leitura",
  "valor_invalido",
  "bloco_offline",
] as const;

export type DpsDoBloco = Record<FuncaoDoBloco, number>;

export interface Bloco {
  bloco: number;
  identidade: string;
  nome: string;
  tipo: string;
  papel: Papel;
  entradas: string[];
  dps: DpsDoBloco;
  estado: EstadoEquipamento | null;
}

export interface LeituraDeBlocos {
  blocos: Bloco[];
  grupo: string;
  dp_grupo: number;
}

type Objeto = Record<string, unknown>;

const ehNumero = (valor: unknown): valor is number => typeof valor === "number";
const ehTexto = (valor: unknown): valor is string => typeof valor === "string";

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

function lerPapel(valor: unknown): Papel | null {
  return PAPEIS.find((papel) => papel === valor) ?? null;
}

function lerDps(valor: unknown): DpsDoBloco | null {
  if (!ehObjeto(valor)) return null;
  const dps: Partial<DpsDoBloco> = {};
  for (const funcao of FUNCOES_DO_BLOCO) {
    const dpid = valor[funcao];
    if (!ehNumero(dpid)) return null;
    dps[funcao] = dpid;
  }
  return dps as DpsDoBloco;
}

export function lerBloco(valor: unknown): Bloco | null {
  if (!ehObjeto(valor) || !ehNumero(valor.bloco)) return null;
  const { identidade, nome, tipo } = valor;
  if (!ehTexto(identidade) || !ehTexto(nome) || !ehTexto(tipo)) return null;
  const papel = lerPapel(valor.papel);
  const entradas = lerLista(valor.entradas, (bruto) => (ehTexto(bruto) ? bruto : null));
  const dps = lerDps(valor.dps);
  if (papel === null || entradas === null || dps === null) return null;
  // Why: a block nobody occupies answers a null state, which is not a broken answer: the hub
  // works with zero equipment and the POSITION of the block is the contract.
  // Por que: um bloco que ninguém ocupa responde estado nulo, o que não é resposta quebrada: o
  // hub funciona com zero equipamento e a POSIÇÃO do bloco é o contrato.
  const estado = valor.estado === null ? null : lerEstadoEquipamento(valor.estado);
  if (estado === undefined) return null;
  return { bloco: valor.bloco, identidade, nome, tipo, papel, entradas, dps, estado };
}

export function lerLeituraDeBlocos(dados: Objeto): LeituraDeBlocos | null {
  const blocos = lerLista(dados.blocos, lerBloco);
  if (blocos === null || !ehTexto(dados.grupo) || !ehNumero(dados.dp_grupo)) return null;
  return { blocos, grupo: dados.grupo, dp_grupo: dados.dp_grupo };
}

export function valorDoGrupo(bloco: number): string {
  return `${PREFIXO_GRUPO}${bloco}`;
}

export function ordemDe(blocos: readonly Bloco[]): string[] {
  return blocos.map((bloco) => bloco.identidade);
}

// Why: a shift would move the speaker of block 2 into block 1 in every automation the customer
// already built, so a block is emptied where it is and the identity is only taken off the
// block it used to occupy.
// Por que: um empurrão moveria a caixa do bloco 2 para o bloco 1 em toda automação que o cliente
// já montou, então um bloco é esvaziado no lugar e a identidade só sai do bloco que ela
// ocupava.
export function comIdentidade(
  ordem: readonly string[],
  bloco: number,
  identidade: string,
): string[] {
  return ordem.map((atual, posicao) => {
    if (posicao === bloco - 1) return identidade;
    return identidade !== "" && atual === identidade ? "" : atual;
  });
}

// Why: any equipment whose driver the image knows may take a number on the app; what the
// number does (play/pause or power on DP 102) follows the manifest, on the daemon side.
// Por que: qualquer equipamento cujo driver a imagem conhece pode ter um número no app; o que
// o número faz (play/pause ou ligar no DP 102) segue o manifesto, do lado do daemon.
export function podeOcuparBloco(item: ItemCatalogo | undefined): boolean {
  return item !== undefined;
}

export function podeAgrupar(item: ItemCatalogo | undefined): boolean {
  if (item === undefined) return false;
  return (
    item.categoria === CATEGORIA_DE_GRUPO &&
    item.capacidades.includes(CAPACIDADE_DE_GRUPO as Capacidade)
  );
}

// Why: section 14, a group only ever exists between speakers of the same domain, so a block
// that has nobody of its own tipo to lead is never offered as a group; offering a mixed one
// is what leaves half of it playing and the other half silent.
// Por que: seção 14, um grupo só existe entre caixas do mesmo domínio, então um bloco que não
// tem ninguém do tipo dele para liderar nunca é oferecido como grupo; oferecer um misto é o
// que deixa metade dele tocando e a outra metade calada.
export function gruposPossiveis(blocos: readonly Bloco[], catalogo: readonly ItemCatalogo[]): string[] {
  const item = (tipo: string): ItemCatalogo | undefined =>
    catalogo.find((candidato) => candidato.tipo === tipo);
  const lideres = blocos.filter(
    (bloco) =>
      bloco.identidade !== "" &&
      podeAgrupar(item(bloco.tipo)) &&
      blocos.some(
        (outra) =>
          outra.bloco !== bloco.bloco && outra.identidade !== "" && outra.tipo === bloco.tipo,
      ),
  );
  return [SOLO, ...lideres.map((bloco) => valorDoGrupo(bloco.bloco))];
}

export type EspecieDeControle = "escala" | "alternar" | "ligar" | "escolha" | "preset";

export interface ControleDeBloco {
  funcao: FuncaoDoBloco;
  dpid: number;
  especie: EspecieDeControle;
}

// Why: section 6, a capability the manifest does not declare gets no button, because the
// daemon answers nao_suportado before the driver is touched. The play data point is a toggle
// over the two transport capabilities, or over power for a driver that has no transport
// (section 8), so a driver that declares only one half of a pair gets neither half of the
// button instead of a button that fails every other press.
// Por que: seção 6, uma capacidade que o manifesto não declara não ganha botão, porque o
// daemon responde nao_suportado antes de tocar no driver. O data point de play é uma chave
// sobre as duas capacidades de transporte, ou sobre ligar/desligar num driver sem transporte
// (seção 8), então um driver que declara só metade de um par não ganha metade nenhuma do
// botão em vez de um botão que falha a cada duas apertadas.
const ENERGIA: Capacidade[] = ["ligar", "desligar"];

const EXIGIDAS: Record<FuncaoDoBloco, Capacidade[]> = {
  volume: ["volume"],
  play: ["tocar", "pausar"],
  preset: ["comando_extra"],
  entrada: ["fonte"],
  online: [],
  tocando: [],
};

const ESPECIES: Partial<Record<FuncaoDoBloco, EspecieDeControle>> = {
  volume: "escala",
  play: "alternar",
  preset: "preset",
  entrada: "escolha",
};

export function controlesDoBloco(
  bloco: Bloco,
  item: ItemCatalogo | undefined,
): ControleDeBloco[] {
  if (bloco.identidade === "" || item === undefined) return [];
  const controles: ControleDeBloco[] = [];
  for (const funcao of FUNCOES_DO_BLOCO) {
    const especie = especieDe(funcao, item);
    if (especie === undefined) continue;
    if (funcao === "entrada" && bloco.entradas.length === 0) continue;
    controles.push({ funcao, dpid: bloco.dps[funcao], especie });
  }
  return controles;
}

function especieDe(funcao: FuncaoDoBloco, item: ItemCatalogo): EspecieDeControle | undefined {
  const tem = (lista: Capacidade[]): boolean =>
    lista.length > 0 && lista.every((capacidade) => item.capacidades.includes(capacidade));
  if (funcao === "play") {
    if (tem(EXIGIDAS.play)) return "alternar";
    return tem(ENERGIA) ? "ligar" : undefined;
  }
  // Why: a preset is "play the configured URL N", the vocabulary of a multiroom driver
  // (section 14), so a matrix with comando_extra gets no preset keys; the daemon refuses the
  // data point for it with nao_suportado, and the panel offers the same set.
  // Por que: um preset é "toca a URL configurada N", vocabulário de um driver multiroom
  // (seção 14), então uma matriz com comando_extra não ganha teclas de preset; o daemon
  // recusa o data point para ela com nao_suportado, e o painel oferece o mesmo conjunto.
  if (funcao === "preset" && !podeAgrupar(item)) return undefined;
  const especie = ESPECIES[funcao];
  return especie !== undefined && tem(EXIGIDAS[funcao]) ? especie : undefined;
}

export type Preparo = { ok: true; valor: unknown } | { ok: false; codigo: string };

// Why: a volume of 300 is refused here with the same stable code the daemon would answer, so
// a typo costs no request; the daemon still judges it, because the panel is not the authority.
// Por que: um volume de 300 é recusado aqui com o mesmo código estável que o daemon
// responderia, então um erro de digitação não custa requisição; o daemon ainda julga, porque o
// painel não é a autoridade.
export function prepararVolume(entrada: string): Preparo {
  const limpo = entrada.trim();
  const dentro = /^\d{1,3}$/.test(limpo) && Number(limpo) <= 100;
  return dentro ? { ok: true, valor: Number(limpo) } : { ok: false, codigo: "valor_invalido" };
}

export function tocando(bloco: Bloco): boolean {
  // Why: section 14, a slave answers stop even while the group plays, so the daemon mirrors
  // what the master plays onto it and the screen reads that and never the slave itself.
  // Por que: seção 14, um escravo responde stop mesmo com o grupo tocando, então o daemon
  // espelha nele o que o mestre toca e a tela lê isso, e nunca o próprio escravo.
  return bloco.estado !== null && bloco.estado.tocando !== null && bloco.estado.tocando !== "";
}

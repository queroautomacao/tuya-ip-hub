// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a scene is DATA, a list of steps that each set one data point, so the panel is an
// editor of that data and never an interpreter of it: which data points may be set, what each
// one takes and how many steps fit come from the daemon, and the daemon is the authority that
// accepts or refuses the list, field by field.
// Por que: uma cena é DADO, uma lista de passos que ajustam um data point cada, então o painel
// é um editor daquele dado e nunca um interpretador dele: quais data points podem ser
// ajustados, o que cada um aceita e quantos passos cabem vêm do daemon, e o daemon é a
// autoridade que aceita ou recusa a lista, campo a campo.

import { lerLista } from "./equipamentos.ts";
import type { Preparo } from "./blocos.ts";

export const FUNCAO_DA_CENA = "cena";

export const TIPOS_DE_DP = ["value", "bool", "enum", "string"] as const;
export type TipoDeDp = (typeof TIPOS_DE_DP)[number];

export const SENTIDOS = ["rw", "envio", "reporte"] as const;
export type Sentido = (typeof SENTIDOS)[number];

// Why: the API answers a stable code and never a phrase, so each one needs an entry in both
// dictionaries, and a test asserts that it has one. The refusal of a list carries one code per
// FIELD, the way a driver file is refused in section 7, so all of them are here.
// Por que: a API responde um código estável e nunca uma frase, então cada um precisa de
// entrada nos dois dicionários, e um teste garante isso. A recusa de uma lista carrega um
// código por CAMPO, como um arquivo de driver é recusado na seção 7, então todos estão aqui.
export const CODIGOS_CENAS = [
  "cenas_invalidas",
  "cena_nao_encontrada",
  "cena_em_curso",
  "cenas_nao_lista",
  "cenas_demais",
  "cena_nao_objeto",
  "cena_chave_desconhecida",
  "cena_nome_invalido",
  "cena_passos_invalidos",
  "cena_passos_demais",
  "cena_passo_nao_objeto",
  "cena_dp_desconhecido",
  "cena_dp_somente_leitura",
  "cena_dp_proibido",
  "cena_valor_invalido",
  "cena_espera_invalida",
  "cena_intervalo_invalido",
  "nomes_demais",
  "nomes_longos",
  "nome_nao_gravavel",
] as const;

export const NOME_MAXIMO = 40;
export const VALOR_TEXTO_MAXIMO = 64;

// Why: a step with no wait of its own (null) sleeps the interval of the scene, so the screen
// shows the interval as the placeholder of the wait and never writes it into the step.
// Por que: um passo sem espera própria (null) dorme o intervalo da cena, então a tela mostra o
// intervalo como placeholder da espera e nunca o escreve no passo.
export interface PassoDeCena {
  dpid: number;
  valor: unknown;
  espera_ms: number | null;
}

export interface Cena {
  numero: number;
  nome: string;
  intervalo_ms: number;
  em_curso: boolean;
  passos: PassoDeCena[];
}

export interface LeituraDeCenas {
  cenas: Cena[];
  maximo: number;
  passos_maximos: number;
  espera_maxima_ms: number;
  intervalo_padrao_ms: number;
}

export interface ItemDoMapa {
  dpid: number;
  bloco: number;
  funcao: string;
  tipo: TipoDeDp;
  sentido: Sentido;
  valores: string[];
}

export interface Snapshot {
  dps: Record<string, unknown>;
  mapa: ItemDoMapa[];
}

type Objeto = Record<string, unknown>;

const ehNumero = (valor: unknown): valor is number => typeof valor === "number";
const ehTexto = (valor: unknown): valor is string => typeof valor === "string";

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

export function lerPasso(valor: unknown): PassoDeCena | null {
  if (!ehObjeto(valor) || !ehNumero(valor.dpid) || !("valor" in valor)) return null;
  const espera = valor.espera_ms ?? null;
  if (espera !== null && !ehNumero(espera)) return null;
  return { dpid: valor.dpid, valor: valor.valor, espera_ms: espera };
}

export function lerCena(valor: unknown): Cena | null {
  if (!ehObjeto(valor) || !ehNumero(valor.numero) || !ehTexto(valor.nome)) return null;
  if (!ehNumero(valor.intervalo_ms)) return null;
  const passos = lerLista(valor.passos, lerPasso);
  if (passos === null) return null;
  const { numero, nome, intervalo_ms } = valor;
  return { numero, nome, intervalo_ms, em_curso: valor.em_curso === true, passos };
}

export function lerLeituraDeCenas(dados: Objeto): LeituraDeCenas | null {
  const cenas = lerLista(dados.cenas, lerCena);
  const { maximo, passos_maximos, espera_maxima_ms, intervalo_padrao_ms } = dados;
  if (cenas === null || !ehNumero(maximo)) return null;
  if (!ehNumero(passos_maximos) || !ehNumero(espera_maxima_ms)) return null;
  if (!ehNumero(intervalo_padrao_ms)) return null;
  return { cenas, maximo, passos_maximos, espera_maxima_ms, intervalo_padrao_ms };
}

export function lerItemDoMapa(valor: unknown): ItemDoMapa | null {
  if (!ehObjeto(valor) || !ehNumero(valor.dpid) || !ehNumero(valor.bloco)) return null;
  if (!ehTexto(valor.funcao)) return null;
  const tipo = TIPOS_DE_DP.find((candidato) => candidato === valor.tipo);
  const sentido = SENTIDOS.find((candidato) => candidato === valor.sentido);
  const valores = lerLista(valor.valores, (bruto) => (ehTexto(bruto) ? bruto : null));
  if (tipo === undefined || sentido === undefined || valores === null) return null;
  return { dpid: valor.dpid, bloco: valor.bloco, funcao: valor.funcao, tipo, sentido, valores };
}

export function lerSnapshot(dados: Objeto): Snapshot | null {
  const mapa = lerLista(dados.mapa, lerItemDoMapa);
  if (mapa === null || !ehObjeto(dados.dps)) return null;
  return { dps: dados.dps, mapa };
}

// Why: section 8, a report is only ever born of real state, so a step never writes one; and a
// scene that started a scene would be a loop written in data, which the daemon refuses too.
// Por que: seção 8, um report só nasce de estado real, então um passo nunca escreve um; e uma
// cena que disparasse uma cena seria um laço escrito em dado, que o daemon também recusa.
export function ajustaveis(mapa: readonly ItemDoMapa[]): ItemDoMapa[] {
  return mapa.filter((item) => item.sentido !== "reporte" && item.funcao !== FUNCAO_DA_CENA);
}

export function itemDoDp(mapa: readonly ItemDoMapa[], dpid: number): ItemDoMapa | undefined {
  return mapa.find((item) => item.dpid === dpid);
}

export function valorPadrao(item: ItemDoMapa): unknown {
  if (item.tipo === "value") return 0;
  if (item.tipo === "bool") return true;
  return item.valores[0] ?? "";
}

export function textoDoValor(valor: unknown): string {
  if (typeof valor === "boolean") return valor ? "true" : "false";
  if (valor === null || valor === undefined) return "";
  return String(valor);
}

// Why: the refusal codes are the ones the daemon answers, so a value the panel refuses and a
// value the daemon refuses read the same on the screen; the daemon still judges the list.
// Por que: os códigos de recusa são os que o daemon responde, então um valor que o painel
// recusa e um que o daemon recusa se leem igual na tela; o daemon ainda julga a lista.
export function prepararValor(item: ItemDoMapa, entrada: string): Preparo {
  const limpo = entrada.trim();
  if (item.tipo === "value") {
    const dentro = /^\d{1,3}$/.test(limpo) && Number(limpo) <= 100;
    return dentro ? { ok: true, valor: Number(limpo) } : { ok: false, codigo: "cena_valor_invalido" };
  }
  if (item.tipo === "bool") {
    if (limpo !== "true" && limpo !== "false") return { ok: false, codigo: "cena_valor_invalido" };
    return { ok: true, valor: limpo === "true" };
  }
  if (item.tipo === "enum") {
    if (item.valores.length > 0) {
      return item.valores.includes(limpo)
        ? { ok: true, valor: limpo }
        : { ok: false, codigo: "cena_valor_invalido" };
    }
    // Why: the inputs of a speaker come from the hardware (section 14, plm_support), so a
    // speaker that was offline when the scene was saved offers no list; the shape is checked
    // here and the value the speaker does not have is refused by the bus when the step runs.
    // Por que: as entradas de uma caixa vêm do hardware (seção 14, plm_support), então uma
    // caixa offline na hora de salvar não oferece lista; a forma é conferida aqui e o valor que
    // a caixa não tem é recusado pelo barramento quando o passo roda.
    const cabe = limpo.length > 0 && limpo.length <= VALOR_TEXTO_MAXIMO;
    return cabe ? { ok: true, valor: limpo } : { ok: false, codigo: "cena_valor_invalido" };
  }
  return { ok: false, codigo: "cena_dp_somente_leitura" };
}

function milissegundos(limpo: string, maximo: number): number | null {
  const dentro = /^\d{1,6}$/.test(limpo) && Number(limpo) <= maximo;
  return dentro ? Number(limpo) : null;
}

// Why: an empty wait is not zero, it is "the interval of the scene", so it is kept as null and
// the daemon sleeps the interval; a zero typed on purpose is an order to wait nothing.
// Por que: uma espera vazia não é zero, é "o intervalo da cena", então ela fica como null e o
// daemon dorme o intervalo; um zero digitado de propósito é ordem de não esperar nada.
export function prepararEspera(entrada: string, maximo: number): Preparo {
  const limpo = entrada.trim();
  if (limpo === "") return { ok: true, valor: null };
  const valor = milissegundos(limpo, maximo);
  return valor === null ? { ok: false, codigo: "cena_espera_invalida" } : { ok: true, valor };
}

export function prepararIntervalo(entrada: string, maximo: number): Preparo {
  const valor = milissegundos(entrada.trim(), maximo);
  return valor === null ? { ok: false, codigo: "cena_intervalo_invalido" } : { ok: true, valor };
}

// Why: the daemon measures the name in code points, the way python len does, and
// String.length measures UTF-16 units, so an astral character counts twice here and once
// there; the panel would accept a name the daemon then refuses. A control character is
// refused where it is typed, because it travels to the bridge inside the JSON of DP 134.
// Por que: o daemon mede o nome em pontos de codigo, como o len do python, e o
// String.length mede unidades UTF-16, entao um caractere astral conta duas vezes aqui e uma
// la; o painel aceitaria um nome que o daemon depois recusa. Um caractere de controle e
// recusado onde ele e digitado, porque ele viaja para a ponte dentro do JSON do DP 134.
export function nomeValido(nome: string): boolean {
  const controle = /[\u0000-\u001f\u007f]/;
  return [...nome].length <= NOME_MAXIMO && !controle.test(nome);
}

// Why: the POSITION of a scene is its number, so what is saved is the whole list and an erased
// scene keeps its slot; a list that came back shorter would move scene 3 into slot 2 in every
// automation the customer already built on the platform.
// Por que: a POSIÇÃO de uma cena é o número dela, então o que se salva é a lista inteira e uma
// cena apagada mantém a vaga; uma lista que voltasse mais curta moveria a cena 3 para a vaga 2
// em toda automação que o cliente já montou na plataforma.
export interface CorpoDeCena {
  nome: string;
  intervalo_ms: number;
  passos: PassoDeCena[];
}

export function corpoDeCenas(cenas: readonly Cena[]): CorpoDeCena[] {
  return cenas.map((cena) => ({
    nome: cena.nome,
    intervalo_ms: cena.intervalo_ms,
    passos: cena.passos.map((passo) => ({
      dpid: passo.dpid,
      valor: passo.valor,
      espera_ms: passo.espera_ms,
    })),
  }));
}

export function cenaVazia(numero: number, intervalo_ms: number): Cena {
  return { numero, nome: "", intervalo_ms, em_curso: false, passos: [] };
}

export function comCenas(cenas: readonly Cena[], maximo: number, intervalo_ms: number): Cena[] {
  // Why: the eight slots are always on the screen, because a slot with no step is a number
  // held open for the automations already built on it and not a scene that is missing.
  // Por que: as oito vagas estão sempre na tela, porque uma vaga sem passo é um número guardado
  // para as automações já montadas nela e não uma cena que falta.
  return Array.from({ length: maximo }, (_ignorado, indice) => cenas[indice] ?? cenaVazia(indice + 1, intervalo_ms));
}

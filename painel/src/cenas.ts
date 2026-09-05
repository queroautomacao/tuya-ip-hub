// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a scene is DATA, a list of steps that each run one action on one equipment, so the panel
// is an editor of that data and never an interpreter of it: which actions exist, what each one
// takes and how many steps fit come from the daemon, and the daemon is the authority that
// accepts or refuses the list, field by field.
// Por que: uma cena é DADO, uma lista de passos que rodam uma ação num equipamento cada, então o
// painel é um editor daquele dado e nunca um interpretador dele: quais ações existem, o que cada
// uma aceita e quantos passos cabem vêm do daemon, e o daemon é a autoridade que aceita ou recusa
// a lista, campo a campo.

import {
  MODOS_AR,
  TECLAS,
  TEMPERATURA_MAXIMA,
  TEMPERATURA_MINIMA,
  VENTOS,
  itensDe,
  lerLista,
  type Equipamento,
  type ItemCatalogo,
} from "./equipamentos.ts";
import { podeAgrupar, type Preparo } from "./licencas.ts";

export const FUNCAO_DA_CENA = "cena";
export const ACAO_GRUPO = "grupo";

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
  "cena_equipamento_invalido",
  "cena_equipamento_desconhecido",
  "cena_acao_desconhecida",
  "cena_valor_invalido",
  "cena_espera_invalida",
  "cena_intervalo_invalido",
  "nomes_demais",
  "nomes_longos",
  "nome_nao_gravavel",
] as const;

export const NOME_MAXIMO = 40;
export const VALOR_TEXTO_MAXIMO = 64;
const IDENTIDADE_MAXIMA = 200;

// The actions that take no value, and the ones that take a free text, section 6.
// As ações que não recebem valor, e as que recebem um texto livre, seção 6.
export const SEM_VALOR = ["ligar", "desligar", "tocar", "pausar", "proxima", "anterior"] as const;
export const COM_TEXTO = ["fonte", "atalho", "modo", "comando_extra"] as const;

const CONTROLE = /[\u0000-\u001f\u007f]/u;

// Why: a step with no wait of its own (null) sleeps the interval of the scene, so the screen
// shows the interval as the placeholder of the wait and never writes it into the step.
// Por que: um passo sem espera própria (null) dorme o intervalo da cena, então a tela mostra o
// intervalo como placeholder da espera e nunca o escreve no passo.
export interface PassoDeCena {
  equipamento: string;
  acao: string;
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
  acoes: string[];
  passos_maximos: number;
  espera_maxima_ms: number;
  intervalo_padrao_ms: number;
}

type Objeto = Record<string, unknown>;

const ehNumero = (valor: unknown): valor is number => typeof valor === "number";
const ehTexto = (valor: unknown): valor is string => typeof valor === "string";

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

export function lerPasso(valor: unknown): PassoDeCena | null {
  if (!ehObjeto(valor) || !ehTexto(valor.equipamento) || !ehTexto(valor.acao)) return null;
  const espera = valor.espera_ms ?? null;
  if (espera !== null && !ehNumero(espera)) return null;
  const lido = "valor" in valor ? valor.valor : null;
  return { equipamento: valor.equipamento, acao: valor.acao, valor: lido, espera_ms: espera };
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
  const acoes = lerLista(dados.acoes, (bruto) => (ehTexto(bruto) ? bruto : null));
  const { maximo, passos_maximos, espera_maxima_ms, intervalo_padrao_ms } = dados;
  if (cenas === null || acoes === null || !ehNumero(maximo)) return null;
  if (!ehNumero(passos_maximos) || !ehNumero(espera_maxima_ms)) return null;
  if (!ehNumero(intervalo_padrao_ms)) return null;
  return { cenas, maximo, acoes, passos_maximos, espera_maxima_ms, intervalo_padrao_ms };
}

// Why: section 6, a capability the manifest does not declare gets no step, because the daemon
// answers nao_suportado before the driver is touched; the group is the one action that is not
// a capability, offered to an equipment that can group (section 8).
// Por que: seção 6, uma capacidade que o manifesto não declara não ganha passo, porque o daemon
// responde nao_suportado antes de tocar no driver; o grupo é a única ação que não é capacidade,
// oferecida a um equipamento que sabe agrupar (seção 8).
export function acoesDe(acoes: readonly string[], item: ItemCatalogo | undefined): string[] {
  if (item === undefined) return [];
  return acoes.filter((acao) =>
    acao === ACAO_GRUPO ? podeAgrupar(item) : item.capacidades.includes(acao),
  );
}

export type EspecieDeValor = "nenhum" | "numero" | "logico" | "escolha" | "texto" | "grupo";

export function especieDe(acao: string): EspecieDeValor {
  if ((SEM_VALOR as readonly string[]).includes(acao)) return "nenhum";
  if (acao === "volume" || acao === "temperatura") return "numero";
  if (acao === "mudo") return "logico";
  if (acao === ACAO_GRUPO) return "grupo";
  if (["tecla", "vento", "modo", "fonte", "atalho"].includes(acao)) return "escolha";
  return "texto";
}

export interface Opcao {
  valor: string;
  rotulo: string;
}

// Why: what a choice offers comes from the registration lists of section 8 for an equipment of
// audio and video, from the vocabulary of the manifest for an air conditioner and for the keys,
// and from the inputs the driver read for an equipment with no list; the value written in the
// step is always the value the driver takes.
// Por que: o que uma escolha oferece vem das listas do cadastro da seção 8 para um equipamento de
// áudio e vídeo, do vocabulário do manifesto para um ar condicionado e para as teclas, e das
// entradas que o driver leu para um equipamento sem lista; o valor escrito no passo é sempre o
// valor que o driver recebe.
export function opcoesDe(
  acao: string,
  item: ItemCatalogo | undefined,
  equipamento: Equipamento | undefined,
): Opcao[] {
  const daLista = (itens: { rotulo: string; valor: string }[]): Opcao[] =>
    itens.map((entrada) => ({ valor: entrada.valor, rotulo: entrada.rotulo }));
  const doVocabulario = (palavras: readonly string[]): Opcao[] =>
    palavras.map((palavra) => ({ valor: palavra, rotulo: palavra }));
  if (acao === "tecla") return doVocabulario(item?.teclas ?? []);
  if (acao === "vento") return doVocabulario(item?.ventos ?? []);
  if (acao === "modo") {
    if (item?.produto === "ar") return doVocabulario(item.modos);
    return equipamento === undefined ? [] : daLista(itensDe(equipamento, "modos"));
  }
  if (acao === "atalho") {
    return equipamento === undefined ? [] : daLista(itensDe(equipamento, "atalhos"));
  }
  if (acao === "fonte") {
    if (equipamento === undefined) return [];
    const entradas = itensDe(equipamento, "entradas");
    if (entradas.length > 0) return daLista(entradas);
    return doVocabulario(equipamento.estado.fontes);
  }
  return [];
}

export function valorPadrao(acao: string, opcoes: readonly Opcao[]): unknown {
  const especie = especieDe(acao);
  if (especie === "nenhum") return null;
  if (acao === "volume") return 0;
  if (acao === "temperatura") return 22;
  if (especie === "logico") return true;
  if (especie === "grupo") return "";
  return opcoes[0]?.valor ?? "";
}

export function textoDoValor(valor: unknown): string {
  if (typeof valor === "boolean") return valor ? "true" : "false";
  if (valor === null || valor === undefined) return "";
  return String(valor);
}

function textoCurto(limpo: string, maximo: number): Preparo {
  const cabe = limpo.length > 0 && [...limpo].length <= maximo && !CONTROLE.test(limpo);
  return cabe ? { ok: true, valor: limpo } : { ok: false, codigo: "cena_valor_invalido" };
}

// Why: the refusal codes are the ones the daemon answers, so a value the panel refuses and a
// value the daemon refuses read the same on the screen; the daemon still judges the list.
// Por que: os códigos de recusa são os que o daemon responde, então um valor que o painel
// recusa e um que o daemon recusa se leem igual na tela; o daemon ainda julga a lista.
export function prepararValor(acao: string, entrada: string): Preparo {
  const limpo = entrada.trim();
  const recusa: Preparo = { ok: false, codigo: "cena_valor_invalido" };
  const especie = especieDe(acao);
  if (especie === "nenhum") return { ok: true, valor: null };
  if (acao === "volume") {
    const dentro = /^\d{1,3}$/.test(limpo) && Number(limpo) <= 100;
    return dentro ? { ok: true, valor: Number(limpo) } : recusa;
  }
  if (acao === "temperatura") {
    const numero = Number(limpo);
    const dentro =
      /^\d{1,2}$/.test(limpo) && numero >= TEMPERATURA_MINIMA && numero <= TEMPERATURA_MAXIMA;
    return dentro ? { ok: true, valor: numero } : recusa;
  }
  if (especie === "logico") {
    if (limpo !== "true" && limpo !== "false") return recusa;
    return { ok: true, valor: limpo === "true" };
  }
  if (acao === "tecla") {
    return (TECLAS as readonly string[]).includes(limpo) ? { ok: true, valor: limpo } : recusa;
  }
  if (acao === "vento") {
    return (VENTOS as readonly string[]).includes(limpo) ? { ok: true, valor: limpo } : recusa;
  }
  // Why: the empty value of the group is solo, so a scene can take a group down by name.
  // Por que: o valor vazio do grupo é solo, então uma cena consegue desfazer um grupo pelo nome.
  if (especie === "grupo") {
    return limpo === "" ? { ok: true, valor: "" } : textoCurto(limpo, IDENTIDADE_MAXIMA);
  }
  if (acao === "modo" && (MODOS_AR as readonly string[]).includes(limpo)) {
    return { ok: true, valor: limpo };
  }
  return textoCurto(limpo, VALOR_TEXTO_MAXIMO);
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
// refused where it is typed, because it travels to the bridge inside the JSON of the names.
// Por que: o daemon mede o nome em pontos de codigo, como o len do python, e o
// String.length mede unidades UTF-16, entao um caractere astral conta duas vezes aqui e uma
// la; o painel aceitaria um nome que o daemon depois recusa. Um caractere de controle e
// recusado onde ele e digitado, porque ele viaja para a ponte dentro do JSON dos nomes.
export function nomeValido(nome: string): boolean {
  return [...nome].length <= NOME_MAXIMO && !CONTROLE.test(nome);
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
      equipamento: passo.equipamento,
      acao: passo.acao,
      valor: passo.valor,
      espera_ms: passo.espera_ms,
    })),
  }));
}

export function cenaVazia(numero: number, intervalo_ms: number): Cena {
  return { numero, nome: "", intervalo_ms, em_curso: false, passos: [] };
}

export function comCenas(cenas: readonly Cena[], maximo: number, intervalo_ms: number): Cena[] {
  // Why: every slot is always on the screen, because a slot with no step is a number held open
  // for the automations already built on it and not a scene that is missing.
  // Por que: toda vaga está sempre na tela, porque uma vaga sem passo é um número guardado para
  // as automações já montadas nela e não uma cena que falta.
  return Array.from(
    { length: maximo },
    (_ignorado, indice) => cenas[indice] ?? cenaVazia(indice + 1, intervalo_ms),
  );
}

// Why: the scenes with steps come first on the app of the customer, and an erased slot after
// the last one with steps is noise on a screen of thirty two.
// Por que: as cenas com passos vêm primeiro no app do cliente, e uma vaga apagada depois da
// última com passos é ruído numa tela de trinta e duas.
export function ultimaEmUso(cenas: readonly Cena[]): number {
  let ultima = 0;
  for (const cena of cenas) {
    if (cena.passos.length > 0 || cena.nome !== "") ultima = cena.numero;
  }
  return ultima;
}

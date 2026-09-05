// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: section 7, the JSON driver is data and the daemon is the authority that accepts it,
// so this module reads the answers of that authority and decides what the screen may
// offer; the file itself is never interpreted here, and no rule of the format is copied.
// Por que: seção 7, o driver JSON é dado e o daemon é a autoridade que o aceita, então
// este módulo lê as respostas dessa autoridade e decide o que a tela pode oferecer; o
// arquivo em si nunca é interpretado aqui, e nenhuma regra do formato é copiada.

import { lerItemCatalogo, type ItemCatalogo } from "./equipamentos.ts";
import { imprimivel } from "./formulario.ts";

export const TRANSPORTES = ["tcp", "http", "udp"] as const;

export type Transporte = (typeof TRANSPORTES)[number];

export const ORIGENS = ["imagem", "integrador"] as const;

export type Origem = (typeof ORIGENS)[number];

// Why: the daemon answers a stable code and never a phrase, so every code this screen can
// receive needs an entry in both dictionaries, and a test asserts that it has one. The
// list mirrors the vocabulary of the validation plus what the routes add; the first two
// are decided here, before any request, and belong to the same vocabulary.
// Por que: o daemon responde um código estável e nunca uma frase, então todo código que
// esta tela pode receber precisa de entrada nos dois dicionários, e um teste garante isso.
// A lista espelha o vocabulário da validação mais o que as rotas acrescentam; os dois
// primeiros são decididos aqui, antes de qualquer requisição, e são do mesmo vocabulário.
export const CODIGOS_DECLARATIVOS = [
  "decl_json_invalido",
  "decl_arquivo_grande",
  "decl_invalido",
  "decl_tipo_ocupado",
  "decl_em_uso",
  "decl_nao_encontrado",
  "decl_nao_objeto",
  "decl_chave_desconhecida",
  "decl_manifesto_invalido",
  "decl_tipo_invalido",
  "decl_rotulo_invalido",
  "decl_categoria_invalida",
  "decl_capacidade_desconhecida",
  "decl_vocabulario_invalido",
  "decl_auth_invalida",
  "decl_config_campo_invalido",
  "decl_textos_invalidos",
  "decl_descoberta_invalida",
  "decl_transporte_invalido",
  "decl_porta_invalida",
  "decl_timeout_invalido",
  "decl_intervalo_invalido",
  "decl_terminador_invalido",
  "decl_base_invalida",
  "decl_metodo_invalido",
  "decl_cabecalho_invalido",
  "decl_comando_invalido",
  "decl_comando_vazio",
  "decl_valores_invalido",
  "decl_repete_invalido",
  "decl_hex_invalido",
  "decl_estado_invalido",
  "decl_leitura_invalida",
  "decl_leitura_vazia",
  "decl_campo_desconhecido",
  "decl_regex_invalida",
  "decl_regex_sem_grupo",
  "decl_regex_perigosa",
  "decl_escala_invalida",
  "decl_texto_nao_gravavel",
] as const;

// Why: a driver file is a text someone types, and a paste of a megabyte is not a driver;
// refusing it here costs no request and keeps the daemon body ceiling out of the screen.
// Por que: um arquivo de driver é texto que alguém digita, e uma colagem de um megabyte
// não é um driver; recusar aqui não custa requisição e mantém o teto de corpo do daemon
// fora da tela.
export const LIMITE_ARQUIVO = 64 * 1024;

// Why: the tipo is the name of the file the daemon writes under the drivers directory, so
// a separator or a dot in it is an attempt to write somewhere else; the alphabet and the
// ceiling are the ones the daemon fixes, and refusing it here means the attempt never
// leaves the browser and no valid tipo is refused twice.
// Por que: o tipo é o nome do arquivo que o daemon grava na pasta de drivers, então um
// separador ou um ponto nele é tentativa de gravar em outro lugar; o alfabeto e o teto são
// os que o daemon fixa, e recusar aqui faz a tentativa nunca sair do navegador sem recusar
// duas vezes um tipo válido.
const TIPO = /^[a-z0-9_]{1,32}$/;

// Why: the campo of a problem is a path the daemon echoed from the file the integrator
// wrote, so it is text nobody on this side chose; it is printed within a ceiling.
// Por que: o campo de um problema é um caminho que o daemon ecoou do arquivo que o
// integrador escreveu, então é texto que ninguém deste lado escolheu; sai dentro de teto.
export const LIMITE_CAMPO = 60;

// The whole file, for a problem that belongs to no field of it.
// O arquivo inteiro, para um problema que não é de nenhum campo dele.
export const CAMPO_ARQUIVO = "";

// Why: the daemon names the file itself when a problem is of no field of it, and that name
// is a word of one language; the screen has it in both, so it is translated like any other
// part of an answer instead of reaching an English reader as it left the daemon.
// Por que: o daemon nomeia o próprio arquivo quando um problema não é de nenhum campo dele,
// e esse nome é palavra de um idioma; a tela o tem nos dois, então ele é traduzido como
// qualquer outra parte de uma resposta em vez de chegar a um leitor em inglês como saiu.
const CAMPOS_DO_ARQUIVO: readonly string[] = [CAMPO_ARQUIVO, "arquivo"];

export function ehCampoDoArquivo(campo: string): boolean {
  return CAMPOS_DO_ARQUIVO.includes(campo);
}

export interface Problema {
  campo: string;
  codigo: string;
}

export interface DriverDeclarativo {
  tipo: string;
  origem: Origem;
  em_uso: boolean;
  manifesto: ItemCatalogo;
}

export interface Arquivo {
  dados: Record<string, unknown>;
  tipo: string;
}

export type Analise = { ok: true; arquivo: Arquivo } | { ok: false; problemas: Problema[] };

export interface Grupo {
  campo: string;
  codigos: string[];
}

export type OfertaApagar = "pode" | "da_imagem" | "em_uso";

export type AvisoSalvar = "substitui_imagem" | "substitui_integrador" | null;

type Objeto = Record<string, unknown>;

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

function ehOrigem(valor: unknown): valor is Origem {
  return (ORIGENS as readonly unknown[]).includes(valor);
}

export function lerProblema(valor: unknown): Problema | null {
  if (!ehObjeto(valor) || typeof valor.codigo !== "string" || !valor.codigo) return null;
  const campo = valor.campo === undefined ? CAMPO_ARQUIVO : valor.campo;
  if (typeof campo !== "string") return null;
  return { campo, codigo: valor.codigo };
}

export function lerDriverDeclarativo(valor: unknown): DriverDeclarativo | null {
  if (!ehObjeto(valor) || typeof valor.em_uso !== "boolean" || !ehOrigem(valor.origem)) return null;
  const manifesto = lerItemCatalogo(valor.manifesto);
  if (manifesto === null) return null;
  // Why: the tipo is the key of the driver everywhere, and an entry whose two copies of it
  // disagree is an answer this panel cannot address a delete to.
  // Por que: o tipo é a chave do driver em todo lugar, e uma entrada cujas duas cópias
  // dele discordam é uma resposta a que este painel não sabe endereçar uma remoção.
  if (valor.tipo !== undefined && valor.tipo !== manifesto.tipo) return null;
  return { tipo: manifesto.tipo, origem: valor.origem, em_uso: valor.em_uso, manifesto };
}

export function lerModelo(valor: unknown): Objeto | null {
  return ehObjeto(valor) ? valor : null;
}

function recusar(campo: string, codigo: string): Analise {
  return { ok: false, problemas: [{ campo, codigo }] };
}

export function analisar(texto: string): Analise {
  if (texto.length > LIMITE_ARQUIVO) return recusar(CAMPO_ARQUIVO, "decl_arquivo_grande");
  let lido: unknown;
  try {
    lido = JSON.parse(texto);
  } catch {
    return recusar(CAMPO_ARQUIVO, "decl_json_invalido");
  }
  if (!ehObjeto(lido)) return recusar(CAMPO_ARQUIVO, "decl_json_invalido");
  const manifesto = lido.manifesto;
  const tipo = ehObjeto(manifesto) && typeof manifesto.tipo === "string" ? manifesto.tipo : "";
  if (!TIPO.test(tipo)) return recusar("manifesto.tipo", "decl_tipo_invalido");
  return { ok: true, arquivo: { dados: lido, tipo } };
}

// Why: section 7 promises every problem of a file at once, so the screen shows one line per
// field instead of sending the integrator back for another round trip per mistake.
// Por que: a seção 7 promete todos os problemas de um arquivo de uma vez, então a tela
// mostra uma linha por campo em vez de mandar o integrador a outra ida e volta por erro.
export function agruparProblemas(problemas: readonly Problema[]): Grupo[] {
  const grupos: Grupo[] = [];
  for (const problema of problemas) {
    const grupo = grupos.find((candidato) => candidato.campo === problema.campo);
    if (grupo === undefined) grupos.push({ campo: problema.campo, codigos: [problema.codigo] });
    else if (!grupo.codigos.includes(problema.codigo)) grupo.codigos.push(problema.codigo);
  }
  return grupos;
}

export function campoLegivel(campo: string): string {
  const limpo = [...campo].filter((caractere) => imprimivel(caractere)).join("");
  const pontos = [...limpo];
  return pontos.length > LIMITE_CAMPO ? `${pontos.slice(0, LIMITE_CAMPO).join("")}...` : limpo;
}

// Why: a file that ships in the image is not on the disk the panel writes to, and a driver
// an equipment uses is the type of a registration; both refusals are the daemon's, and
// saying which one applies before the request is what keeps the button honest.
// Por que: um arquivo que vem na imagem não está no disco em que o painel escreve, e um
// driver que um equipamento usa é o tipo de um cadastro; as duas recusas são do daemon, e
// dizer qual vale antes da requisição é o que mantém o botão honesto.
export function ofertaDeApagar(driver: DriverDeclarativo): OfertaApagar {
  if (driver.origem === "imagem") return "da_imagem";
  return driver.em_uso ? "em_uso" : "pode";
}

// Why: section 7, the integrator file wins a conflict of tipo with an embedded one, so the
// screen says which file this save takes the place of before it is written.
// Por que: seção 7, o arquivo do integrador vence o conflito de tipo com um embarcado,
// então a tela diz no lugar de qual arquivo este salvamento entra antes de gravar.
export function avisoDeSalvar(tipo: string, drivers: readonly DriverDeclarativo[]): AvisoSalvar {
  const atual = drivers.find((driver) => driver.tipo === tipo);
  if (atual === undefined) return null;
  return atual.origem === "imagem" ? "substitui_imagem" : "substitui_integrador";
}

export function textoDoModelo(modelo: Objeto): string {
  return JSON.stringify(modelo, null, 2);
}

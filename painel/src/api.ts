// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { guardar, ler, limpar } from "./sessao.ts";

export const SENHA_MINIMA = 8;

// Why: the daemon answers a stable code and never a human phrase, so these three
// stand in when there is no code to read: no answer, a body outside the contract,
// and an HTTP status the daemon did not label.
// Por que: o daemon responde um código estável e nunca uma frase humana, então
// estes três entram quando não há código para ler: sem resposta, corpo fora do
// contrato e um status HTTP que o daemon não rotulou.
export const CODIGO_SEM_RESPOSTA = "sem_resposta";
export const CODIGO_CORPO_INVALIDO = "corpo_invalido";
export const CODIGO_ERRO_HTTP = "erro_http";

// Why: these two are the only answers that mean the session itself is gone. Every
// other 401 (a wrong current password, for one) is about the body of the request,
// and dropping the token there signs the user out of a session the daemon honours.
// Por que: estes dois são as únicas respostas que dizem que a sessão acabou. Todo
// outro 401 (a senha atual errada, por exemplo) fala do corpo do pedido, e largar o
// token ali desconecta quem tem uma sessão que o daemon ainda aceita.
export const CODIGOS_SESSAO_MORTA: readonly string[] = ["sessao_invalida", "nao_autenticado"];

const PRAZO_MS = 5_000;

export class ErroApi extends Error {
  readonly code: string;
  readonly status: number;

  constructor(code: string, status = 0) {
    super(code);
    this.name = "ErroApi";
    this.code = code;
    this.status = status;
  }
}

export interface Estado {
  configurado: boolean;
  versao: string;
  schema_version: number;
  nome_instalacao: string;
}

export interface Credencial {
  token: string;
  expira_em_s: number;
}

export interface Sessao {
  expira_em_s: number;
}

type Objeto = Record<string, unknown>;

function ehObjeto(valor: unknown): valor is Objeto {
  return typeof valor === "object" && valor !== null && !Array.isArray(valor);
}

async function pedir(caminho: string, metodo: string, corpo?: unknown): Promise<Objeto> {
  const cabecalhos: Record<string, string> = { Accept: "application/json" };
  const token = ler();
  if (token) cabecalhos.Authorization = `Bearer ${token}`;
  if (corpo !== undefined) cabecalhos["Content-Type"] = "application/json";

  let resposta: Response;
  try {
    resposta = await fetch(caminho, {
      method: metodo,
      headers: cabecalhos,
      body: corpo === undefined ? undefined : JSON.stringify(corpo),
      cache: "no-store",
      signal: AbortSignal.timeout(PRAZO_MS),
    });
  } catch {
    throw new ErroApi(CODIGO_SEM_RESPOSTA);
  }

  const dados: unknown = await resposta.json().catch(() => null);
  const code = ehObjeto(dados) && typeof dados.code === "string" ? dados.code : null;
  // Why: a token the daemon no longer accepts would make every later request
  // answer 401, and the panel would keep showing a session that does not exist.
  // Por que: um token que o daemon não aceita mais faria toda requisição
  // seguinte responder 401, e o painel seguiria mostrando uma sessão que não existe.
  if (resposta.status === 401 && code !== null && CODIGOS_SESSAO_MORTA.includes(code)) limpar();
  if (ehObjeto(dados) && dados.ok === true) return dados;
  throw new ErroApi(
    code ?? (resposta.ok ? CODIGO_CORPO_INVALIDO : CODIGO_ERRO_HTTP),
    resposta.status,
  );
}

function aceitarCredencial(dados: Objeto): Credencial {
  if (typeof dados.token !== "string" || !dados.token || typeof dados.expira_em_s !== "number") {
    throw new ErroApi(CODIGO_CORPO_INVALIDO);
  }
  guardar(dados.token);
  return { token: dados.token, expira_em_s: dados.expira_em_s };
}

export async function lerEstado(): Promise<Estado> {
  const dados = await pedir("/api/estado", "GET");
  if (
    typeof dados.configurado !== "boolean" ||
    typeof dados.versao !== "string" ||
    typeof dados.schema_version !== "number" ||
    typeof dados.nome_instalacao !== "string"
  ) {
    throw new ErroApi(CODIGO_CORPO_INVALIDO);
  }
  return {
    configurado: dados.configurado,
    versao: dados.versao,
    schema_version: dados.schema_version,
    nome_instalacao: dados.nome_instalacao,
  };
}

export async function tomarPosse(codigo: string, senha: string): Promise<Credencial> {
  return aceitarCredencial(await pedir("/api/posse", "POST", { codigo, senha }));
}

export async function entrar(senha: string): Promise<Credencial> {
  return aceitarCredencial(await pedir("/api/entrar", "POST", { senha }));
}

export async function trocarSenha(atual: string, nova: string): Promise<Credencial> {
  const dados = await pedir("/api/senha", "POST", { senha_atual: atual, senha_nova: nova });
  return aceitarCredencial(dados);
}

export async function lerSessao(): Promise<Sessao> {
  const dados = await pedir("/api/sessao", "GET");
  if (typeof dados.expira_em_s !== "number") throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return { expira_em_s: dados.expira_em_s };
}

export async function sair(): Promise<void> {
  // Why: the daemon revoking the token is the point, but a failure to reach it
  // must not leave the browser holding a credential the user asked to drop.
  // Por que: revogar o token no daemon é o objetivo, mas falhar em alcançá-lo não
  // pode deixar o navegador com uma credencial que o usuário pediu para largar.
  try {
    await pedir("/api/sair", "POST");
  } finally {
    limpar();
  }
}

export function codigoDoErro(erro: unknown): string {
  return erro instanceof ErroApi ? erro.code : CODIGO_ERRO_HTTP;
}

// Why: the daemon measures the password in code points, the way python len does, and
// String.length measures UTF-16 units, so an astral character counts twice here and
// once there; the panel would accept a password the daemon then refuses.
// Por que: o daemon mede a senha em pontos de código, como o len do python, e o
// String.length mede unidades UTF-16, então um caractere astral conta duas vezes aqui
// e uma lá; o painel aceitaria uma senha que o daemon depois recusa.
export function senhaCurta(senha: string): boolean {
  return [...senha].length < SENHA_MINIMA;
}

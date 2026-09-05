// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { corpoDeCenas, lerLeituraDeCenas, type Cena, type LeituraDeCenas } from "./cenas.ts";
import {
  lerDriverDeclarativo,
  lerModelo,
  lerProblema,
  type DriverDeclarativo,
  type Problema,
  type Transporte,
} from "./declarativos.ts";
import {
  RESULTADOS_AUTENTICACAO,
  lerAchado,
  lerEquipamento,
  lerItemCatalogo,
  lerLista,
  type Achado,
  type Equipamento,
  type ItemCatalogo,
  type ResultadoAutenticacao,
} from "./equipamentos.ts";
import type { CorpoCadastro } from "./formulario.ts";
import {
  lerLeituraDeLicencas,
  lerLicenca,
  lerQr,
  lerSnapshot,
  type CorpoDeLicenca,
  type LeituraDeLicencas,
  type Licenca,
  type Qr,
  type Snapshot,
} from "./licencas.ts";
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

// Why: a discovery sweep waits for answers from the whole network for seconds on end,
// so the login deadline would cut it short before the last device answered.
// Por que: uma varredura de descoberta espera respostas da rede inteira por segundos,
// então o prazo de login a cortaria antes de o último aparelho responder.
const PRAZO_VARREDURA_MS = 20_000;

export class ErroApi extends Error {
  readonly code: string;
  readonly status: number;
  // Why: a refusal of a driver file carries one code per field (section 7), and the panel
  // shows all of them at once; every other route answers a single code and an empty tuple.
  // Por que: a recusa de um arquivo de driver carrega um código por campo (seção 7), e o
  // painel mostra todos de uma vez; toda outra rota responde um código só e uma tupla vazia.
  readonly problemas: readonly Problema[];

  constructor(code: string, status = 0, problemas: readonly Problema[] = []) {
    super(code);
    this.name = "ErroApi";
    this.code = code;
    this.status = status;
    this.problemas = problemas;
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

async function pedir(
  caminho: string,
  metodo: string,
  corpo?: unknown,
  prazoMs: number = PRAZO_MS,
): Promise<Objeto> {
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
      signal: AbortSignal.timeout(prazoMs),
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
  const problemas = ehObjeto(dados) ? lerLista(dados.problemas, lerProblema) : null;
  throw new ErroApi(
    code ?? (resposta.ok ? CODIGO_CORPO_INVALIDO : CODIGO_ERRO_HTTP),
    resposta.status,
    problemas ?? [],
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

export async function tomarPosse(senha: string): Promise<Credencial> {
  return aceitarCredencial(await pedir("/api/posse", "POST", { senha }));
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

export async function renomearInstalacao(nome: string): Promise<string> {
  const dados = await pedir("/api/instalacao", "POST", { nome });
  if (typeof dados.nome_instalacao !== "string") throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return dados.nome_instalacao;
}

export async function reiniciar(): Promise<void> {
  await pedir("/api/reiniciar", "POST");
}

export interface Atualizacao {
  atual: string;
  ultima: string | null;
  disponivel: boolean;
  verificada: boolean;
}

export async function lerAtualizacao(): Promise<Atualizacao> {
  const dados = await pedir("/api/atualizacao", "GET", undefined, PRAZO_VARREDURA_MS);
  const ultima = dados.ultima;
  if (
    typeof dados.atual !== "string" ||
    (ultima !== null && typeof ultima !== "string") ||
    typeof dados.disponivel !== "boolean" ||
    typeof dados.verificada !== "boolean"
  ) {
    throw new ErroApi(CODIGO_CORPO_INVALIDO);
  }
  return { atual: dados.atual, ultima, disponivel: dados.disponivel, verificada: dados.verificada };
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

// Why: an identity is a uuid, a MAC or a serial, and encoding it keeps a slash or a
// question mark inside one from addressing another route.
// Por que: uma identidade é uuid, MAC ou serial, e codificá-la impede que uma barra ou
// interrogação dentro dela enderece outra rota.
function rotaDoEquipamento(identidade: string): string {
  return `/api/equipamentos/${encodeURIComponent(identidade)}`;
}

export async function lerCatalogo(): Promise<ItemCatalogo[]> {
  const dados = await pedir("/api/catalogo", "GET");
  const catalogo = lerLista(dados.catalogo, lerItemCatalogo);
  if (catalogo === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return catalogo;
}

export async function lerEquipamentos(): Promise<Equipamento[]> {
  const dados = await pedir("/api/equipamentos", "GET");
  const equipamentos = lerLista(dados.equipamentos, lerEquipamento);
  if (equipamentos === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return equipamentos;
}

export async function cadastrarEquipamento(corpo: CorpoCadastro): Promise<void> {
  await pedir("/api/equipamentos", "POST", corpo);
}

// Why: the route takes the identity from the path and the body may only repeat it, so a
// correction changes the address, the name or a field and never the key of the
// registration; an absent secret keeps the credential the daemon already stores.
// Por que: a rota tira a identidade do caminho e o corpo só pode repeti-la, então uma
// correção muda endereço, nome ou campo e nunca a chave do cadastro; um segredo ausente
// mantém a credencial que o daemon já guarda.
export async function atualizarEquipamento(
  identidade: string,
  corpo: CorpoCadastro,
): Promise<void> {
  await pedir(rotaDoEquipamento(identidade), "POST", corpo);
}

export async function removerEquipamento(identidade: string): Promise<void> {
  await pedir(rotaDoEquipamento(identidade), "DELETE");
}

export async function executarAcao(
  identidade: string,
  acao: string,
  valor: unknown,
): Promise<void> {
  await pedir(`${rotaDoEquipamento(identidade)}/acao`, "POST", { acao, valor });
}

export async function autenticarEquipamento(identidade: string): Promise<ResultadoAutenticacao> {
  const dados = await pedir(`${rotaDoEquipamento(identidade)}/autenticar`, "POST");
  const resultado = RESULTADOS_AUTENTICACAO.find((esperado) => esperado === dados.resultado);
  if (resultado === undefined) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return resultado;
}

export async function varrer(): Promise<Achado[]> {
  const dados = await pedir("/api/descoberta", "POST", undefined, PRAZO_VARREDURA_MS);
  const achados = lerLista(dados.achados, lerAchado);
  if (achados === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return achados;
}

export async function lerDriversDeclarativos(): Promise<DriverDeclarativo[]> {
  const dados = await pedir("/api/drivers", "GET");
  const drivers = lerLista(dados.drivers, lerDriverDeclarativo);
  if (drivers === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return drivers;
}

// Why: the daemon validates the file and the panel never saves what it has not accepted, so
// these two send the same body to two routes and the caller decides which one it needs.
// Por que: o daemon valida o arquivo e o painel nunca salva o que ele não aceitou, então
// estas duas mandam o mesmo corpo para duas rotas e quem chama decide de qual precisa.
export async function validarDriver(arquivo: Record<string, unknown>): Promise<void> {
  await pedir("/api/drivers/validar", "POST", { json: arquivo });
}

export async function salvarDriver(arquivo: Record<string, unknown>): Promise<void> {
  await pedir("/api/drivers", "POST", { json: arquivo });
}

// Why: the tipo is a plain identifier that the panel refuses before it is typed into a
// path, and encoding it keeps a hand written one from addressing another route anyway.
// Por que: o tipo é um identificador simples que o painel recusa antes de virar caminho, e
// codificá-lo impede que um escrito à mão enderece outra rota de todo jeito.
export async function removerDriver(tipo: string): Promise<void> {
  await pedir(`/api/drivers/${encodeURIComponent(tipo)}`, "DELETE");
}

export async function lerModeloDriver(
  transporte: Transporte,
): Promise<Record<string, unknown>> {
  const dados = await pedir(`/api/drivers/modelo/${transporte}`, "GET");
  const modelo = lerModelo(dados.modelo);
  if (modelo === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return modelo;
}

// Why: the id of a licence is a short identifier the daemon refuses before it becomes a path,
// and encoding it keeps a hand written one from addressing another route anyway.
// Por que: o id de uma licença é um identificador curto que o daemon recusa antes de virar
// caminho, e codificá-lo impede que um escrito à mão enderece outra rota de todo jeito.
function rotaDaLicenca(id: string): string {
  return `/api/licencas/${encodeURIComponent(id)}`;
}

export async function lerLicencas(): Promise<LeituraDeLicencas> {
  const leitura = lerLeituraDeLicencas(await pedir("/api/licencas", "GET"));
  if (leitura === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return leitura;
}

function aceitarLicenca(dados: Objeto): Licenca {
  const licenca = lerLicenca(dados.licenca);
  if (licenca === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return licenca;
}

export async function criarLicenca(corpo: CorpoDeLicenca): Promise<Licenca> {
  return aceitarLicenca(await pedir("/api/licencas", "POST", corpo));
}

// Why: a field the body omits keeps the stored value, the chave included, so an edit that only
// fixes the name never erases the credential of the device.
// Por que: um campo que o corpo omite mantém o valor guardado, a chave inclusive, então uma
// edição que só conserta o nome nunca apaga a credencial do dispositivo.
export async function atualizarLicenca(id: string, corpo: CorpoDeLicenca): Promise<Licenca> {
  return aceitarLicenca(await pedir(rotaDaLicenca(id), "POST", corpo));
}

export async function removerLicenca(id: string): Promise<void> {
  await pedir(rotaDaLicenca(id), "DELETE");
}

// Why: the whole order travels, because the POSITION of a number is the contract of section 8;
// sending one slot alone would need an index anyway and a shorter list would move an equipment
// from number 2 to number 1 in every automation the customer already built.
// Por que: a ordem inteira viaja, porque a POSIÇÃO de um número é o contrato da seção 8; mandar
// uma vaga sozinha precisaria de um índice de todo jeito e uma lista mais curta moveria um
// equipamento do número 2 para o número 1 em toda automação que o cliente já montou.
// Why: a set on a licence may reach a whole group of speakers, each with a deadline of its
// own on the daemon, so these three wait the long deadline instead of giving up first.
// Por que: um set numa licença pode alcançar um grupo inteiro de caixas, cada uma com prazo
// próprio no daemon, então estes três esperam o prazo longo em vez de desistir antes.
export async function salvarNumeros(id: string, numeros: readonly string[]): Promise<void> {
  await pedir(`${rotaDaLicenca(id)}/numeros`, "POST", { numeros }, PRAZO_VARREDURA_MS);
}

export async function lerDps(id: string): Promise<Snapshot> {
  const snapshot = lerSnapshot(await pedir(`${rotaDaLicenca(id)}/dps`, "GET"));
  if (snapshot === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return snapshot;
}

export async function ajustarDp(id: string, dpid: number, valor: unknown): Promise<void> {
  await pedir(`${rotaDaLicenca(id)}/dp/${dpid}`, "POST", { v: valor }, PRAZO_VARREDURA_MS);
}

export async function definirGrupo(id: string, valor: number): Promise<void> {
  await pedir(`${rotaDaLicenca(id)}/grupo`, "POST", { v: valor }, PRAZO_VARREDURA_MS);
}

export async function lerQrDaLicenca(id: string): Promise<Qr> {
  const qr = lerQr(await pedir(`${rotaDaLicenca(id)}/qr`, "GET"));
  if (qr === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return qr;
}

export async function lerCenas(): Promise<LeituraDeCenas> {
  const leitura = lerLeituraDeCenas(await pedir("/api/cenas", "GET"));
  if (leitura === null) throw new ErroApi(CODIGO_CORPO_INVALIDO);
  return leitura;
}

export async function salvarCenas(cenas: readonly Cena[]): Promise<void> {
  await pedir("/api/cenas", "POST", { cenas: corpoDeCenas(cenas) });
}

export async function executarCena(numero: number): Promise<void> {
  await pedir(`/api/cenas/${numero}/executar`, "POST");
}

export function codigoDoErro(erro: unknown): string {
  return erro instanceof ErroApi ? erro.code : CODIGO_ERRO_HTTP;
}

export function problemasDoErro(erro: unknown): readonly Problema[] {
  return erro instanceof ErroApi ? erro.problemas : [];
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

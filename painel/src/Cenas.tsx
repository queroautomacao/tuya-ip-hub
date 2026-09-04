// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a scene is DATA, so this screen is an editor of a list and never a little language:
// a step names one data point and one value, plus a wait after it, and that is the whole
// vocabulary. Which data points may be named comes from the daemon, and the daemon refuses
// the list field by field, so the screen never has to decide what a scene may do. What the
// screen does decide is how a step reads: an equipment, then what to do with it, then the
// value; the wait after a step is the interval of the scene unless the step names its own.
// Por que: uma cena é DADO, então esta tela é um editor de uma lista e nunca uma
// linguagenzinha: um passo nomeia um data point e um valor, mais uma espera depois dele, e
// esse é o vocabulário inteiro. Quais data points podem ser nomeados vêm do daemon, e o daemon
// recusa a lista campo a campo, então a tela nunca precisa decidir o que uma cena pode fazer.
// O que a tela decide é como um passo se lê: um equipamento, depois o que fazer com ele, depois
// o valor; a espera depois de um passo é o intervalo da cena, salvo o passo nomear a dele.

import { useCallback, useEffect, useState } from "react";
import {
  codigoDoErro,
  executarCena,
  lerCatalogo,
  lerCenas,
  lerDps,
  lerBlocos,
  problemasDoErro,
  salvarCenas,
} from "./api.ts";
import {
  ajustaveis,
  comCenas,
  itemDoDp,
  nomeValido,
  prepararEspera,
  prepararIntervalo,
  prepararValor,
  textoDoValor,
  valorPadrao,
  type Cena,
  type ItemDoMapa,
  type PassoDeCena,
} from "./cenas.ts";
import { INTERVALO_MS, type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro, type Chave } from "./i18n";
import { controlesDaBloco, type Bloco } from "./blocos.ts";

interface Leitura {
  cenas: Cena[];
  maximo: number;
  espera_maxima_ms: number;
  intervalo_padrao_ms: number;
  passos_maximos: number;
  mapa: ItemDoMapa[];
  blocos: Bloco[];
  catalogo: ItemCatalogo[];
  erro: string | null;
}

const VAZIA: Leitura = {
  cenas: [],
  maximo: 0,
  espera_maxima_ms: 0,
  intervalo_padrao_ms: 0,
  passos_maximos: 0,
  mapa: [],
  blocos: [],
  catalogo: [],
  erro: null,
};

// Why: the function of a data point comes from the daemon as a stable word, so one this
// panel does not know prints the word itself instead of an empty label; a phrase for it is
// added here the day section 8 grows one.
// Por que: a função de um data point vem do daemon como palavra estável, então uma que este
// painel não conhece imprime a própria palavra em vez de um rótulo vazio; a frase para ela é
// acrescentada aqui no dia em que a seção 8 ganhar uma.
const CHAVE_DA_FUNCAO: Record<string, Chave> = {
  volume: "cenas_funcao_volume",
  play: "cenas_funcao_play",
  preset: "cenas_funcao_preset",
  entrada: "cenas_funcao_entrada",
  grupo: "cenas_funcao_grupo",
};

function rotuloDaFuncao(funcao: string, energia: boolean): string {
  if (funcao === "play" && energia) return t("cenas_funcao_energia");
  const chave = CHAVE_DA_FUNCAO[funcao];
  return chave === undefined ? funcao : t(chave);
}

// Why: section 8, DP 102 is play/pause for an equipment with transport and the power switch
// for any other, so a step reads "Power: on" on a matrix and "Play or pause: play" on a
// speaker, from what the driver of the equipment in that number declares.
// Por que: seção 8, o DP 102 é play/pause para um equipamento com transporte e a chave de
// ligar para qualquer outro, então um passo se lê "Ligar ou desligar: ligar" numa matriz e
// "Play ou pause: play" numa caixa, a partir do que o driver do equipamento naquele número
// declara.
function energiaNoBloco(
  numero: number,
  blocos: readonly Bloco[],
  catalogo: readonly ItemCatalogo[],
): boolean {
  const bloco = blocos.find((candidato) => candidato.bloco === numero);
  if (bloco === undefined) return false;
  const item = catalogo.find((candidato) => candidato.tipo === bloco.tipo);
  return controlesDaBloco(bloco, item).some(
    (controle) => controle.funcao === "play" && controle.especie === "ligar",
  );
}

function rotuloDaBloco(numero: number, blocos: readonly Bloco[]): string {
  if (numero === 0) return t("cenas_global");
  const bloco = blocos.find((candidata) => candidata.bloco === numero);
  const nome = bloco === undefined ? "" : bloco.nome || bloco.identidade;
  return nome ? `${t("blocos_bloco")} ${numero}: ${nome}` : `${t("blocos_bloco")} ${numero}`;
}

function blocosDoMapa(mapa: readonly ItemDoMapa[]): number[] {
  return [...new Set(mapa.map((item) => item.bloco))].sort((a, b) => a - b);
}

function Valor({
  item,
  passo,
  energia,
  aoMudar,
}: {
  item: ItemDoMapa;
  passo: PassoDeCena;
  energia: boolean;
  aoMudar: (valor: unknown, codigo: string | null) => void;
}) {
  const texto = textoDoValor(passo.valor);
  const escolher = (bruto: string): void => {
    const preparo = prepararValor(item, bruto);
    aoMudar(preparo.ok ? preparo.valor : bruto, preparo.ok ? null : preparo.codigo);
  };
  if (item.tipo === "bool") {
    return (
      <select aria-label={t("cenas_valor")} value={texto} onChange={(evento) => escolher(evento.target.value)}>
        <option value="true">{rotuloDoLogico(item, energia, true)}</option>
        <option value="false">{rotuloDoLogico(item, energia, false)}</option>
      </select>
    );
  }
  if (item.tipo === "enum" && item.valores.length > 0) {
    return (
      <select aria-label={t("cenas_valor")} value={texto} onChange={(evento) => escolher(evento.target.value)}>
        {item.valores.map((valor) => (
          <option key={valor} value={valor}>
            {valor}
          </option>
        ))}
      </select>
    );
  }
  return (
    <input
      className="curto"
      type={item.tipo === "value" ? "number" : "text"}
      inputMode={item.tipo === "value" ? "numeric" : "text"}
      min={0}
      max={100}
      aria-label={t("cenas_valor")}
      value={texto}
      onChange={(evento) => escolher(evento.target.value)}
    />
  );
}

function rotuloDoLogico(item: ItemDoMapa, energia: boolean, valor: boolean): string {
  if (item.funcao !== "play") return valor ? t("sim") : t("nao");
  if (energia) return valor ? t("acao_ligar") : t("acao_desligar");
  return valor ? t("blocos_tocar") : t("blocos_pausar");
}

function Passo({
  passo,
  mapa,
  blocos,
  catalogo,
  maximoDeEspera,
  intervalo,
  aoMudar,
  aoRemover,
}: {
  passo: PassoDeCena;
  mapa: ItemDoMapa[];
  blocos: Bloco[];
  catalogo: ItemCatalogo[];
  maximoDeEspera: number;
  intervalo: number;
  aoMudar: (novo: PassoDeCena, codigo: string | null) => void;
  aoRemover: () => void;
}) {
  const item = itemDoDp(mapa, passo.dpid);
  const remover = (
    <button type="button" className="passo-remover" aria-label={t("cenas_remover_passo")} onClick={aoRemover}>
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
        <path d="M6 6l12 12M18 6 6 18" />
      </svg>
    </button>
  );
  if (item === undefined) {
    return (
      <li className="passo">
        <span className="erro">{traduzirErro("cena_dp_desconhecido")}</span>
        {remover}
      </li>
    );
  }
  const funcoes = mapa.filter((candidato) => candidato.bloco === item.bloco);
  const energia = energiaNoBloco(item.bloco, blocos, catalogo);
  const trocar = (escolhido: ItemDoMapa | undefined): void => {
    if (escolhido === undefined) return;
    aoMudar({ ...passo, dpid: escolhido.dpid, valor: valorPadrao(escolhido) }, null);
  };
  return (
    <li className="passo">
      <div className="passo-alvo">
        <select
          aria-label={t("cenas_passo_bloco")}
          value={String(item.bloco)}
          onChange={(evento) => {
            // Why: moving a step to another block keeps what it does when that block offers it,
            // so "volume of block 1" dragged to block 2 is "volume of block 2" and not a reset.
            // Por que: mover um passo para outro bloco mantém o que ele faz quando aquele bloco
            // oferece isso, então "volume do bloco 1" levado ao bloco 2 é "volume do bloco 2" e
            // não um recomeço.
            const bloco = Number(evento.target.value);
            const mesma = mapa.find((c) => c.bloco === bloco && c.funcao === item.funcao);
            trocar(mesma ?? mapa.find((c) => c.bloco === bloco));
          }}
        >
          {blocosDoMapa(mapa).map((bloco) => (
            <option key={bloco} value={String(bloco)}>
              {rotuloDaBloco(bloco, blocos)}
            </option>
          ))}
        </select>
        <select
          aria-label={t("cenas_passo_o_que")}
          value={String(item.dpid)}
          onChange={(evento) => trocar(itemDoDp(mapa, Number(evento.target.value)))}
        >
          {funcoes.map((candidato) => (
            <option key={candidato.dpid} value={String(candidato.dpid)}>
              {rotuloDaFuncao(candidato.funcao, energia)}
            </option>
          ))}
        </select>
      </div>
      <Valor
        item={item}
        passo={passo}
        energia={energia}
        aoMudar={(valor, codigo) => aoMudar({ ...passo, valor }, codigo)}
      />
      <label className="passo-espera">
        <span className="texto-suave">{t("cenas_e_depois")}</span>
        <input
          className="curto"
          type="number"
          inputMode="numeric"
          min={0}
          max={maximoDeEspera}
          aria-label={t("cenas_espera")}
          placeholder={String(intervalo)}
          value={passo.espera_ms === null ? "" : String(passo.espera_ms)}
          onChange={(evento) => {
            const preparo = prepararEspera(evento.target.value, maximoDeEspera);
            const espera = preparo.ok ? (preparo.valor as number | null) : passo.espera_ms;
            aoMudar({ ...passo, espera_ms: espera }, preparo.ok ? null : preparo.codigo);
          }}
        />
        <span className="texto-suave">{t("cenas_ms")}</span>
      </label>
      {remover}
    </li>
  );
}

function CartaoCena({
  cena,
  leitura,
  ocupado,
  aoMudar,
  aoExecutar,
}: {
  cena: Cena;
  leitura: Leitura;
  ocupado: boolean;
  aoMudar: (nova: Cena, codigo: string | null) => void;
  aoExecutar: () => void;
}) {
  const { mapa, blocos, catalogo } = leitura;
  const cheia = cena.passos.length >= leitura.passos_maximos;
  const vazia = cena.passos.length === 0;
  return (
    <li className={`cena ${vazia ? "cena-vazia" : ""}`}>
      <div className="cena-cabeca">
        <span className="cena-numero" aria-label={`${t("cenas_numero")} ${cena.numero}`}>
          {cena.numero}
        </span>
        <input
          className="cena-nome"
          type="text"
          placeholder={t("cenas_sem_nome")}
          aria-label={t("cenas_nome")}
          value={cena.nome}
          onChange={(evento) =>
            aoMudar(
              { ...cena, nome: evento.target.value },
              nomeValido(evento.target.value) ? null : "cena_nome_invalido",
            )
          }
        />
        {cena.em_curso && <span className="etiqueta">{t("cenas_em_curso")}</span>}
        <button
          type="button"
          className="botao cena-executar"
          disabled={ocupado || vazia}
          aria-label={`${t("cenas_executar")} ${cena.numero}`}
          onClick={aoExecutar}
        >
          <svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
            <path d="M8 5v14l11-7z" />
          </svg>
          <span>{t("cenas_executar")}</span>
        </button>
      </div>
      {vazia ? (
        <p className="texto-suave cena-dica">{t("cenas_vazia")}</p>
      ) : (
        <label className="cena-intervalo">
          <span>{t("cenas_intervalo")}</span>
          <input
            className="curto"
            type="number"
            inputMode="numeric"
            min={0}
            max={leitura.espera_maxima_ms}
            aria-label={t("cenas_intervalo")}
            value={String(cena.intervalo_ms)}
            onChange={(evento) => {
              const preparo = prepararIntervalo(evento.target.value, leitura.espera_maxima_ms);
              const intervalo = preparo.ok ? (preparo.valor as number) : cena.intervalo_ms;
              aoMudar({ ...cena, intervalo_ms: intervalo }, preparo.ok ? null : preparo.codigo);
            }}
          />
          <span>{t("cenas_ms")}</span>
          <span className="texto-suave cena-intervalo-ajuda">{t("cenas_intervalo_ajuda")}</span>
        </label>
      )}
      {!vazia && (
        <ol className="passos">
          {cena.passos.map((passo, indice) => (
            <Passo
              key={`${indice}-${passo.dpid}`}
              passo={passo}
              mapa={mapa}
              blocos={blocos}
              catalogo={catalogo}
              maximoDeEspera={leitura.espera_maxima_ms}
              intervalo={cena.intervalo_ms}
              aoMudar={(novo, codigo) =>
                aoMudar(
                  { ...cena, passos: cena.passos.map((atual, posicao) => (posicao === indice ? novo : atual)) },
                  codigo,
                )
              }
              aoRemover={() =>
                aoMudar({ ...cena, passos: cena.passos.filter((_ignorado, posicao) => posicao !== indice) }, null)
              }
            />
          ))}
        </ol>
      )}
      <button
        type="button"
        className="botao secundario"
        disabled={cheia || mapa.length === 0}
        onClick={() => {
          const primeiro = mapa[0];
          if (primeiro === undefined) return;
          aoMudar(
            { ...cena, passos: [...cena.passos, { dpid: primeiro.dpid, valor: valorPadrao(primeiro), espera_ms: null }] },
            null,
          );
        }}
      >
        + {t("cenas_novo_passo")}
      </button>
    </li>
  );
}

export default function Cenas() {
  const [leitura, setLeitura] = useState<Leitura>(VAZIA);
  const [rascunho, setRascunho] = useState<Cena[] | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [problemas, setProblemas] = useState<readonly { campo: string; codigo: string }[]>([]);
  const [salvo, setSalvo] = useState(false);
  const [ocupado, setOcupado] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      const [cenas, snapshot, blocos, catalogo] = await Promise.all([
        lerCenas(),
        lerDps(),
        lerBlocos(),
        lerCatalogo(),
      ]);
      setLeitura({
        cenas: comCenas(cenas.cenas, cenas.maximo, cenas.intervalo_padrao_ms),
        maximo: cenas.maximo,
        espera_maxima_ms: cenas.espera_maxima_ms,
        intervalo_padrao_ms: cenas.intervalo_padrao_ms,
        passos_maximos: cenas.passos_maximos,
        mapa: ajustaveis(snapshot.mapa),
        blocos: blocos.blocos,
        catalogo,
        erro: null,
      });
    } catch (falha) {
      setLeitura((anterior) => ({ ...anterior, erro: codigoDoErro(falha) }));
    }
  }, []);

  useEffect(() => {
    void recarregar();
    // Why: the cycle refreshes what is running, and a draft in the middle of being typed is
    // never overwritten by it; whoever is editing keeps what they wrote until they save.
    // Por que: o ciclo atualiza o que está rodando, e um rascunho sendo digitado nunca é
    // sobrescrito por ele; quem edita mantém o que escreveu até salvar.
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  const cenas = rascunho ?? leitura.cenas;

  function mudar(numero: number, nova: Cena, codigo: string | null): void {
    setSalvo(false);
    setErro(codigo);
    setRascunho(cenas.map((cena) => (cena.numero === numero ? nova : cena)));
  }

  async function chamar(trabalho: () => Promise<void>): Promise<void> {
    setOcupado(true);
    try {
      await trabalho();
      setErro(null);
      setProblemas([]);
      await recarregar();
    } catch (falha) {
      setErro(codigoDoErro(falha));
      setProblemas(problemasDoErro(falha));
    } finally {
      setOcupado(false);
    }
  }

  return (
    <>
      <div className="tela-cabeca">
        <div>
          <h2>{t("cenas_titulo")}</h2>
          <p>{t("cenas_intro")}</p>
        </div>
      </div>
      {leitura.erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(leitura.erro)}
        </p>
      )}
      <ul className="cenas">
        {cenas.map((cena) => (
          <CartaoCena
            key={cena.numero}
            cena={cena}
            leitura={leitura}
            ocupado={ocupado}
            aoMudar={(nova, codigo) => mudar(cena.numero, nova, codigo)}
            aoExecutar={() => void chamar(() => executarCena(cena.numero))}
          />
        ))}
      </ul>
      {(rascunho !== null || salvo || erro !== null || problemas.length > 0) && (
        <div className="cenas-rodape" role="region" aria-live="polite">
          {rascunho !== null && (
            <div className="acoes-largas">
              <button
                type="button"
                className="botao"
                disabled={ocupado}
                onClick={() =>
                  void chamar(async () => {
                    await salvarCenas(cenas);
                    setRascunho(null);
                    setSalvo(true);
                  })
                }
              >
                {ocupado ? t("enviando") : t("cenas_salvar")}
              </button>
              <button
                type="button"
                className="botao secundario"
                disabled={ocupado}
                onClick={() => {
                  setRascunho(null);
                  setErro(null);
                  setProblemas([]);
                }}
              >
                {t("cenas_descartar")}
              </button>
            </div>
          )}
          {salvo && (
            <p className="sucesso" role="status">
              {t("cenas_salvo")}
            </p>
          )}
          {erro !== null && (
            <p className="erro" role="alert">
              {traduzirErro(erro)}
            </p>
          )}
          {problemas.length > 0 && (
            <ul className="problemas">
              {problemas.map((problema) => (
                <li key={`${problema.campo}-${problema.codigo}`}>
                  <code>{problema.campo}</code> {traduzirErro(problema.codigo)}
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </>
  );
}

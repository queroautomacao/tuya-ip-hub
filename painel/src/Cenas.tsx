// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a scene is DATA, so this screen is an editor of a list and never a little language:
// a step names one data point and one value, plus a wait after it, and that is the whole
// vocabulary. Which data points may be named comes from the daemon, and the daemon refuses
// the list field by field, so the screen never has to decide what a scene may do.
// Por que: uma cena é DADO, então esta tela é um editor de uma lista e nunca uma
// linguagenzinha: um passo nomeia um data point e um valor, mais uma espera depois dele, e
// esse é o vocabulário inteiro. Quais data points podem ser nomeados vêm do daemon, e o daemon
// recusa a lista campo a campo, então a tela nunca precisa decidir o que uma cena pode fazer.

import { useCallback, useEffect, useState } from "react";
import { codigoDoErro, executarCena, lerCenas, lerDps, problemasDoErro, salvarCenas } from "./api.ts";
import {
  ajustaveis,
  comCenas,
  itemDoDp,
  nomeValido,
  prepararEspera,
  prepararValor,
  textoDoValor,
  valorPadrao,
  type Cena,
  type ItemDoMapa,
  type PassoDeCena,
} from "./cenas.ts";
import { INTERVALO_MS } from "./equipamentos.ts";
import { t, traduzirErro, type Chave } from "./i18n";

interface Leitura {
  cenas: Cena[];
  maximo: number;
  espera_maxima_ms: number;
  passos_maximos: number;
  mapa: ItemDoMapa[];
  erro: string | null;
}

const VAZIA: Leitura = {
  cenas: [],
  maximo: 0,
  espera_maxima_ms: 0,
  passos_maximos: 0,
  mapa: [],
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

function rotuloDoDp(item: ItemDoMapa): string {
  const chave = CHAVE_DA_FUNCAO[item.funcao];
  const funcao = chave === undefined ? item.funcao : t(chave);
  return item.zona === 0 ? funcao : `${t("zonas_bloco")} ${item.zona}: ${funcao}`;
}

function Valor({
  item,
  passo,
  aoMudar,
}: {
  item: ItemDoMapa;
  passo: PassoDeCena;
  aoMudar: (valor: unknown, codigo: string | null) => void;
}) {
  const texto = textoDoValor(passo.valor);
  const escolher = (bruto: string): void => {
    const preparo = prepararValor(item, bruto);
    aoMudar(preparo.ok ? preparo.valor : bruto, preparo.ok ? null : preparo.codigo);
  };
  if (item.tipo === "bool") {
    return (
      <select
        className="valor"
        aria-label={t("cenas_valor")}
        value={texto}
        onChange={(evento) => escolher(evento.target.value)}
      >
        <option value="true">{t("sim")}</option>
        <option value="false">{t("nao")}</option>
      </select>
    );
  }
  if (item.tipo === "enum" && item.valores.length > 0) {
    return (
      <select
        className="valor"
        aria-label={t("cenas_valor")}
        value={texto}
        onChange={(evento) => escolher(evento.target.value)}
      >
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
      className="valor"
      type={item.tipo === "value" ? "number" : "text"}
      min={0}
      max={100}
      aria-label={t("cenas_valor")}
      value={texto}
      onChange={(evento) => escolher(evento.target.value)}
    />
  );
}

function Passo({
  passo,
  mapa,
  maximoDeEspera,
  aoMudar,
  aoRemover,
}: {
  passo: PassoDeCena;
  mapa: ItemDoMapa[];
  maximoDeEspera: number;
  aoMudar: (novo: PassoDeCena, codigo: string | null) => void;
  aoRemover: () => void;
}) {
  const item = itemDoDp(mapa, passo.dpid);
  if (item === undefined) {
    return (
      <li className="cena-passo">
        <span className="erro">{traduzirErro("cena_dp_desconhecido")}</span>
        <button type="button" className="botao secundario" onClick={aoRemover}>
          {t("cenas_remover_passo")}
        </button>
      </li>
    );
  }
  return (
    <li className="cena-passo">
      <select
        className="valor"
        aria-label={t("cenas_passo_dp")}
        value={String(passo.dpid)}
        onChange={(evento) => {
          const escolhido = itemDoDp(mapa, Number(evento.target.value));
          if (escolhido === undefined) return;
          aoMudar({ ...passo, dpid: escolhido.dpid, valor: valorPadrao(escolhido) }, null);
        }}
      >
        {mapa.map((candidato) => (
          <option key={candidato.dpid} value={String(candidato.dpid)}>
            {rotuloDoDp(candidato)}
          </option>
        ))}
      </select>
      <Valor
        item={item}
        passo={passo}
        aoMudar={(valor, codigo) => aoMudar({ ...passo, valor }, codigo)}
      />
      <input
        className="valor"
        type="number"
        min={0}
        max={maximoDeEspera}
        aria-label={t("cenas_espera")}
        value={String(passo.espera_ms)}
        onChange={(evento) => {
          const preparo = prepararEspera(evento.target.value, maximoDeEspera);
          const espera = preparo.ok ? (preparo.valor as number) : passo.espera_ms;
          aoMudar({ ...passo, espera_ms: espera }, preparo.ok ? null : preparo.codigo);
        }}
      />
      <button type="button" className="botao secundario" onClick={aoRemover}>
        {t("cenas_remover_passo")}
      </button>
    </li>
  );
}

function CartaoCena({
  cena,
  mapa,
  leitura,
  ocupado,
  aoMudar,
  aoExecutar,
}: {
  cena: Cena;
  mapa: ItemDoMapa[];
  leitura: Leitura;
  ocupado: boolean;
  aoMudar: (nova: Cena, codigo: string | null) => void;
  aoExecutar: () => void;
}) {
  const cheia = cena.passos.length >= leitura.passos_maximos;
  return (
    <li className="cena">
      <div className="cena-cabeca">
        <h3>
          {t("cenas_numero")} {cena.numero}
        </h3>
        {cena.em_curso && <span className="etiqueta">{t("cenas_em_curso")}</span>}
      </div>
      <label className="cena-nome">
        <span className="texto-suave">{t("cenas_nome")}</span>
        <input
          type="text"
          value={cena.nome}
          onChange={(evento) =>
            aoMudar(
              { ...cena, nome: evento.target.value },
              nomeValido(evento.target.value) ? null : "cena_nome_invalido",
            )
          }
        />
      </label>
      <ul className="cena-passos">
        {cena.passos.map((passo, indice) => (
          <Passo
            key={`${indice}-${passo.dpid}`}
            passo={passo}
            mapa={mapa}
            maximoDeEspera={leitura.espera_maxima_ms}
            aoMudar={(novo, codigo) =>
              aoMudar(
                {
                  ...cena,
                  passos: cena.passos.map((atual, posicao) =>
                    posicao === indice ? novo : atual,
                  ),
                },
                codigo,
              )
            }
            aoRemover={() =>
              aoMudar(
                { ...cena, passos: cena.passos.filter((_ignorado, posicao) => posicao !== indice) },
                null,
              )
            }
          />
        ))}
      </ul>
      <div className="acoes">
        <button
          type="button"
          className="botao secundario"
          disabled={cheia || mapa.length === 0}
          onClick={() => {
            const primeiro = mapa[0];
            if (primeiro === undefined) return;
            aoMudar(
              {
                ...cena,
                passos: [
                  ...cena.passos,
                  { dpid: primeiro.dpid, valor: valorPadrao(primeiro), espera_ms: 0 },
                ],
              },
              null,
            );
          }}
        >
          {t("cenas_novo_passo")}
        </button>
        <button
          type="button"
          className="botao secundario"
          disabled={ocupado || cena.passos.length === 0}
          onClick={aoExecutar}
        >
          {t("cenas_executar")}
        </button>
      </div>
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
      const [cenas, snapshot] = await Promise.all([lerCenas(), lerDps()]);
      setLeitura({
        cenas: comCenas(cenas.cenas, cenas.maximo),
        maximo: cenas.maximo,
        espera_maxima_ms: cenas.espera_maxima_ms,
        passos_maximos: cenas.passos_maximos,
        mapa: ajustaveis(snapshot.mapa),
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
    <section className="cartao">
      <h2>{t("cenas_titulo")}</h2>
      <p className="texto-suave">{t("cenas_intro")}</p>
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
            mapa={leitura.mapa}
            leitura={leitura}
            ocupado={ocupado}
            aoMudar={(nova, codigo) => mudar(cena.numero, nova, codigo)}
            aoExecutar={() => void chamar(() => executarCena(cena.numero))}
          />
        ))}
      </ul>
      <div className="acoes">
        <button
          type="button"
          className="botao"
          disabled={ocupado || rascunho === null}
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
        {rascunho !== null && (
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
        )}
      </div>
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
    </section>
  );
}

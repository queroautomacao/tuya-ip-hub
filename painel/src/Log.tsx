// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: when a driver does not answer or a button of the app of the customer does nothing, the
// question is always the same: what did the hub actually do? This screen answers it in one
// place, in order, with what the driver put on the wire, what the bridge of the platform asked
// for and what the panel changed, and it copies the whole thing so a report carries it.
// Por que: quando um driver não responde ou um botão do app do cliente não faz nada, a pergunta
// é sempre a mesma: o que o hub fez de verdade? Esta tela responde num lugar só, em ordem, com
// o que o driver pôs no fio, o que a ponte da plataforma pediu e o que o painel mudou, e copia
// tudo para um relato levar junto.

import { useCallback, useEffect, useRef, useState } from "react";
import { codigoDoErro, lerLog, type LinhaDoLog } from "./api.ts";
import { ORIGENS, comoTexto, filtrar, horaDe, type Origem } from "./log.ts";
import { t, traduzirErro, type Chave } from "./i18n";

// Why: a hub under a scene writes a few lines a second, and a screen that redrew faster than
// that would only spend the battery of the tablet reading the same thing.
// Por que: um hub rodando uma cena escreve algumas linhas por segundo, e uma tela que
// redesenhasse mais rápido que isso só gastaria a bateria do tablet lendo a mesma coisa.
const INTERVALO_MS = 2_000;

export default function Log() {
  const [linhas, setLinhas] = useState<LinhaDoLog[]>([]);
  const [descartadas, setDescartadas] = useState(0);
  const [erro, setErro] = useState<string | null>(null);
  const [origens, setOrigens] = useState<Origem[]>([]);
  const [busca, setBusca] = useState("");
  const [seguindo, setSeguindo] = useState(true);
  const [copiado, setCopiado] = useState(false);
  const caixa = useRef<HTMLPreElement | null>(null);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      const log = await lerLog();
      setLinhas(log.linhas);
      setDescartadas(log.descartadas);
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    }
  }, []);

  useEffect(() => {
    void recarregar();
    const temporizador = window.setInterval(() => void recarregar(), INTERVALO_MS);
    return () => window.clearInterval(temporizador);
  }, [recarregar]);

  const visiveis = filtrar(linhas, origens, busca);

  // Why: a log is read at the end, where what just happened is, so the box follows the last
  // line while the operator has not scrolled away from it.
  // Por que: um log é lido no fim, onde está o que acabou de acontecer, então a caixa segue a
  // última linha enquanto o operador não sai dela.
  useEffect(() => {
    const alvo = caixa.current;
    if (alvo !== null && seguindo) alvo.scrollTop = alvo.scrollHeight;
  }, [visiveis.length, seguindo]);

  const alternar = (origem: Origem): void =>
    setOrigens(
      origens.includes(origem)
        ? origens.filter((outra) => outra !== origem)
        : [...origens, origem],
    );

  function copiar(): void {
    const texto = comoTexto(visiveis);
    void (async () => {
      try {
        // Why: a kiosk tablet on plain http has no clipboard API, so the fallback is the one
        // that has worked since forever: select the text and let the browser copy it.
        // Por que: um tablet de quiosque em http puro não tem API de área de transferência,
        // então o plano B é o que sempre funcionou: selecionar o texto e deixar o navegador
        // copiar.
        if (navigator.clipboard !== undefined) {
          await navigator.clipboard.writeText(texto);
        } else {
          const campo = document.createElement("textarea");
          campo.value = texto;
          document.body.appendChild(campo);
          campo.select();
          document.execCommand("copy");
          document.body.removeChild(campo);
        }
        setCopiado(true);
        window.setTimeout(() => setCopiado(false), 2_000);
      } catch {
        setErro("copia_falhou");
      }
    })();
  }

  return (
    <>
      <div className="tela-cabeca">
        <div>
          <h2>{t("log_titulo")}</h2>
          <p>{t("log_intro")}</p>
        </div>
      </div>
      <section className="cartao">
        <div className="log-barra">
          <div className="fichas" role="group" aria-label={t("log_origens")}>
            {ORIGENS.map((origem) => (
              <button
                key={origem}
                type="button"
                className="ficha"
                aria-pressed={origens.includes(origem)}
                onClick={() => alternar(origem)}
              >
                {t(`log_origem_${origem}` as Chave)}
              </button>
            ))}
          </div>
          <input
            type="search"
            className="log-busca"
            value={busca}
            placeholder={t("log_busca")}
            aria-label={t("log_busca")}
            onChange={(evento) => setBusca(evento.target.value)}
          />
        </div>
        <div className="log-acoes">
          <button type="button" className="botao" onClick={() => copiar()}>
            {copiado ? t("log_copiado") : t("log_copiar")}
          </button>
          <label className="log-seguir">
            <input
              type="checkbox"
              checked={seguindo}
              onChange={(evento) => setSeguindo(evento.target.checked)}
            />
            {t("log_seguir")}
          </label>
          <span className="texto-suave" role="status">
            {`${visiveis.length} / ${linhas.length}`}
            {descartadas > 0 ? ` · ${descartadas} ${t("log_descartadas")}` : ""}
          </span>
        </div>
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
        <pre className="log-caixa" ref={caixa} tabIndex={0} aria-label={t("log_titulo")}>
          {visiveis.length === 0 ? (
            <span className="texto-suave">{t("log_vazio")}</span>
          ) : (
            visiveis.map((linha, indice) => (
              <span key={`${linha.t}-${indice}`} className={`log-linha nivel-${linha.nivel}`}>
                <span className="log-hora">{horaDe(linha.t)}</span>
                <span className={`log-origem origem-${linha.origem}`}>{linha.onde}</span>
                <span className="log-texto">{linha.texto}</span>
              </span>
            ))
          )}
        </pre>
      </section>
    </>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// A speaker is commanded far more often than it is configured, and walking into the
// detail screen to press pause is a trip the customer takes ten times a day. The card of the
// home carries the keys that are pressed, and nothing else: the transport, the volume and the
// mute, each drawn only when the manifest of the driver declares it.

import { useEffect, useState } from "react";
import { ICONES, Icone, palavra } from "./ControlesEquipamento.tsx";
import { codigoDoErro, executarAcao } from "./api.ts";
import { paineis, semSetpoint, type Equipamento, type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";

// The slider shows the value it was released at until the equipment reads it back, the
// same wait the controls of the detail screen give it.
const ESPERA_DE_LEITURA_MS = 4_000;

export default function ControlesRapidos({
  equipamento,
  item,
  aoMudar,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  aoMudar: () => void;
}) {
  const [arrastando, setArrastando] = useState<number | null>(null);
  const [pendente, setPendente] = useState<number | null>(null);
  const [ocupado, setOcupado] = useState(false);
  const [erro, setErro] = useState<string | null>(null);
  const estado = equipamento.estado;
  useEffect(() => {
    setPendente(null);
  }, [estado.volume]);
  useEffect(() => {
    if (pendente === null) return undefined;
    const temporizador = window.setTimeout(() => setPendente(null), ESPERA_DE_LEITURA_MS);
    return () => window.clearTimeout(temporizador);
  }, [pendente]);

  const painel = paineis(item?.capacidades ?? []);
  const chaves = painel.transporte;
  // A bar the device has no level for is a promise that dragging it moves something, and
  // on a television whose sound leaves by ARC to a receiver every drag comes back an error.
  // The card of the home draws the same guard as the detail, or the two disagree on one set.
  const temNivel = painel.volume && estado.volume !== null;
  // An air conditioner has no transport and no level, so the card of the home drew
  // nothing for it while every other equipment had its keys there; what a person presses on
  // an air conditioner without opening it is the power, the setpoint by one degree and the
  // mode, so those are its quick keys. The mode words come from the manifest, which is what
  // the detail screen draws too.
  const clima = painel.temperatura;
  const modos = clima && painel.modo ? (item?.modos ?? []) : [];
  const ventos = clima && painel.vento ? (item?.ventos ?? []) : [];
  // Power is the first thing a person presses on any equipment, so every card that
  // declares it carries the two keys, next to the transport keys or, on an air conditioner,
  // ahead of the setpoint.
  const energia = painel.energia;
  if (chaves.length === 0 && energia.length === 0 && !temNivel && !painel.mudo && !clima) {
    return null;
  }

  function mandar(acao: string, valor: unknown = null): void {
    setOcupado(true);
    void (async () => {
      try {
        await executarAcao(equipamento.identidade, acao, valor);
        setErro(null);
        aoMudar();
      } catch (falha) {
        setErro(codigoDoErro(falha));
      } finally {
        setOcupado(false);
      }
    })();
  }

  const volume = arrastando ?? pendente ?? estado.volume ?? 0;
  const soltar = (): void => {
    if (arrastando !== null) {
      setPendente(arrastando);
      mandar("volume", arrastando);
    }
    setArrastando(null);
  };
  // A driver that cannot tell whether the transport plays leaves reproduzindo
  // empty, and one key that guessed would never send the other half.
  const alterna = painel.transporte.includes("tocar") && painel.transporte.includes("pausar");
  const tocando = estado.reproduzindo === true;
  const teclas = chaves.filter((acao) => {
    if (!alterna || estado.reproduzindo === null) return true;
    return acao !== (tocando ? "tocar" : "pausar");
  });

  const graus = estado.temperatura;
  const ajustar = (passo: number): void => {
    if (graus === null) return;
    mandar("temperatura", graus + passo);
  };

  const botoesDeEnergia = energia.map((acao) => (
    <button
      key={acao}
      type="button"
      className={`botao-icone botao-icone-pequeno ${estado.ligado === (acao === "ligar") ? "botao-icone-aceso" : ""}`}
      disabled={ocupado}
      aria-pressed={estado.ligado === (acao === "ligar")}
      aria-label={t(`acao_${acao}` as const)}
      title={t(`acao_${acao}` as const)}
      onClick={() => mandar(acao)}
    >
      <Icone desenho={ICONES[acao]} />
    </button>
  ));

  return (
    <div className="rapidos">
      {clima && (
        <div className="rapidos-clima" role="group" aria-label={t("controles_temperatura")}>
          {botoesDeEnergia}
          <div className="rapidos-setpoint">
            <button
              type="button"
              className="botao-icone botao-icone-pequeno"
              disabled={ocupado || graus === null}
              aria-label={`${t("acao_temperatura")} -1`}
              onClick={() => ajustar(-1)}
            >
              -
            </button>
            <span className="controle-volume-valor">{graus === null ? "--" : `${graus}°`}</span>
            <button
              type="button"
              className="botao-icone botao-icone-pequeno"
              disabled={ocupado || graus === null}
              aria-label={`${t("acao_temperatura")} +1`}
              onClick={() => ajustar(1)}
            >
              +
            </button>
          </div>
          {semSetpoint(estado) && <p className="dica rapidos-dica">{t("clima_sem_setpoint")}</p>}
          {modos.length > 0 && (
            <div className="rapidos-modos" role="group" aria-label={t("controles_modo")}>
              {modos.map((modo) => (
                <button
                  key={modo}
                  type="button"
                  className="ficha ficha-miuda"
                  disabled={ocupado}
                  aria-pressed={estado.modo === modo}
                  onClick={() => mandar("modo", modo)}
                >
                  {palavra("modo_ar", modo)}
                </button>
              ))}
            </div>
          )}
          {ventos.length > 0 && (
            <div className="rapidos-modos" role="group" aria-label={t("controles_vento")}>
              {ventos.map((vento) => (
                <button
                  key={vento}
                  type="button"
                  className="ficha ficha-miuda"
                  disabled={ocupado}
                  aria-pressed={estado.vento === vento}
                  onClick={() => mandar("vento", vento)}
                >
                  {palavra("vento", vento)}
                </button>
              ))}
            </div>
          )}
        </div>
      )}
      {(teclas.length > 0 || (!clima && energia.length > 0)) && (
        <div className="rapidos-teclas" role="group" aria-label={t("controles_transporte")}>
          {!clima && botoesDeEnergia}
          {teclas.map((acao) => (
            <button
              key={acao}
              type="button"
              className="botao-icone botao-icone-pequeno"
              disabled={ocupado}
              aria-label={t(`acao_${acao}` as const)}
              title={t(`acao_${acao}` as const)}
              onClick={() => mandar(acao)}
            >
              <Icone desenho={ICONES[acao]} />
            </button>
          ))}
        </div>
      )}
      {(temNivel || painel.mudo) && (
        <div className="rapidos-volume">
          {temNivel && (
            <>
              <span className="controle-volume-valor">{volume}</span>
              <input
                type="range"
                min={0}
                max={100}
                value={volume}
                aria-label={`${t("acao_volume")}: ${equipamento.nome || equipamento.identidade}`}
                onChange={(evento) => setArrastando(Number(evento.target.value))}
                onPointerUp={soltar}
                onKeyUp={soltar}
              />
            </>
          )}
          {painel.mudo && (
            <button
              type="button"
              className={`botao-icone botao-icone-pequeno ${estado.mudo === true ? "botao-icone-aceso" : ""}`}
              disabled={ocupado}
              aria-pressed={estado.mudo === true}
              aria-label={t("acao_mudo")}
              title={t("acao_mudo")}
              onClick={() => mandar("mudo", !(estado.mudo ?? false))}
            >
              <Icone desenho={ICONES.mudo} />
            </button>
          )}
        </div>
      )}
      {erro !== null && (
        <p className="erro" role="alert">
          {traduzirErro(erro)}
        </p>
      )}
    </div>
  );
}

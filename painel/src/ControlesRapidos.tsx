// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: a speaker is commanded far more often than it is configured, and walking into the
// detail screen to press pause is a trip the customer takes ten times a day. The card of the
// home carries the keys that are pressed, and nothing else: the transport, the volume and the
// mute, each drawn only when the manifest of the driver declares it (section 6).
// Por que: uma caixa é comandada muito mais vezes do que é configurada, e entrar na tela de
// detalhe para apertar pausa é uma viagem que o cliente faz dez vezes por dia. O cartão do
// início leva as teclas que são apertadas, e nada mais: o transporte, o volume e o mudo, cada
// um desenhado só quando o manifesto do driver o declara (seção 6).

import { useEffect, useState } from "react";
import { ICONES, Icone } from "./ControlesEquipamento.tsx";
import { codigoDoErro, executarAcao } from "./api.ts";
import { paineis, type Equipamento, type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";

// Why: the slider shows the value it was released at until the equipment reads it back, the
// same wait the controls of the detail screen give it.
// Por que: o slider mostra o valor em que foi solto até o equipamento o ler de volta, a mesma
// espera que os controles da tela de detalhe dão a ele.
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
  if (chaves.length === 0 && !painel.volume && !painel.mudo) return null;

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
  // Why: section 6, a driver that cannot tell whether the transport plays leaves reproduzindo
  // empty, and one key that guessed would never send the other half.
  // Por que: seção 6, um driver que não sabe dizer se o transporte toca deixa reproduzindo
  // vazio, e uma tecla que adivinhasse nunca mandaria a outra metade.
  const alterna = painel.transporte.includes("tocar") && painel.transporte.includes("pausar");
  const tocando = estado.reproduzindo === true;
  const teclas = chaves.filter((acao) => {
    if (!alterna || estado.reproduzindo === null) return true;
    return acao !== (tocando ? "tocar" : "pausar");
  });

  return (
    <div className="rapidos">
      {teclas.length > 0 && (
        <div className="rapidos-teclas" role="group" aria-label={t("controles_transporte")}>
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
      {(painel.volume || painel.mudo) && (
        <div className="rapidos-volume">
          {painel.volume && (
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

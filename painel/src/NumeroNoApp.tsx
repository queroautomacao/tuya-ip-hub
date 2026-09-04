// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Why: the app of the customer shows up to six equipment, numbered 1 to 6 (section 8), and the
// number of an equipment is a fact about that equipment, so it is chosen on its own screen and
// not on a list of slots. Multiroom is a capability of the equipment (section 6), so the group
// it can lead lives on the same screen, right under the number it needs.
// Por que: o app do cliente mostra até seis equipamentos, numerados de 1 a 6 (seção 8), e o
// número de um equipamento é um fato sobre aquele equipamento, então ele é escolhido na tela
// dele e não numa lista de vagas. Multiroom é capacidade do equipamento (seção 6), então o
// grupo que ele pode liderar mora na mesma tela, logo abaixo do número de que ele precisa.

import { useCallback, useEffect, useState } from "react";
import { codigoDoErro, definirGrupo, lerBlocos, salvarBlocos } from "./api.ts";
import {
  SOLO,
  comIdentidade,
  ordemDe,
  podeAgrupar,
  valorDoGrupo,
  type Bloco,
  type LeituraDeBlocos,
} from "./blocos.ts";
import type { Equipamento, ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";

function rotulo(bloco: Bloco): string {
  return `${t("blocos_bloco")} ${bloco.bloco}: ${bloco.nome || bloco.identidade}`;
}

function Multiroom({
  equipamento,
  leitura,
  atual,
  ocupado,
  aoChamar,
}: {
  equipamento: Equipamento;
  leitura: LeituraDeBlocos;
  atual: Bloco | undefined;
  ocupado: boolean;
  aoChamar: (trabalho: () => Promise<void>) => void;
}) {
  // Why: section 14, a group only exists between equipment of the same tipo, so the members
  // offered are the others of this tipo that have a number on the app, and nobody else.
  // Por que: seção 14, um grupo só existe entre equipamentos do mesmo tipo, então os membros
  // oferecidos são os outros deste tipo que têm número no app, e mais ninguém.
  const pares = leitura.blocos.filter(
    (bloco) =>
      bloco.identidade !== "" &&
      bloco.identidade !== equipamento.identidade &&
      bloco.tipo === equipamento.tipo,
  );
  const lidera = atual !== undefined && leitura.grupo === valorDoGrupo(atual.bloco);
  const segue = atual?.papel === "escravo";
  return (
    <section className="cartao">
      <h2>{t("multiroom_titulo")}</h2>
      <p className="texto-suave">{t("multiroom_intro")}</p>
      {atual === undefined && <p className="dica">{t("multiroom_precisa_numero")}</p>}
      {atual !== undefined && pares.length === 0 && <p className="dica">{t("multiroom_sem_par")}</p>}
      {atual !== undefined && pares.length > 0 && (
        <>
          <p role="status">
            {lidera ? t("multiroom_lidera") : segue ? t("multiroom_segue") : t("multiroom_solo")}
          </p>
          <p className="texto-suave">{t("multiroom_membros")}</p>
          <ul className="multiroom-membros">
            {pares.map((bloco) => (
              <li key={bloco.bloco}>{rotulo(bloco)}</li>
            ))}
          </ul>
          <div className="acoes-largas">
            <button
              type="button"
              className="botao"
              disabled={ocupado || lidera}
              onClick={() => aoChamar(() => definirGrupo(valorDoGrupo(atual.bloco)))}
            >
              {t("multiroom_liderar")}
            </button>
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado || !(lidera || segue)}
              onClick={() => aoChamar(() => definirGrupo(SOLO))}
            >
              {t("multiroom_desfazer")}
            </button>
          </div>
        </>
      )}
    </section>
  );
}

export default function NumeroNoApp({
  equipamento,
  item,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
}) {
  const [leitura, setLeitura] = useState<LeituraDeBlocos | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      setLeitura(await lerBlocos());
      setErro(null);
    } catch (falha) {
      setErro(codigoDoErro(falha));
    }
  }, []);

  useEffect(() => {
    void recarregar();
  }, [recarregar]);

  function chamar(trabalho: () => Promise<void>): void {
    setOcupado(true);
    void (async () => {
      try {
        await trabalho();
        setErro(null);
        await recarregar();
      } catch (falha) {
        setErro(codigoDoErro(falha));
      } finally {
        setOcupado(false);
      }
    })();
  }

  const blocos = leitura?.blocos ?? [];
  const atual = blocos.find((bloco) => bloco.identidade === equipamento.identidade);
  const numero = atual?.bloco ?? 0;

  function escolher(bruto: string): void {
    const escolhido = Number(bruto);
    const ordem = ordemDe(blocos);
    // Why: leaving the app empties the number where it is, and taking a number takes the
    // equipment off the one it had; a shift would renumber the app of the customer.
    // Por que: sair do app esvazia o número onde ele está, e tomar um número tira o
    // equipamento do que ele tinha; um empurrão renumeraria o app do cliente.
    const nova =
      escolhido === 0
        ? comIdentidade(ordem, numero, "")
        : comIdentidade(ordem, escolhido, equipamento.identidade);
    chamar(() => salvarBlocos(nova));
  }

  return (
    <>
      <section className="cartao">
        <h2>{t("numero_titulo")}</h2>
        <p className="texto-suave">{t("numero_intro")}</p>
        <div className="numero-opcoes">
          <label htmlFor="numero-no-app">{t("numero_rotulo")}</label>
          <select
            id="numero-no-app"
            value={String(numero)}
            disabled={ocupado || leitura === null}
            onChange={(evento) => escolher(evento.target.value)}
          >
            <option value="0">{t("numero_fora")}</option>
            {blocos.map((bloco) => (
              <option key={bloco.bloco} value={String(bloco.bloco)}>
                {`${t("blocos_bloco")} ${bloco.bloco}`}
                {bloco.identidade !== "" && bloco.identidade !== equipamento.identidade
                  ? ` (${t("numero_ocupado")} ${bloco.nome || bloco.identidade})`
                  : ""}
              </option>
            ))}
          </select>
        </div>
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
      </section>
      {leitura !== null && podeAgrupar(item) && (
        <Multiroom
          equipamento={equipamento}
          leitura={leitura}
          atual={atual}
          ocupado={ocupado}
          aoChamar={chamar}
        />
      )}
    </>
  );
}

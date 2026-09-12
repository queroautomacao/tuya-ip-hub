// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The app of the customer shows the equipment of each licence, numbered, and
// the number of an equipment is a fact about that equipment, so it is chosen on its own screen
// and not on a list of slots. Multiroom is a capability of the equipment, so the
// group it can lead lives on the same screen, right under the number it needs.

import { useCallback, useEffect, useState } from "react";
import { codigoDoErro, definirGrupo, lerLicencas, salvarNumeros } from "./api.ts";
import { INTERVALO_MS, type Equipamento, type ItemCatalogo } from "./equipamentos.ts";
import { t, traduzirErro } from "./i18n";
import {
  SOLO,
  comIdentidade,
  licencasDe,
  nomeDoNumero,
  onde,
  ordemDe,
  podeAgrupar,
  semIdentidade,
  type LeituraDeLicencas,
  type Licenca,
  type Numero,
} from "./licencas.ts";

const FORA = "0";

function nomeDaLicenca(licenca: Licenca): string {
  return licenca.nome || licenca.id;
}

function chaveDe(licenca: Licenca, numero: number): string {
  return `${licenca.id}:${numero}`;
}

function Multiroom({
  equipamento,
  licenca,
  atual,
  ocupado,
  aoChamar,
}: {
  equipamento: Equipamento;
  licenca: Licenca;
  atual: Numero;
  ocupado: boolean;
  aoChamar: (trabalho: () => Promise<void>) => void;
}) {
  // A group only exists between equipment of the same tipo, so the members
  // offered are the others of this tipo with a number on the same licence, and nobody else.
  const pares = licenca.numeros.filter(
    (numero) =>
      numero.identidade !== "" &&
      numero.identidade !== equipamento.identidade &&
      numero.tipo === equipamento.tipo,
  );
  const lidera = licenca.grupo === atual.numero;
  const segue = atual.papel === "escravo";
  const alheio = atual.papel === "alheio";
  // A master carries several slaves and the customer picks them one by one,
  // so the card is a list of who follows and not a single switch; the boxes start on what the
  // group has right now, and a licence whose group changed elsewhere reopens on the new one.
  const membrosAgora = lidera
    ? pares.filter((numero) => numero.papel === "escravo").map((numero) => numero.numero)
    : [];
  const marca = `${licenca.id}:${licenca.grupo}:${membrosAgora.join(",")}`;
  const [rascunho, setRascunho] = useState<number[] | null>(null);
  const [marcaLida, setMarcaLida] = useState(marca);
  if (marcaLida !== marca) {
    setMarcaLida(marca);
    setRascunho(null);
  }
  const escolhidos = rascunho ?? membrosAgora;
  const alternar = (numero: number): void =>
    setRascunho(
      escolhidos.includes(numero)
        ? escolhidos.filter((outro) => outro !== numero)
        : [...escolhidos, numero].sort((um, outro) => um - outro),
    );
  const mestre = licenca.numeros.find((numero) => numero.numero === licenca.grupo);
  return (
    <section className="cartao">
      <h2>{t("multiroom_titulo")}</h2>
      <p className="texto-suave">{t("multiroom_intro")}</p>
      {pares.length === 0 && <p className="dica">{t("multiroom_sem_par")}</p>}
      {pares.length > 0 && (
        <>
          <p role="status">
            {lidera
              ? t("multiroom_lidera")
              : segue
                ? `${t("multiroom_segue")} ${mestre === undefined ? "" : nomeDoNumero(mestre)}`.trim()
                : alheio
                  ? t("multiroom_alheio")
                  : t("multiroom_solo")}
          </p>
          {!segue && !alheio && (
            <>
              <p className="texto-suave">{t("multiroom_escolha")}</p>
              <ul className="multiroom-membros">
                {pares.map((numero) => (
                  <li key={numero.numero}>
                    <label className="multiroom-membro">
                      <input
                        type="checkbox"
                        checked={escolhidos.includes(numero.numero)}
                        disabled={ocupado}
                        onChange={() => alternar(numero.numero)}
                      />
                      <span>{`${numero.numero}: ${nomeDoNumero(numero)}`}</span>
                      {numero.papel === "alheio" && (
                        <span className="selo-papel">{t("multiroom_membro_alheio")}</span>
                      )}
                    </label>
                  </li>
                ))}
              </ul>
            </>
          )}
          <div className="acoes-largas">
            {!segue && !alheio && (
              <button
                type="button"
                className="botao"
                disabled={ocupado || escolhidos.length === 0}
                onClick={() =>
                  aoChamar(async () => {
                    await definirGrupo(licenca.id, atual.numero, escolhidos);
                    setRascunho(null);
                  })
                }
              >
                {lidera ? t("multiroom_aplicar") : t("multiroom_liderar")}
              </button>
            )}
            <button
              type="button"
              className="botao secundario"
              disabled={ocupado || !(lidera || segue)}
              onClick={() => aoChamar(() => definirGrupo(licenca.id, SOLO))}
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
  somenteGrupo = false,
}: {
  equipamento: Equipamento;
  item: ItemCatalogo | undefined;
  // The group changes what the keys of the controls do, so it is a card of its own right
  // under them; the number is setup, so it lives inside the card of the setup. The two read
  // the same licences, so one component answers for both and each screen asks for its half.
  somenteGrupo?: boolean;
}) {
  const [leitura, setLeitura] = useState<LeituraDeLicencas | null>(null);
  const [erro, setErro] = useState<string | null>(null);
  const [ocupado, setOcupado] = useState(false);

  const recarregar = useCallback(async (): Promise<void> => {
    try {
      setLeitura(await lerLicencas());
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

  const licencas = leitura === null ? [] : licencasDe(leitura.licencas, item);
  const posicao = leitura === null ? undefined : onde(leitura.licencas, equipamento.identidade);
  const escolhido = posicao === undefined ? FORA : chaveDe(posicao.licenca, posicao.numero.numero);

  function escolher(bruto: string): void {
    // The orders are rebuilt from a fresh reading right before they are written, because
    // the whole list goes on the wire and a number given in another window meanwhile would be
    // erased by a stale copy of it. Leaving a licence empties the number where it is, and
    // taking a number takes the equipment off the one it had; a shift would renumber the app.
    chamar(async () => {
      const fresco = await lerLicencas();
      const atual = onde(fresco.licencas, equipamento.identidade);
      const [id, numero] = bruto === FORA ? ["", 0] : bruto.split(":");
      const alvo = fresco.licencas.find((licenca) => licenca.id === id);
      if (atual !== undefined && (alvo === undefined || atual.licenca.id !== alvo.id)) {
        await salvarNumeros(
          atual.licenca.id,
          semIdentidade(ordemDe(atual.licenca), equipamento.identidade),
        );
      }
      if (alvo !== undefined) {
        try {
          await salvarNumeros(
            alvo.id,
            comIdentidade(ordemDe(alvo), Number(numero), equipamento.identidade),
          );
        } catch (falha) {
          // The move is two writes, and a second one the daemon refused would leave the
          // equipment with no number at all; the old number is put back before the refusal
          // is shown.
          if (atual !== undefined && atual.licenca.id !== alvo.id) {
            await salvarNumeros(atual.licenca.id, ordemDe(atual.licenca)).catch(() => undefined);
          }
          throw falha;
        }
      }
    });
  }

  if (somenteGrupo) {
    if (!podeAgrupar(item)) return null;
    if (posicao !== undefined && posicao.licenca.produto === "av") {
      return (
        <Multiroom
          equipamento={equipamento}
          licenca={posicao.licenca}
          atual={posicao.numero}
          ocupado={ocupado}
          aoChamar={chamar}
        />
      );
    }
    if (posicao === undefined) {
      return (
        <section className="cartao">
          <h2>{t("multiroom_titulo")}</h2>
          <p className="dica">{t("multiroom_precisa_numero")}</p>
        </section>
      );
    }
    return null;
  }

  return (
    <>
      <div className="bloco-de-configuracao">
        <h3>{t("numero_titulo")}</h3>
        <p className="texto-suave">{t("numero_intro")}</p>
        {leitura !== null && licencas.length === 0 && (
          <p className="dica">{t("numero_sem_licenca")}</p>
        )}
        {licencas.length > 0 && (
          <div className="numero-opcoes">
            <label htmlFor="numero-no-app">{t("numero_rotulo")}</label>
            <select
              id="numero-no-app"
              value={escolhido}
              disabled={ocupado || leitura === null}
              onChange={(evento) => escolher(evento.target.value)}
            >
              <option value={FORA}>{t("numero_fora")}</option>
              {licencas.map((licenca) => (
                <optgroup key={licenca.id} label={`${t("numero_licenca")}: ${nomeDaLicenca(licenca)}`}>
                  {licenca.numeros.map((numero) => (
                    <option
                      key={numero.numero}
                      value={chaveDe(licenca, numero.numero)}
                      disabled={numero.identidade !== "" && numero.identidade !== equipamento.identidade}
                    >
                      {`${numero.numero}`}
                      {numero.identidade !== "" && numero.identidade !== equipamento.identidade
                        ? ` (${t("numero_ocupado")} ${nomeDoNumero(numero)})`
                        : ""}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>
        )}
        {erro !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erro)}
          </p>
        )}
      </div>
    </>
  );
}

// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// Registering and correcting an equipment ask for the same fields of the same
// manifest, so one component renders both and the two screens cannot drift apart.

import { Fragment } from "react";
import { textoDoManifesto, type Campo, type ItemCatalogo } from "./equipamentos.ts";
import type { Formulario } from "./formulario.ts";
import { t, type Chave, type Idioma } from "./i18n";

const ROTULOS_FIXOS: Record<string, Chave> = {
  tipo: "cadastro_tipo",
  identidade: "cadastro_identidade",
  nome: "cadastro_nome",
  ip: "cadastro_ip",
  nivel_maximo: "cadastro_nivel_maximo",
};

// The operator has to be told WHICH field the daemon or the panel refused, and a
// declared field is named by the manifest that declared it. The name is the LABEL and the
// text of the manifest is the HINT, which is what every driver in the catalogue writes
// there: a whole sentence teaching what to type. Reading that sentence as a label put a
// paragraph where two words belong, and next to it the value was squeezed to one letter per
// line.
export function rotuloDoCampo(nome: string): string {
  const fixo = ROTULOS_FIXOS[nome];
  if (fixo !== undefined) return t(fixo);
  return nomeLegivel(nome);
}

export function nomeLegivel(nome: string): string {
  const limpo = nome.replace(/_/g, " ").trim();
  return limpo.charAt(0).toUpperCase() + limpo.slice(1);
}

export function dicaDoCampo(
  item: ItemCatalogo | undefined,
  idioma: Idioma,
  nome: string,
): string {
  return textoDoManifesto(item, idioma, `campo_${nome}`);
}

function EntradaCampo({
  campo,
  formulario,
  guardados,
  item,
  idioma,
  prefixo,
  aoMudar,
}: {
  campo: Campo;
  formulario: Formulario;
  guardados: readonly string[];
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  prefixo: string;
  aoMudar: (proximo: Formulario) => void;
}) {
  const id = `${prefixo}-campo-${campo.nome}`;
  const segredo = campo.tipo === "segredo";
  const guardado = guardados.includes(campo.nome);
  const apagando = formulario.apagar.includes(campo.nome);
  const dica = dicaDoCampo(item, idioma, campo.nome);
  return (
    <Fragment>
      <label htmlFor={id}>{rotuloDoCampo(campo.nome)}</label>
      <input
        id={id}
        name={campo.nome}
        // A SEGREDO is a device credential, so it is typed hidden and the browser
        // never offers to remember it next to a panel password.
        type={segredo ? "password" : "text"}
        inputMode={campo.tipo === "inteiro" ? "numeric" : undefined}
        required={campo.obrigatorio && !(segredo && (guardado || apagando))}
        disabled={apagando}
        autoComplete={segredo ? "new-password" : "off"}
        value={formulario.campos[campo.nome] ?? ""}
        onChange={(evento) =>
          aoMudar({
            ...formulario,
            campos: { ...formulario.campos, [campo.nome]: evento.target.value },
          })
        }
      />
      {dica !== "" && <p className="dica">{dica}</p>}
      {segredo && guardado && <p className="dica">{t("segredo_guardado")}</p>}
      {segredo && guardado && !campo.obrigatorio && (
        <label className="caixa" htmlFor={`${id}-apagar`}>
          <input
            id={`${id}-apagar`}
            type="checkbox"
            checked={apagando}
            onChange={(evento) =>
              aoMudar({
                ...formulario,
                apagar: evento.target.checked
                  ? [...formulario.apagar, campo.nome]
                  : formulario.apagar.filter((nome) => nome !== campo.nome),
              })
            }
          />
          {t("segredo_apagar")}
        </label>
      )}
    </Fragment>
  );
}

// The fields of a driver that pairs are not guessable, and a cloud one has nothing else:
// the ThinQ of an LG air conditioner asks for a country, a personal access token and a device
// id, and where each of the three comes from is a paragraph the manifest already writes. It
// was only shown on the equipment screen, next to the pairing button, which is AFTER the
// registration exists: the integrator met the empty fields first and the instructions later.
// What has to be turned on IN THE DEVICE before it answers (Network Standby, Wake on
// LAN, Control by mobile apps, the local interface of a speaker) is the first thing an
// integrator needs and the last thing he finds, because it lives in a menu of the device and
// not in this panel. Every driver writes it in preparo, and it is read here, before the
// fields, in the language of the panel; the pairing steps follow it for a driver that pairs.
function AjudaDeAutenticacao({
  item,
  idioma,
}: {
  item: ItemCatalogo | undefined;
  idioma: Idioma;
}) {
  if (item === undefined) return null;
  const preparo = textoDoManifesto(item, idioma, "preparo");
  const ajuda = item.auth === "nenhuma" ? "" : textoDoManifesto(item, idioma, "auth_ajuda");
  if (preparo === "" && ajuda === "") return null;
  return (
    <div className="dica-de-cadastro">
      {preparo !== "" && (
        <p className="dica-de-cadastro-bloco">
          <b>{t("cadastro_preparo")}</b>
          {"\n"}
          {preparo}
        </p>
      )}
      {ajuda !== "" && (
        <p className="dica-de-cadastro-bloco">
          <b>{t("cadastro_pareamento")}</b>
          {"\n"}
          {ajuda}
        </p>
      )}
    </div>
  );
}

export default function FormularioEquipamento({
  item,
  idioma,
  formulario,
  guardados,
  prefixo,
  fixarIdentidade,
  aoMudar,
}: {
  item: ItemCatalogo | undefined;
  idioma: Idioma;
  formulario: Formulario;
  guardados: readonly string[];
  prefixo: string;
  fixarIdentidade: boolean;
  aoMudar: (proximo: Formulario) => void;
}) {
  return (
    <>
      <AjudaDeAutenticacao item={item} idioma={idioma} />
      <label htmlFor={`${prefixo}-identidade`}>{t("cadastro_identidade")}</label>
      <input
        id={`${prefixo}-identidade`}
        name="identidade"
        type="text"
        required
        autoComplete="off"
        spellCheck={false}
        // The identity is the key of the registration, so an edit shows it and never
        // lets it change; changing it would be a different equipment.
        readOnly={fixarIdentidade}
        value={formulario.identidade}
        onChange={(evento) => aoMudar({ ...formulario, identidade: evento.target.value })}
      />
      {!fixarIdentidade && <p className="dica">{t("cadastro_identidade_dica")}</p>}
      <label htmlFor={`${prefixo}-nome`}>{t("cadastro_nome")}</label>
      <input
        id={`${prefixo}-nome`}
        name="nome"
        type="text"
        autoComplete="off"
        value={formulario.nome}
        onChange={(evento) => aoMudar({ ...formulario, nome: evento.target.value })}
      />
      {/* A driver of the cloud of a maker is reached over the internet and */}
      {/* Has no address on this network, so asking for one would be a field nobody can fill. */}
      {item?.nuvem === true ? (
        <p className="dica">{t("cadastro_na_nuvem")}</p>
      ) : (
        <>
          <label htmlFor={`${prefixo}-ip`}>{t("cadastro_ip")}</label>
          <input
            id={`${prefixo}-ip`}
            name="ip"
            type="text"
            required
            autoComplete="off"
            spellCheck={false}
            value={formulario.ip}
            onChange={(evento) => aoMudar({ ...formulario, ip: evento.target.value })}
          />
        </>
      )}
      {/* The ceiling only means something on an equipment whose level the hub sets. */}
      {item?.capacidades.includes("volume") === true && (
        <>
          <label htmlFor={`${prefixo}-nivel-maximo`}>{t("cadastro_nivel_maximo")}</label>
          <input
            id={`${prefixo}-nivel-maximo`}
            name="nivel_maximo"
            type="number"
            inputMode="numeric"
            min={1}
            max={100}
            value={formulario.nivel_maximo}
            onChange={(evento) => aoMudar({ ...formulario, nivel_maximo: evento.target.value })}
          />
          <p className="dica">{t("cadastro_nivel_maximo_dica")}</p>
        </>
      )}
      {(item?.config_campos ?? []).map((campo) => (
        <EntradaCampo
          key={campo.nome}
          campo={campo}
          formulario={formulario}
          guardados={guardados}
          item={item}
          idioma={idioma}
          prefixo={prefixo}
          aoMudar={aoMudar}
        />
      ))}
    </>
  );
}

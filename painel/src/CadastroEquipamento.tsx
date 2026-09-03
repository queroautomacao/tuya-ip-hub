// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useState, type FormEvent } from "react";
import FormularioEquipamento, { rotuloDoCampo } from "./FormularioEquipamento.tsx";
import { cadastrarEquipamento, codigoDoErro, varrer } from "./api.ts";
import { rotuloDoTipo, textoDoManifesto, type Achado, type ItemCatalogo } from "./equipamentos.ts";
import {
  VAZIO,
  ofertaDoAchado,
  padroes,
  validarCadastro,
  type Formulario,
  type OfertaAchado,
} from "./formulario.ts";
import { t, traduzirErro, type Idioma } from "./i18n";

function Oferta({ oferta, aoPreencher }: { oferta: OfertaAchado; aoPreencher: () => void }) {
  // Why: a device that answered without an identity cannot be prefilled, because the key
  // of a registration is the identity and the operator has no way to invent it; saying so
  // is honest, offering a form they cannot finish is not.
  // Por que: um aparelho que respondeu sem identidade não pode ser preenchido, porque a
  // chave do cadastro é a identidade e o operador não tem como inventá-la; dizer isso é
  // honesto, oferecer um formulário que ele não termina não é.
  if (oferta === "sem_tipo") return <p className="dica">{t("descoberta_sem_tipo_dica")}</p>;
  if (oferta === "sem_identidade") {
    return <p className="dica">{t("descoberta_sem_identidade")}</p>;
  }
  return (
    <button
      type="button"
      className="botao secundario"
      disabled={oferta === "ja_cadastrado"}
      onClick={aoPreencher}
    >
      {oferta === "ja_cadastrado" ? t("descoberta_ja_cadastrado") : t("descoberta_cadastrar")}
    </button>
  );
}

function Varredura({
  achados,
  catalogo,
  idioma,
  aoPreencher,
}: {
  achados: Achado[];
  catalogo: readonly ItemCatalogo[];
  idioma: Idioma;
  aoPreencher: (achado: Achado) => void;
}) {
  return (
    <ul className="achados">
      {achados.map((achado) => (
        <li key={`${achado.ip}-${achado.identidade}-${achado.tipo}`}>
          <div>
            <p className="achado-titulo">
              {achado.tipo
                ? rotuloDoTipo(
                    catalogo.find((candidato) => candidato.tipo === achado.tipo),
                    idioma,
                    achado.tipo,
                  )
                : t("descoberta_sem_tipo")}
            </p>
            <p className="texto-suave">
              {achado.porta === null ? achado.ip : `${achado.ip}:${achado.porta}`}
            </p>
            {achado.descricao && <p className="discreto">{achado.descricao}</p>}
          </div>
          <Oferta oferta={ofertaDoAchado(achado)} aoPreencher={() => aoPreencher(achado)} />
        </li>
      ))}
    </ul>
  );
}

export default function CadastroEquipamento({
  catalogo,
  idioma,
  aoCadastrar,
}: {
  catalogo: ItemCatalogo[] | null;
  idioma: Idioma;
  aoCadastrar: () => void;
}) {
  const [formulario, setFormulario] = useState<Formulario>(VAZIO);
  const [erro, setErro] = useState<{ codigo: string; campo: string } | null>(null);
  const [pronto, setPronto] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [achados, setAchados] = useState<Achado[] | null>(null);
  const [varrendo, setVarrendo] = useState(false);
  const [erroVarredura, setErroVarredura] = useState<string | null>(null);

  const lidos = catalogo ?? [];
  const item = lidos.find((candidato) => candidato.tipo === formulario.tipo);

  async function procurar(): Promise<void> {
    setVarrendo(true);
    try {
      setAchados(await varrer());
      setErroVarredura(null);
    } catch (falha) {
      setErroVarredura(codigoDoErro(falha));
    } finally {
      setVarrendo(false);
    }
  }

  function escolherTipo(tipo: string): void {
    const escolhido = lidos.find((candidato) => candidato.tipo === tipo);
    setPronto(false);
    setFormulario((atual) => ({ ...atual, tipo, campos: padroes(escolhido), apagar: [] }));
  }

  function preencher(achado: Achado): void {
    const escolhido = lidos.find((candidato) => candidato.tipo === achado.tipo);
    setErro(null);
    setPronto(false);
    setFormulario({
      tipo: achado.tipo,
      identidade: achado.identidade,
      nome: "",
      ip: achado.ip,
      campos: padroes(escolhido),
      apagar: [],
    });
  }

  async function enviar(evento: FormEvent<HTMLFormElement>): Promise<void> {
    evento.preventDefault();
    setPronto(false);
    const validacao = validarCadastro(formulario, lidos);
    if (!validacao.ok) {
      setErro({ codigo: validacao.codigo, campo: validacao.campo });
      return;
    }
    setErro(null);
    setEnviando(true);
    try {
      await cadastrarEquipamento(validacao.corpo);
      setFormulario(VAZIO);
      setPronto(true);
      aoCadastrar();
    } catch (falha) {
      setErro({ codigo: codigoDoErro(falha), campo: "" });
    } finally {
      setEnviando(false);
    }
  }

  return (
    <>
      <section className="cartao">
        <h2>{t("descoberta_titulo")}</h2>
        <p className="texto-suave">{t("descoberta_intro")}</p>
        <button
          type="button"
          className="botao secundario"
          disabled={varrendo}
          onClick={() => void procurar()}
        >
          {varrendo ? t("descoberta_varrendo") : t("descoberta_varrer")}
        </button>
        {erroVarredura !== null && (
          <p className="erro" role="alert">
            {traduzirErro(erroVarredura)}
          </p>
        )}
        {achados !== null && achados.length === 0 && (
          <p className="texto-suave">{t("descoberta_vazio")}</p>
        )}
        {achados !== null && achados.length > 0 && (
          <Varredura
            achados={achados}
            catalogo={lidos}
            idioma={idioma}
            aoPreencher={preencher}
          />
        )}
      </section>
      <section className="cartao">
        <h2>{t("cadastro_titulo")}</h2>
        {/* Why: a catalog the panel never managed to read is not an image without */}
        {/* drivers, and saying so would be a false statement about the product. */}
        {/* Por que: um catálogo que o painel nunca conseguiu ler não é uma imagem sem */}
        {/* driver, e dizer isso seria uma afirmação falsa sobre o produto. */}
        {catalogo === null && <p className="texto-suave">{t("catalogo_indisponivel")}</p>}
        {catalogo !== null && catalogo.length === 0 && (
          <p className="texto-suave">{t("catalogo_vazio")}</p>
        )}
        {catalogo !== null && catalogo.length > 0 && (
          <form className="formulario" onSubmit={(evento) => void enviar(evento)}>
            <label htmlFor="cadastro-tipo">{t("cadastro_tipo")}</label>
            <select
              id="cadastro-tipo"
              name="tipo"
              required
              value={formulario.tipo}
              onChange={(evento) => escolherTipo(evento.target.value)}
            >
              <option value="">{t("cadastro_escolher")}</option>
              {catalogo.map((candidato) => (
                <option key={candidato.tipo} value={candidato.tipo}>
                  {rotuloDoTipo(candidato, idioma, candidato.tipo)}
                </option>
              ))}
            </select>
            {item !== undefined && (
              <p className="dica">{textoDoManifesto(item, idioma, "descricao")}</p>
            )}
            <FormularioEquipamento
              item={item}
              idioma={idioma}
              formulario={formulario}
              guardados={[]}
              prefixo="cadastro"
              fixarIdentidade={false}
              aoMudar={setFormulario}
            />
            {erro !== null && (
              <p className="erro" role="alert">
                {traduzirErro(erro.codigo)}
                {erro.campo && ` (${rotuloDoCampo(item, idioma, erro.campo)})`}
              </p>
            )}
            {pronto && (
              <p className="sucesso" role="status">
                {t("cadastro_ok")}
              </p>
            )}
            <button type="submit" className="botao" disabled={enviando}>
              {enviando ? t("enviando") : t("cadastro_enviar")}
            </button>
          </form>
        )}
      </section>
    </>
  );
}

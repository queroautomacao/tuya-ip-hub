// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import { useEffect, useState, type FormEvent } from "react";
import FormularioEquipamento, { rotuloDoCampo } from "./FormularioEquipamento.tsx";
import { cadastrarEquipamento, codigoDoErro, lerRede, listarAparelhosDaConta, type AparelhoDaConta, varrer } from "./api.ts";
import {
  faixaSugerida,
  ordenarAchados,
  rotuloDoTipo,
  textoDoManifesto,
  type Achado,
  type ItemCatalogo,
} from "./equipamentos.ts";
import {
  VAZIO,
  ofertaDoAchado,
  padroes,
  validarCadastro,
  type Formulario,
  type OfertaAchado, NIVEL_MAXIMO } from "./formulario.ts";
import { t, traduzirErro, type Idioma } from "./i18n";

function Oferta({ oferta, aoPreencher }: { oferta: OfertaAchado; aoPreencher: () => void }) {
  // A device that answered without an identity cannot be prefilled, because the key
  // of a registration is the identity and the operator has no way to invent it; saying so
  // is honest, offering a form they cannot finish is not.
  if (oferta === "sem_tipo") return <p className="discreto">{t("descoberta_sem_tipo")}</p>;
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
                : achado.nome || t("descoberta_aparelho")}
            </p>
            <p className="texto-suave">
              {achado.porta === null ? achado.ip : `${achado.ip}:${achado.porta}`}
              {achado.tipo && achado.nome ? ` (${achado.nome})` : ""}
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
  enderecos = [],
}: {
  catalogo: ItemCatalogo[] | null;
  idioma: Idioma;
  aoCadastrar: () => void;
  enderecos?: readonly string[];
}) {
  const [formulario, setFormulario] = useState<Formulario>(VAZIO);
  const [erro, setErro] = useState<{ codigo: string; campo: string } | null>(null);
  const [pronto, setPronto] = useState(false);
  const [enviando, setEnviando] = useState(false);
  const [achados, setAchados] = useState<Achado[] | null>(null);
  const [varrendo, setVarrendo] = useState(false);
  const [erroVarredura, setErroVarredura] = useState<string | null>(null);
  const [faixa, setFaixa] = useState("");
  const [faixaTocada, setFaixaTocada] = useState(false);
  const [faixasDoHub, setFaixasDoHub] = useState<string[]>([]);
  useEffect(() => {
    let vivo = true;
    lerRede()
      .then((faixas) => {
        if (vivo) setFaixasDoHub(faixas);
      })
      .catch(() => undefined);
    return () => {
      vivo = false;
    };
  }, []);
  // The suggestion follows what arrives (the list of equipment, the networks of the host)
  // until the operator types a range of their own.
  const chaveDosEnderecos = enderecos.join(",");
  useEffect(() => {
    if (faixaTocada) return;
    const sugerida = faixaSugerida(window.location.hostname, enderecos, faixasDoHub);
    if (sugerida) setFaixa(sugerida);
  }, [chaveDosEnderecos, faixasDoHub, faixaTocada]);
  const [aparelhos, setAparelhos] = useState<AparelhoDaConta[] | null>(null);
  const [buscando, setBuscando] = useState(false);
  const [erroDaConta, setErroDaConta] = useState<string | null>(null);

  const lidos = catalogo ?? [];
  const item = lidos.find((candidato) => candidato.tipo === formulario.tipo);

  async function procurar(): Promise<void> {
    setVarrendo(true);
    try {
      setAchados(ordenarAchados(await varrer(faixa)));
      setErroVarredura(null);
    } catch (falha) {
      setErroVarredura(codigoDoErro(falha));
    } finally {
      setVarrendo(false);
    }
  }

  async function buscarNaConta(campo: string): Promise<void> {
    setBuscando(true);
    try {
      const lidos = await listarAparelhosDaConta(formulario.tipo, formulario.campos);
      setAparelhos(lidos.aparelhos);
      setErroDaConta(null);
      // One device in the account has no choice to make, so it is chosen, and the
      // operator is left with a name on the screen instead of a list of one.
      if (lidos.aparelhos.length === 1) escolherDaConta(campo, lidos.aparelhos[0]);
    } catch (falha) {
      setAparelhos(null);
      setErroDaConta(codigoDoErro(falha));
    } finally {
      setBuscando(false);
    }
  }

  function escolherDaConta(campo: string, aparelho: AparelhoDaConta): void {
    setErro(null);
    setPronto(false);
    setFormulario((atual) => ({
      ...atual,
      // The identity is the key of the registration and the operator has nothing better
      // to key it by than what the account calls this device; he can still rename it.
      identidade: atual.identidade === "" ? aparelho.id : atual.identidade,
      nome: atual.nome === "" ? aparelho.nome : atual.nome,
      campos: { ...atual.campos, [campo]: aparelho.id },
    }));
  }

  function escolherTipo(tipo: string): void {
    const escolhido = lidos.find((candidato) => candidato.tipo === tipo);
    setPronto(false);
    setAparelhos(null);
    setErroDaConta(null);
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
      nivel_maximo: String(NIVEL_MAXIMO),
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
        {/* Multicast does not cross a bridge network, so on a hub inside a container the */}
        {/* Button alone answers "nothing here" on a segment full of equipment. Naming the */}
        {/* Range turns the sweep into one question per address, which crosses. It stays */}
        {/* Optional because a hub on the network of the installation needs nothing typed. */}
        <div className="linha-de-varredura">
          <label htmlFor="descoberta-faixa">{t("descoberta_faixa")}</label>
          <input
            id="descoberta-faixa"
            name="faixa"
            type="text"
            inputMode="numeric"
            placeholder="192.168.1.0/24"
            value={faixa}
            disabled={varrendo}
            onChange={(evento) => {
              setFaixaTocada(true);
              setFaixa(evento.target.value);
            }}
          />
          <button
            type="button"
            className="botao secundario"
            disabled={varrendo}
            onClick={() => void procurar()}
          >
            {varrendo ? t("descoberta_varrendo") : t("descoberta_varrer")}
          </button>
        </div>
        <p className="dica">{t("descoberta_faixa_dica")}</p>
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
        {/* A catalog the panel never managed to read is not an image without */}
        {/* Drivers, and saying so would be a false statement about the product. */}
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
            {/* The device of a cloud driver lives inside an account and is named by an id */}
            {/* That exists nowhere else, so the panel asks the account and the operator picks */}
            {/* From what it answered. Typing an id copied out of a log is the step this */}
            {/* Removes; the credentials for the question are the ones already on the screen. */}
            {item !== undefined && item.lista_da_conta !== "" && (
              <div className="conta">
                <button
                  type="button"
                  className="botao secundario"
                  disabled={buscando}
                  onClick={() => void buscarNaConta(item.lista_da_conta)}
                >
                  {buscando ? t("conta_buscando") : t("conta_buscar")}
                </button>
                {erroDaConta !== null && (
                  <p className="erro" role="alert">
                    {traduzirErro(erroDaConta)}
                  </p>
                )}
                {aparelhos !== null && aparelhos.length === 0 && (
                  <p className="texto-suave">{t("conta_vazia")}</p>
                )}
                {aparelhos !== null && aparelhos.length > 0 && (
                  <ul className="conta-aparelhos">
                    {aparelhos.map((aparelho) => (
                      <li key={aparelho.id}>
                        <button
                          type="button"
                          className="botao secundario"
                          aria-pressed={formulario.campos[item.lista_da_conta] === aparelho.id}
                          onClick={() => escolherDaConta(item.lista_da_conta, aparelho)}
                        >
                          <b>{aparelho.nome || aparelho.id}</b>
                          <code>{aparelho.id}</code>
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            )}
            {erro !== null && (
              <p className="erro" role="alert">
                {traduzirErro(erro.codigo)}
                {erro.campo && ` (${rotuloDoCampo(erro.campo)})`}
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

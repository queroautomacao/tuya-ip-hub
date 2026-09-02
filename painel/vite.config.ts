// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Why: in development the daemon runs on its own port, so the dev server
// proxies its routes to keep every fetch same-origin (the Origin rule of
// section 9 rejects anything else).
// Por que: em desenvolvimento o daemon roda na própria porta, então o servidor
// de dev faz proxy das rotas dele para manter todo fetch na mesma origem (a
// regra de Origin da seção 9 recusa qualquer outra coisa).
export default defineConfig({
  plugins: [react()],
  // Why: the minifier strips the license headers of the bundled dependencies,
  // so the build writes dist/.vite/license.md and the image ships that notice.
  // Por que: o minificador remove os cabeçalhos de licença das dependências
  // empacotadas, então o build escreve dist/.vite/license.md e a imagem leva
  // esse aviso.
  build: { outDir: "dist", license: true },
  server: {
    proxy: {
      "/health": "http://127.0.0.1:8080",
      "/api": "http://127.0.0.1:8080",
    },
  },
});

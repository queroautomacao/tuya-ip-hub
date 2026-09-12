// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// In development the daemon runs on its own port, so the dev server
// proxies its routes to keep every fetch same-origin (the Origin rule of
// rejects anything else).
export default defineConfig({
  plugins: [react()],
  // Relative, and not the default "/": the panel is served at the root of the hub on the
  // local network AND under a path of its own through the relay of the remote access
  // (/h/<endereco>/). With an absolute base, every asset of a remote session would be asked
  // for at the root of the relay, which answers 404, and the page would load black.
  base: "./",
  // The minifier strips the license headers of the bundled dependencies,
  // so the build writes dist/.vite/license.md and the image ships that notice.
  build: { outDir: "dist", license: true },
  server: {
    proxy: {
      "/health": "http://127.0.0.1:8080",
      "/api": "http://127.0.0.1:8080",
    },
  },
});

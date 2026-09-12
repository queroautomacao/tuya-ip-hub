// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 Quero Automação Ltda

// The file of a logo is named by the brand, and a name written by a person is not a file
// name: it carries case, accents and spaces. One rule turns "LG" and "Sony Bravia" into "lg"
// and "sony-bravia", and the same rule names the file, so nobody has to guess the spelling.
// This lives apart from logos.ts because that one uses a feature of the bundler that does not
// exist outside a build, and a rule this simple deserves a test that runs anywhere.
export function apelidoDa(marca: string): string {
  return marca
    .normalize("NFD")
    .replace(/[\u0300-\u036f]/g, "")
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "");
}

import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  site: 'https://Fabio3rs.github.io/BibliothecaPatristica',
  base: '/BibliothecaPatristica',
  trailingSlash: 'never',
  i18n: {
    defaultLocale: 'pt-br',
    locales: ['pt-br', 'en'],
    // Sem prefixo de URL para o locale padrão (pt-br permanece em /)
    routing: {
      prefixDefaultLocale: false,
    },
  },
});

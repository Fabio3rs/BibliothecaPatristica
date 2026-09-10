import { defineConfig } from 'astro/config';

export default defineConfig({
  output: 'static',
  site: 'https://Fabio3rs.github.io/BibliothecaPatristica',
  base: '/BibliothecaPatristica',
  prefetch: true,
  trailingSlash: 'always',
  i18n: {
    defaultLocale: 'pt-br',
    locales: ['pt-br', 'en', 'it', 'fr'],
    // Sem prefixo de URL para o locale padrão (pt-br permanece em /)
    routing: {
      prefixDefaultLocale: false,
    },
  },
});

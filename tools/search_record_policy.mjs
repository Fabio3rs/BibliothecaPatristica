function normalizeSummary(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .replace(/\s+/g, ' ')
    .trim()
    .toLocaleLowerCase('pt-BR');
}

export function isAdministrativePage(page) {
  return normalizeSummary(page?.summary_page) === 'conteudo administrativo';
}

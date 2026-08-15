import assert from 'node:assert/strict';
import test from 'node:test';

import { isAdministrativePage } from './search_record_policy.mjs';

test('exclui resumo que contém somente a etiqueta administrativa', () => {
  assert.equal(isAdministrativePage({ summary_page: 'Conteúdo administrativo' }), true);
  assert.equal(isAdministrativePage({ summary_page: '  CONTEUDO\nADMINISTRATIVO  ' }), true);
});

test('preserva resumos substantivos que mencionam conteúdo administrativo', () => {
  assert.equal(
    isAdministrativePage({
      summary_page: 'A página contém conteúdo administrativo e descreve uma carta episcopal.',
    }),
    false,
  );
});

test('preserva páginas sem a etiqueta administrativa', () => {
  assert.equal(isAdministrativePage({ summary_page: '' }), false);
  assert.equal(isAdministrativePage({}), false);
});

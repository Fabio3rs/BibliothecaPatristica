import test from 'node:test';
import assert from 'node:assert/strict';

import {
  deriveObservedMetrics,
  mechanicalEvaluation,
  validateSchemaValue,
  validateToolArguments,
} from '../../scripts/playgrounds/agent_rag_benchmark.mjs';

const definitions = [{
  type: 'function',
  function: {
    name: 'search_indices',
    parameters: {
      type: 'object',
      properties: {
        query: { type: 'string', maxLength: 200 },
        limit: { type: 'integer', minimum: 1, maximum: 10 },
      },
      required: ['query'],
      additionalProperties: false,
    },
  },
}, {
  type: 'function',
  function: {
    name: 'get_page_ocr',
    parameters: {
      type: 'object',
      properties: {
        volume_id: { type: 'string' },
        page: { type: 'integer', minimum: 1 },
        query: { type: 'string' },
      },
      required: ['volume_id', 'page'],
      additionalProperties: false,
    },
  },
}];

test('validates provider tool arguments against bounds and additional properties', () => {
  assert.deepEqual(validateToolArguments('search_indices', { query: 'Clemens', limit: 10 }, definitions), {
    valid: true,
    errors: [],
  });
  const invalid = validateToolArguments('search_indices', { query: 'Clemens', limit: 50, volume_id: 'PG001' }, definitions);
  assert.equal(invalid.valid, false);
  assert.match(invalid.errors.join('\n'), /above 10/);
  assert.match(invalid.errors.join('\n'), /volume_id is not allowed/);
  assert.deepEqual(validateSchemaValue(['a'], { oneOf: [{ type: 'string' }, { type: 'array', maxItems: 2 }] }), []);
});

test('derives partial rounds, usage and tool calls after a loop error', () => {
  const run = {
    status: 'error',
    metrics: { elapsed_ms: 123, rounds: 0, tool_calls: 0, usage: [] },
    tool_trace: [{ tool: 'search_indices' }, { tool: 'search_indices' }],
    events: [
      { type: 'response', payload: { body: { usage: { prompt_tokens: 100, completion_tokens: 10, total_tokens: 110 } } } },
      { type: 'response', payload: { usage: { prompt_tokens: 150, completion_tokens: 15, total_tokens: 165 } } },
    ],
  };
  assert.deepEqual(deriveObservedMetrics(run), {
    elapsed_ms: 123,
    rounds: 2,
    tool_calls: 2,
    usage_total: { prompt_tokens: 250, completion_tokens: 25, total_tokens: 275 },
    partial: true,
  });
});

test('mechanical evaluation catches unsupported authenticity claims and schema violations', () => {
  const caseSpec = {
    expect: {
      acceptable_outcome: 'refuse_unsupported_authenticity',
      must_use_any: ['search_indices', 'get_volume_index'],
    },
  };
  const baseRun = {
    turns: [{ answer: 'Não é possível provar a autenticidade histórica apenas com resumo e OCR.', sources: [] }],
    tool_trace: [{
      tool: 'search_indices',
      arguments: { query: 'Clemens', limit: 50 },
      result: { ok: true, data: { items: [] } },
    }],
  };
  const refused = mechanicalEvaluation(caseSpec, baseRun, definitions);
  assert.equal(refused.pass, false);
  assert.equal(refused.checks.find((check) => check.id.startsWith('acceptable_outcome:')).pass, true);
  assert.equal(refused.checks.find((check) => check.id === 'valid_tool_arguments').pass, false);

  const asserted = mechanicalEvaluation(caseSpec, {
    ...baseRun,
    turns: [{ answer: 'A autoria é historicamente autêntica pois o cabeçalho a declara.', sources: [] }],
    tool_trace: [{ ...baseRun.tool_trace[0], arguments: { query: 'Clemens', limit: 10 } }],
  }, definitions);
  assert.equal(asserted.checks.find((check) => check.id.startsWith('acceptable_outcome:')).pass, false);
});

test('mechanical evaluation detects source id collisions across user turns', () => {
  const run = {
    turns: [
      { answer: 'Primeira [s1].', sources: [{ citationId: 's1', id: 'page:PG001:18' }] },
      { answer: 'Segunda [s1].', sources: [{ citationId: 's1', id: 'page:PL016:250' }] },
    ],
    tool_trace: [],
  };
  const evaluation = mechanicalEvaluation({ expect: { stable_source_ids: true } }, run, definitions);
  assert.equal(evaluation.checks.find((check) => check.id === 'stable_source_ids').pass, false);
});

const DEFAULT_MAX_QUERIES = 4;
const DEFAULT_MAX_QUERY_CHARS = 200;
const DEFAULT_MAX_TOTAL_CHARS = 600;
const DEFAULT_RRF_K = 60;

function queryError(message) {
  const error = new Error(message);
  error.code = 'invalid_arguments';
  return error;
}

function normalizeOne(value, field, maxChars) {
  if (typeof value !== 'string') throw queryError(`${field} must contain only strings.`);
  const normalized = value.normalize('NFC').replace(/\s+/g, ' ').trim();
  return normalized.slice(0, maxChars);
}

export function normalizeQuerySet(input = {}, options = {}) {
  const maxQueries = Number.isInteger(options.maxQueries) ? options.maxQueries : DEFAULT_MAX_QUERIES;
  const maxQueryChars = Number.isInteger(options.maxQueryChars) ? options.maxQueryChars : DEFAULT_MAX_QUERY_CHARS;
  const maxTotalChars = Number.isInteger(options.maxTotalChars) ? options.maxTotalChars : DEFAULT_MAX_TOTAL_CHARS;
  const primary = Array.isArray(input.query) ? input.query : [input.query];
  const alternatives = input.alternatives == null ? [] : input.alternatives;
  if (!Array.isArray(alternatives)) throw queryError('alternatives must be an array of strings.');

  const raw = [...primary, ...alternatives];
  if (raw.some((entry) => Array.isArray(entry) || (entry != null && typeof entry !== 'string'))) {
    throw queryError('query and alternatives must contain only strings.');
  }

  const queries = [];
  const seen = new Set();
  let totalChars = 0;
  let truncated = false;
  for (const [index, entry] of raw.entries()) {
    const normalized = normalizeOne(entry ?? '', index < primary.length ? 'query' : 'alternatives', maxQueryChars);
    if (!normalized || seen.has(normalized)) continue;
    if (queries.length >= maxQueries || totalChars + normalized.length > maxTotalChars) {
      truncated = true;
      continue;
    }
    queries.push(normalized);
    seen.add(normalized);
    totalChars += normalized.length;
  }
  if (!queries.length) throw queryError('query is required.');
  return { queries, strategy: queries.length > 1 ? 'rank_fusion' : 'single_query', truncated };
}

export function fuseRankedResults(resultSets, options = {}) {
  const k = Number.isFinite(options.k) ? Number(options.k) : DEFAULT_RRF_K;
  const keyOf = typeof options.keyOf === 'function' ? options.keyOf : (item) => String(item?.url || '');
  const fused = new Map();

  for (const [queryIndex, resultSet] of (resultSets || []).entries()) {
    for (const [rankIndex, item] of (resultSet || []).entries()) {
      const key = keyOf(item);
      if (!key) continue;
      const current = fused.get(key) || {
        key,
        item,
        rrfScore: 0,
        queryIndexes: [],
        bestRank: Number.POSITIVE_INFINITY,
      };
      const rank = rankIndex + 1;
      current.rrfScore += 1 / (k + rank);
      current.queryIndexes.push(queryIndex);
      current.bestRank = Math.min(current.bestRank, rank);
      fused.set(key, current);
    }
  }

  return [...fused.values()].sort((left, right) => (
    right.rrfScore - left.rrfScore
    || right.queryIndexes.length - left.queryIndexes.length
    || left.bestRank - right.bestRank
    || left.key.localeCompare(right.key)
  ));
}

export const QUERY_STRATEGY_LIMITS = Object.freeze({
  maxQueries: DEFAULT_MAX_QUERIES,
  maxQueryChars: DEFAULT_MAX_QUERY_CHARS,
  maxTotalChars: DEFAULT_MAX_TOTAL_CHARS,
  rrfK: DEFAULT_RRF_K,
});

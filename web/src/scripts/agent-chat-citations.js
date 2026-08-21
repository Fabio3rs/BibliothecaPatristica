export const SOURCE_CITATION_GROUP_PATTERN_SOURCE = String.raw`\[\s*[sS]\d+(?:\s*[,;]\s*[sS]\d+)*\s*\]`;

const SOURCE_CITATION_GROUP_PATTERN = new RegExp(`^${SOURCE_CITATION_GROUP_PATTERN_SOURCE}$`);

export function parseSourceCitationGroup(value) {
  const group = String(value || '');
  if (!SOURCE_CITATION_GROUP_PATTERN.test(group)) return [];
  return [...group.matchAll(/[sS]\d+/g)].map((match) => match[0].toLowerCase());
}

export function sourceCitationIds(value) {
  const ids = new Set();
  const pattern = new RegExp(SOURCE_CITATION_GROUP_PATTERN_SOURCE, 'g');
  for (const match of String(value || '').matchAll(pattern)) {
    for (const sourceId of parseSourceCitationGroup(match[0])) ids.add(sourceId);
  }
  return ids;
}

export function sanitizeSourceCitationGroups(value, allowedSourceIds) {
  const allowed = allowedSourceIds instanceof Set
    ? allowedSourceIds
    : new Set(allowedSourceIds || []);
  const pattern = new RegExp(SOURCE_CITATION_GROUP_PATTERN_SOURCE, 'g');
  return String(value || '').replace(pattern, (group) => {
    const validIds = [...new Set(
      parseSourceCitationGroup(group).filter((sourceId) => allowed.has(sourceId)),
    )];
    return validIds.length ? `[${validIds.join(', ')}]` : '';
  });
}

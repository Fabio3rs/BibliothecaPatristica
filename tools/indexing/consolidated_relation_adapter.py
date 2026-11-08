"""Expose literal repaired/new catalog groups without reviving superseded owners."""
import json


def metadata_layers(metadata):
    """Repairs preserve their predecessor, but current fields always win."""
    layers = []
    while isinstance(metadata, dict):
        layers.append(metadata)
        metadata = metadata.get('original_item_metadata')
    return layers


def adapted_groups(groups, items):
    """Keep survey barriers and add source-spanned clauses from the catalog.

    The parser selection/run identifier is deliberately not a section identifier.
    Unknown owners remain barriers to cross-group inheritance.
    """
    result = {g['id']: dict(g) for g in groups}
    for ident, records in items.items():
        repairs = []
        for item in records:
            metadata = json.loads(item['metadata_json'])
            layers = metadata_layers(metadata)
            entry = metadata.get('entry')
            parser = metadata.get('parser_result', {})
            index = parser.get('existing_reference_entry')
            if entry is None and isinstance(index, int) and 0 <= index < len(parser.get('entries', [])):
                entry = parser['entries'][index]
            if entry is not None:
                repairs.append((item, metadata, entry))
            if ident in result:
                if metadata.get('reference_display_is_contextualized'):
                    # A bare old number must not lose its restored Roman context.
                    result[ident]['relation_context_blocked'] = 'contextualized_reference_requires_specialized_map'
                continue
            span = metadata.get('source_span')
            if not (isinstance(span, list) and len(span) == 2
                    and all(isinstance(n, int) for n in span) and 0 <= span[0] < span[1]):
                continue
            parent = result.get(metadata.get('parent_group_id'), {})
            g = {key: parent[key] for key in ('profile', 'section_id', 'section_candidate', 'physical') if key in parent}
            g.update({key: metadata[key] for key in ('profile', 'section_id', 'section_candidate', 'physical')
                      if metadata.get(key) is not None})
            g.update(id=ident, source=item['source'], volume=item['volume'],
                     char_start=span[0], char_end=span[1], raw=item['ref_raw'],
                     source_sha256=next((m['source_sha256'] for m in layers if m.get('source_sha256')), ''),
                     owner_raw='', status='segmentation_or_notation_review',
                     flags=['catalog_clause_segmentation_unverified'], catalog_context=True)
            result[ident] = g
        if ident not in result:
            continue
        g = result[ident]
        g['catalog_context'] = True
        if repairs:
            signatures = {(entry.get('owner_raw'), tuple(entry.get('owner_span') or ()))
                          for _, _, entry in repairs}
            if len(signatures) != 1:
                g['relation_context_blocked'] = 'conflicting_repaired_owners'
            else:
                _, _, entry = repairs[0]
                g.update(owner_raw=entry.get('owner_raw', ''), owner_span=entry.get('owner_span'),
                         owner_span_required=True)
    return list(result.values())


def owner_is_known(group, raw):
    owner = group.get('owner_raw', '')
    if not isinstance(owner, str) or not owner.strip() or not any(c.isalpha() for c in owner):
        return False
    if group.get('owner_span_required'):
        span = group.get('owner_span')
        return (isinstance(span, list) and len(span) == 2
                and all(isinstance(n, int) for n in span)
                and 0 <= span[0] < span[1] <= group['char_start']
                and raw[span[0]:span[1]] == owner)
    return True


def context_boundary(raw, previous, current):
    if previous is None:
        return 'no_local_antecedent'
    for group in (previous, current):
        if group.get('relation_context_blocked'):
            return group['relation_context_blocked']
        if not owner_is_known(group, raw):
            return 'unknown_or_changed_owner'
    for key in ('profile', 'section_id', 'section_candidate'):
        if previous.get(key) != current.get(key):
            return 'editorial_scope_boundary'
    return None

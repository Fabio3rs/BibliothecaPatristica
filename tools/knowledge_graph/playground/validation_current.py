"""New contracts opt in without changing historical validation fingerprints."""
from .validation_dispatch import POLICIES as HISTORICAL_POLICIES, evaluate_response as historical_response
from .evidence_locator_v6 import POLICY as DEFAULT_POLICY

POLICIES = (*HISTORICAL_POLICIES, DEFAULT_POLICY)


def evaluate_response(source, result, prepared, *, matching_policy=None):
    navigation = prepared['schema']['properties']['schema_version'].get('const') == 'kg-navigation-v1'
    policy = matching_policy or prepared.get('validation_policy', 'local-quote-v5' if navigation else 'local-quote-v2')
    segmented = policy == DEFAULT_POLICY
    if segmented and prepared.get('task') in {'statement_review', 'stance_review'}:
        raise ValueError('Review experiments must preserve their frozen matching policy')
    if prepared['schema']['properties']['schema_version'].get('const') == 'kg-navigation-v1':
        from .navigation_contract import evaluate_response as navigation_response
        checked = navigation_response(source, result, prepared, matching_policy='local-quote-v5' if segmented else matching_policy)
    else:
        checked = historical_response(source, result, prepared, matching_policy='local-quote-v5' if segmented else matching_policy)
    if segmented:
        from .validation_segmented import upgrade
        checked = upgrade(source, checked)
    if prepared['schema']['properties']['schema_version'].get('const') == 'kg-extraction-v3.1':
        from .discourse_contract import validate_discourse
        return validate_discourse(source, checked, mode=prepared['mode'])
    return checked

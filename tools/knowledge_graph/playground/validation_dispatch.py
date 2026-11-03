"""Select a policy without changing implementations hashed by frozen studies."""
from .evidence_locator_v5 import POLICY as DEFAULT_POLICY

POLICIES = ('local-quote-v2', 'local-quote-v3', 'local-quote-v4', DEFAULT_POLICY)


def evaluate_response(source, result, prepared, *, matching_policy=None):
    policy = matching_policy or prepared.get('validation_policy', 'local-quote-v2')
    if policy not in POLICIES:
        raise ValueError('Unknown evidence matching policy')
    if prepared.get('task') == 'statement_review':
        from .statement_review_contract import evaluate_review
        if policy != prepared['validation_policy']:
            raise ValueError('Statement review must preserve its frozen matching policy')
        return evaluate_review(source, result, prepared)
    if policy in ('local-quote-v4', DEFAULT_POLICY):
        from . import validation_location, validation_micro
        module = validation_location if policy == DEFAULT_POLICY else validation_micro
        evaluator = module.evaluate_review if prepared.get('task') == 'stance_review' else module.evaluate_response
        checked = evaluator(source, result, prepared)
        from .statement_contract import validate_roles
        return validate_roles(checked)
    if prepared.get('task') == 'stance_review':
        from .verification_contract import evaluate_review
        if policy != prepared['validation_policy']:
            raise ValueError('Review benchmark revalidation must preserve its frozen matching policy')
        return evaluate_review(source, result, prepared)
    from .validation import evaluate_response as frozen_response
    from .statement_contract import validate_roles
    return validate_roles(frozen_response(source, result, prepared, matching_policy=policy))

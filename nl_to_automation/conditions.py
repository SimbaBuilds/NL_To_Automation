"""
Condition evaluation for automation actions.
"""

import logging
from typing import Any, Dict

from .templates import get_nested_value, resolve_template

logger = logging.getLogger(__name__)


def compare_values(actual: Any, op: str, expected: Any) -> bool:
    """
    Compare two values using the specified operator.

    Supported operators:
    - Comparison: <, >, <=, >=, ==, !=
    - String: contains, not_contains, starts_with, ends_with
    - Existence: exists, not_exists
    """
    # Handle existence operators first
    if op == 'exists':
        return actual is not None
    elif op == 'not_exists':
        return actual is None

    # For other operators, handle None values
    if actual is None:
        return False

    # Type coercion for numeric comparisons
    if op in ('<', '>', '<=', '>='):
        try:
            actual = float(actual)
            expected = float(expected)
        except (TypeError, ValueError):
            logger.warning(f"Cannot compare non-numeric values: {actual} {op} {expected}")
            return False

    # Comparison operators
    if op == '<':
        return actual < expected
    elif op == '>':
        return actual > expected
    elif op == '<=':
        return actual <= expected
    elif op == '>=':
        return actual >= expected
    elif op == '==' or op == 'eq':
        return actual == expected
    elif op == '!=' or op == 'neq':
        return actual != expected

    # String operators
    elif op == 'contains':
        return str(expected).lower() in str(actual).lower()
    elif op == 'not_contains':
        return str(expected).lower() not in str(actual).lower()
    elif op == 'starts_with':
        return str(actual).lower().startswith(str(expected).lower())
    elif op == 'ends_with':
        return str(actual).lower().endswith(str(expected).lower())

    else:
        logger.warning(f"Unknown comparison operator: {op}")
        return False


def evaluate_clause(clause: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """
    Evaluate a single condition clause.

    Clause format:
    {"path": "sleep_data.score", "op": "<", "value": 70}
    """
    path = clause.get('path', '')
    op = clause.get('op', '==')
    expected = clause.get('value')

    # Resolve expected value if it's a template
    if isinstance(expected, str):
        expected = resolve_template(expected, context)
        # Try to convert to number if it looks numeric
        try:
            if '.' in str(expected):
                expected = float(expected)
            else:
                expected = int(expected)
        except (TypeError, ValueError):
            pass

    actual = get_nested_value(context, path)

    return compare_values(actual, op, expected)


def evaluate_condition(condition: Dict[str, Any], context: Dict[str, Any]) -> bool:
    """
    Evaluate a structured condition against the execution context.

    Condition formats:

    1. Single clause (no operator needed):
       {"path": "sleep_data.score", "op": "<", "value": 70}

    2. Multi-clause with operator:
       {
           "operator": "AND",
           "clauses": [
               {"path": "sleep_data.data[0].score", "op": "<", "value": 70},
               {"path": "sleep_data.data[1].score", "op": "<", "value": 70}
           ]
       }

    Args:
        condition: Condition dict
        context: Execution context with action outputs

    Returns:
        True if condition passes, False otherwise
    """
    if not condition:
        return True

    # Single clause format (has 'path' key)
    if 'path' in condition:
        return evaluate_clause(condition, context)

    # Multi-clause format (has 'clauses' key)
    operator = condition.get('operator', 'AND').upper()
    clauses = condition.get('clauses', [])

    if not clauses:
        return True

    if operator == 'AND':
        return all(evaluate_clause(c, context) for c in clauses)
    elif operator == 'OR':
        return any(evaluate_clause(c, context) for c in clauses)
    else:
        logger.warning(f"Unknown logical operator: {operator}")
        return False

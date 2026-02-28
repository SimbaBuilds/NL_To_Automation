"""Tests for condition evaluation."""

import pytest

from nl_to_automation.conditions import (
    compare_values,
    evaluate_clause,
    evaluate_condition,
)


class TestCompareValues:
    """Tests for compare_values function."""

    def test_numeric_less_than(self):
        """Test numeric < comparison."""
        assert compare_values(5, '<', 10) is True
        assert compare_values(10, '<', 5) is False
        assert compare_values(5, '<', 5) is False

    def test_numeric_greater_than(self):
        """Test numeric > comparison."""
        assert compare_values(10, '>', 5) is True
        assert compare_values(5, '>', 10) is False
        assert compare_values(5, '>', 5) is False

    def test_numeric_less_than_or_equal(self):
        """Test numeric <= comparison."""
        assert compare_values(5, '<=', 10) is True
        assert compare_values(5, '<=', 5) is True
        assert compare_values(10, '<=', 5) is False

    def test_numeric_greater_than_or_equal(self):
        """Test numeric >= comparison."""
        assert compare_values(10, '>=', 5) is True
        assert compare_values(5, '>=', 5) is True
        assert compare_values(5, '>=', 10) is False

    def test_equality(self):
        """Test == comparison."""
        assert compare_values(5, '==', 5) is True
        assert compare_values('hello', '==', 'hello') is True
        assert compare_values(5, '==', 10) is False
        assert compare_values('hello', '==', 'world') is False

    def test_inequality(self):
        """Test != comparison."""
        assert compare_values(5, '!=', 10) is True
        assert compare_values('hello', '!=', 'world') is True
        assert compare_values(5, '!=', 5) is False

    def test_contains(self):
        """Test contains operator (case-insensitive)."""
        assert compare_values('Hello World', 'contains', 'world') is True
        assert compare_values('Hello World', 'contains', 'WORLD') is True
        assert compare_values('Hello World', 'contains', 'xyz') is False

    def test_not_contains(self):
        """Test not_contains operator."""
        assert compare_values('Hello World', 'not_contains', 'xyz') is True
        assert compare_values('Hello World', 'not_contains', 'world') is False

    def test_starts_with(self):
        """Test starts_with operator (case-insensitive)."""
        assert compare_values('Hello World', 'starts_with', 'hello') is True
        assert compare_values('Hello World', 'starts_with', 'HELLO') is True
        assert compare_values('Hello World', 'starts_with', 'World') is False

    def test_ends_with(self):
        """Test ends_with operator (case-insensitive)."""
        assert compare_values('Hello World', 'ends_with', 'world') is True
        assert compare_values('Hello World', 'ends_with', 'WORLD') is True
        assert compare_values('Hello World', 'ends_with', 'Hello') is False

    def test_exists(self):
        """Test exists operator."""
        assert compare_values('something', 'exists', None) is True
        assert compare_values(0, 'exists', None) is True
        assert compare_values(None, 'exists', None) is False

    def test_not_exists(self):
        """Test not_exists operator."""
        assert compare_values(None, 'not_exists', None) is True
        assert compare_values('something', 'not_exists', None) is False

    def test_none_handling(self):
        """Test that None values return False for most operators."""
        assert compare_values(None, '<', 10) is False
        assert compare_values(None, '>', 10) is False
        assert compare_values(None, '==', 10) is False


class TestEvaluateClause:
    """Tests for evaluate_clause function."""

    def test_simple_clause(self):
        """Test evaluating a simple clause."""
        clause = {'path': 'score', 'op': '>', 'value': 70}
        context = {'score': 85}
        assert evaluate_clause(clause, context) is True

        context = {'score': 50}
        assert evaluate_clause(clause, context) is False

    def test_nested_path_clause(self):
        """Test clause with nested path."""
        clause = {'path': 'user.score', 'op': '>=', 'value': 70}
        context = {'user': {'score': 85}}
        assert evaluate_clause(clause, context) is True

    def test_template_in_value(self):
        """Test clause with template variable in value."""
        clause = {'path': 'current_score', 'op': '>', 'value': '{{threshold}}'}
        context = {'current_score': 85, 'threshold': 70}
        assert evaluate_clause(clause, context) is True

    def test_string_comparison(self):
        """Test string-based comparison."""
        clause = {'path': 'subject', 'op': 'contains', 'value': 'urgent'}
        context = {'subject': 'URGENT: Please review'}
        assert evaluate_clause(clause, context) is True


class TestEvaluateCondition:
    """Tests for evaluate_condition function."""

    def test_empty_condition_passes(self):
        """Test that empty/None condition passes."""
        assert evaluate_condition(None, {}) is True
        assert evaluate_condition({}, {}) is True

    def test_single_clause_condition(self):
        """Test condition with single clause."""
        condition = {'path': 'score', 'op': '<', 'value': 70}
        context = {'score': 50}
        assert evaluate_condition(condition, context) is True

        context = {'score': 85}
        assert evaluate_condition(condition, context) is False

    def test_and_condition(self):
        """Test AND condition with multiple clauses."""
        condition = {
            'operator': 'AND',
            'clauses': [
                {'path': 'score', 'op': '<', 'value': 70},
                {'path': 'user.name', 'op': 'exists'}
            ]
        }

        # Both clauses pass
        context = {'score': 50, 'user': {'name': 'Alice'}}
        assert evaluate_condition(condition, context) is True

        # First clause fails
        context = {'score': 85, 'user': {'name': 'Alice'}}
        assert evaluate_condition(condition, context) is False

        # Second clause fails
        context = {'score': 50, 'user': {}}
        assert evaluate_condition(condition, context) is False

    def test_or_condition(self):
        """Test OR condition with multiple clauses."""
        condition = {
            'operator': 'OR',
            'clauses': [
                {'path': 'priority', 'op': '==', 'value': 'high'},
                {'path': 'urgent', 'op': '==', 'value': True}
            ]
        }

        # First clause passes
        context = {'priority': 'high', 'urgent': False}
        assert evaluate_condition(condition, context) is True

        # Second clause passes
        context = {'priority': 'low', 'urgent': True}
        assert evaluate_condition(condition, context) is True

        # Both pass
        context = {'priority': 'high', 'urgent': True}
        assert evaluate_condition(condition, context) is True

        # Neither passes
        context = {'priority': 'low', 'urgent': False}
        assert evaluate_condition(condition, context) is False

    def test_complex_nested_condition(self):
        """Test complex condition with nested paths and multiple clauses."""
        condition = {
            'operator': 'AND',
            'clauses': [
                {'path': 'data.score', 'op': '>', 'value': 70},
                {'path': 'data.date', 'op': 'exists'},
                {'path': 'user.email', 'op': 'contains', 'value': '@example.com'}
            ]
        }

        context = {
            'data': {'score': 85, 'date': '2026-02-27'},
            'user': {'email': 'alice@example.com'}
        }
        assert evaluate_condition(condition, context) is True

        # Fails on score check
        context['data']['score'] = 60
        assert evaluate_condition(condition, context) is False

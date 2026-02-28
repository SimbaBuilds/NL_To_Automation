"""Tests for template resolution."""

import pytest
from datetime import datetime
from zoneinfo import ZoneInfo

from nl_to_automation.templates import (
    get_nested_value,
    resolve_template,
    resolve_parameters,
)


class TestGetNestedValue:
    """Tests for get_nested_value function."""

    def test_simple_dict_access(self):
        """Test accessing simple dict keys."""
        data = {'a': 1, 'b': 2}
        assert get_nested_value(data, 'a') == 1
        assert get_nested_value(data, 'b') == 2

    def test_nested_dict_access(self):
        """Test accessing nested dict keys."""
        data = {'user': {'name': 'Alice', 'age': 30}}
        assert get_nested_value(data, 'user.name') == 'Alice'
        assert get_nested_value(data, 'user.age') == 30

    def test_array_indexing_bracket_notation(self):
        """Test array access with bracket notation."""
        data = {'items': [{'id': 1}, {'id': 2}]}
        assert get_nested_value(data, 'items[0].id') == 1
        assert get_nested_value(data, 'items[1].id') == 2

    def test_array_indexing_dot_notation(self):
        """Test array access with dot notation."""
        data = {'items': [{'id': 1}, {'id': 2}]}
        assert get_nested_value(data, 'items.0.id') == 1
        assert get_nested_value(data, 'items.1.id') == 2

    def test_negative_indexing(self):
        """Test negative array indexing."""
        data = {'items': [1, 2, 3, 4]}
        assert get_nested_value(data, 'items.-1') == 4
        assert get_nested_value(data, 'items.-2') == 3

    def test_none_handling(self):
        """Test that None is returned for missing paths."""
        data = {'a': {'b': 1}}
        assert get_nested_value(data, 'a.c') is None
        assert get_nested_value(data, 'x.y.z') is None

    def test_per_item_mode_fallback(self):
        """Test fallback for per_item aggregation mode."""
        # When path expects array but data is single object
        data = {'subject': 'Test', 'score': 85}
        assert get_nested_value(data, '0.subject') == 'Test'
        assert get_nested_value(data, '0.score') == 85


class TestResolveTemplate:
    """Tests for resolve_template function."""

    def test_simple_variable_resolution(self):
        """Test resolving simple variables."""
        context = {'name': 'Alice', 'score': 85}
        result = resolve_template('Hello {{name}}, your score is {{score}}', context)
        assert result == 'Hello Alice, your score is 85'

    def test_nested_variable_resolution(self):
        """Test resolving nested variables."""
        context = {'user': {'name': 'Bob', 'email': 'bob@example.com'}}
        result = resolve_template('Email: {{user.email}}', context)
        assert result == 'Email: bob@example.com'

    def test_today_variable(self):
        """Test {{today}} resolves to current date."""
        context = {'user': {'timezone': 'America/New_York'}}
        result = resolve_template('Date: {{today}}', context)
        # Should be YYYY-MM-DD format
        assert len(result.split(': ')[1]) == 10
        assert result.split(': ')[1].count('-') == 2

    def test_yesterday_variable(self):
        """Test {{yesterday}} resolves correctly."""
        context = {'user': {'timezone': 'UTC'}}
        result = resolve_template('{{yesterday}}', context)
        # Should be YYYY-MM-DD format
        assert len(result) == 10
        assert result.count('-') == 2

    def test_missing_variable_handling(self):
        """Test handling of missing variables."""
        context = {'name': 'Alice'}
        result = resolve_template('Hello {{name}}, score: {{missing}}', context)
        assert result == 'Hello Alice, score: [No available data]'

    def test_complex_object_to_json(self):
        """Test that complex objects are converted to JSON."""
        context = {'data': {'items': [1, 2, 3]}}
        result = resolve_template('Data: {{data}}', context)
        assert '"items"' in result
        assert '[1, 2, 3]' in result or '[1,2,3]' in result

    def test_timezone_support(self):
        """Test that user timezone is respected."""
        context = {'user': {'timezone': 'America/Los_Angeles'}}
        result = resolve_template('{{today}}', context)
        # Result should be a valid date string
        assert len(result) == 10

    def test_utc_fallback(self):
        """Test UTC fallback when no timezone provided."""
        context = {}
        result = resolve_template('{{today}}', context)
        # Should still work, using UTC
        assert len(result) == 10


class TestResolveParameters:
    """Tests for resolve_parameters function."""

    def test_resolve_string_parameters(self):
        """Test resolving string parameters."""
        params = {'title': 'Hello {{name}}', 'body': 'Score: {{score}}'}
        context = {'name': 'Alice', 'score': 85}
        result = resolve_parameters(params, context)
        assert result == {'title': 'Hello Alice', 'body': 'Score: 85'}

    def test_resolve_nested_parameters(self):
        """Test resolving nested dict parameters."""
        params = {
            'message': {
                'title': 'Hello {{name}}',
                'body': 'Score: {{score}}'
            }
        }
        context = {'name': 'Bob', 'score': 90}
        result = resolve_parameters(params, context)
        assert result == {
            'message': {
                'title': 'Hello Bob',
                'body': 'Score: 90'
            }
        }

    def test_resolve_list_parameters(self):
        """Test resolving list parameters."""
        params = {
            'messages': ['Hello {{name}}', 'Goodbye {{name}}']
        }
        context = {'name': 'Charlie'}
        result = resolve_parameters(params, context)
        assert result == {
            'messages': ['Hello Charlie', 'Goodbye Charlie']
        }

    def test_preserve_non_string_values(self):
        """Test that non-string values are preserved."""
        params = {
            'count': 5,
            'enabled': True,
            'data': None
        }
        context = {}
        result = resolve_parameters(params, context)
        assert result == params

"""
Template variable resolution for automation parameters.
"""

import json
import logging
import re
from datetime import datetime, timedelta
from typing import Any, Dict
from zoneinfo import ZoneInfo

logger = logging.getLogger(__name__)


def get_nested_value(data: Any, path: str) -> Any:
    """
    Get a nested value from a dict/list using dot notation.
    Supports array indexing: 'data[0].score' or 'data.0.score'

    Also handles aggregation mode differences:
    - For 'per_item' mode: data is a single object, path like '0.field' skips the index
    - For 'latest' mode: data is an array, path like '0.field' accesses first element

    Examples:
        get_nested_value({'a': {'b': 1}}, 'a.b') -> 1
        get_nested_value({'data': [{'score': 70}]}, 'data[0].score') -> 70
        get_nested_value({'data': [{'score': 70}]}, 'data.0.score') -> 70
        get_nested_value({'subject': 'Test'}, '0.subject') -> 'Test'  # per_item fallback
    """
    if data is None:
        return None

    # Handle array notation like data[0].score -> data.0.score
    path = re.sub(r'\[(\d+)\]', r'.\1', path)

    parts = path.split('.')
    current = data

    i = 0
    while i < len(parts):
        part = parts[i]
        if current is None:
            return None

        # Try numeric index first (for lists), then dict key for spread arrays
        # Support negative indexing (e.g., -1 for last element)
        is_numeric = part.isdigit() or (part.startswith('-') and part[1:].isdigit())
        if is_numeric:
            idx = int(part)
            if isinstance(current, list):
                # Support negative indexing: -1 = last, -2 = second to last, etc.
                if -len(current) <= idx < len(current):
                    current = current[idx]
                else:
                    return None
            elif isinstance(current, dict) and part in current:
                # Handle case where array was spread into object with string keys "0", "1", etc.
                current = current[part]
            elif isinstance(current, dict) and idx == 0:
                # Fallback for per_item mode: path expects array but data is single object
                # Skip the index and continue with the same object
                # e.g., path='0.subject' but data={'subject': 'Test'} -> skip '0', try 'subject'
                i += 1
                continue
            else:
                return None
        # Then try dict key
        elif isinstance(current, dict):
            current = current.get(part)
        else:
            return None

        i += 1

    return current


def resolve_template(template: str, context: Dict[str, Any]) -> str:
    """
    Resolve {{variable}} placeholders in a template string.

    Built-in variables (date values use user's timezone, with UTC fallback):
    - {{today}} - Today's date in user's timezone (YYYY-MM-DD)
    - {{tomorrow}} - Tomorrow's date in user's timezone (YYYY-MM-DD)
    - {{yesterday}} - Yesterday's date in user's timezone (YYYY-MM-DD)
    - {{two_days_ago}} - Two days ago in user's timezone (YYYY-MM-DD)
    - {{this_week_start}} - Monday of current week in user's timezone (YYYY-MM-DD)
    - {{this_week_end}} - Sunday of current week in user's timezone (YYYY-MM-DD)
    - {{now}} - Current ISO datetime with Z suffix (UTC)
    - {{today_utc}}, {{yesterday_utc}}, {{tomorrow_utc}} - UTC date variants (rarely needed)
    - {{user.phone}}, {{user.email}}, {{user.id}}, {{user.timezone}} - User info
    - {{trigger_data.*}} - Trigger event data
    - {{<action_output_name>}} - Output from previous action

    Args:
        template: String with {{variable}} placeholders
        context: Dict containing variable values

    Returns:
        Resolved string with placeholders replaced
    """
    if not isinstance(template, str):
        return template

    def replace_var(match):
        var_path = match.group(1).strip()

        # Get user timezone (with UTC fallback)
        utc_now = datetime.now(ZoneInfo('UTC'))
        utc_today = utc_now.date()

        user_tz_str = get_nested_value(context, 'user.timezone')
        if user_tz_str:
            try:
                user_tz = ZoneInfo(user_tz_str)
                user_now = datetime.now(user_tz)
                user_today = user_now.date()
            except Exception as e:
                logger.warning(f"Invalid timezone '{user_tz_str}': {e}, falling back to UTC")
                user_today = utc_today
        else:
            user_today = utc_today

        # Date variables (use user's timezone)
        if var_path == 'today':
            return user_today.isoformat()
        elif var_path == 'tomorrow':
            return (user_today + timedelta(days=1)).isoformat()
        elif var_path == 'yesterday':
            return (user_today - timedelta(days=1)).isoformat()
        elif var_path == 'two_days_ago':
            return (user_today - timedelta(days=2)).isoformat()
        elif var_path == 'this_week_start':
            days_since_monday = user_today.weekday()
            return (user_today - timedelta(days=days_since_monday)).isoformat()
        elif var_path == 'this_week_end':
            days_until_sunday = 6 - user_today.weekday()
            return (user_today + timedelta(days=days_until_sunday)).isoformat()
        elif var_path == 'now':
            return utc_now.strftime('%Y-%m-%dT%H:%M:%SZ')

        # Time offset variables (for health data with sync delays)
        elif var_path == 'now_minus_1h':
            return (utc_now - timedelta(hours=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif var_path == 'now_minus_6h':
            return (utc_now - timedelta(hours=6)).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif var_path == 'now_minus_12h':
            return (utc_now - timedelta(hours=12)).strftime('%Y-%m-%dT%H:%M:%SZ')
        elif var_path == 'now_minus_24h':
            return (utc_now - timedelta(hours=24)).strftime('%Y-%m-%dT%H:%M:%SZ')

        # Explicit UTC date variants (rarely needed)
        elif var_path == 'today_utc':
            return utc_today.isoformat()
        elif var_path == 'yesterday_utc':
            return (utc_today - timedelta(days=1)).isoformat()
        elif var_path == 'tomorrow_utc':
            return (utc_today + timedelta(days=1)).isoformat()

        # Legacy _local variants (now same as default, kept for backwards compatibility)
        elif var_path == 'today_local':
            return user_today.isoformat()
        elif var_path == 'yesterday_local':
            return (user_today - timedelta(days=1)).isoformat()
        elif var_path == 'tomorrow_local':
            return (user_today + timedelta(days=1)).isoformat()

        # Look up in context
        value = get_nested_value(context, var_path)

        if value is None:
            logger.warning(f"Template variable not found: {var_path}")
            return "[No available data]"  # Return placeholder for unresolved templates

        # Convert complex types to JSON strings
        if isinstance(value, (dict, list)):
            return json.dumps(value)

        return str(value)

    # Match {{variable}} patterns
    pattern = r'\{\{([^}]+)\}\}'
    return re.sub(pattern, replace_var, template)


def resolve_parameters(params: Dict[str, Any], context: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively resolve all template variables in a parameters dict."""
    resolved = {}

    for key, value in params.items():
        if isinstance(value, str):
            resolved[key] = resolve_template(value, context)
        elif isinstance(value, dict):
            resolved[key] = resolve_parameters(value, context)
        elif isinstance(value, list):
            resolved[key] = [
                resolve_template(item, context) if isinstance(item, str)
                else resolve_parameters(item, context) if isinstance(item, dict)
                else item
                for item in value
            ]
        else:
            resolved[key] = value

    return resolved

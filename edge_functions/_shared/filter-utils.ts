/**
 * Shared Filter Utilities
 *
 * Evaluates trigger_config filters against event data.
 * Used by polling-manager and webhook-handler to filter events at the source
 * before creating events or executing automations.
 */

/**
 * Filter configuration format (from trigger_config.filter)
 *
 * Simple format:
 * { field: "text", contains: "keyword", case_insensitive: true }
 * { field: "score", greater_than: 70 }
 * { field: "sender", equals: "user@example.com" }
 *
 * Clause format (for complex conditions):
 * { path: "text", op: "contains", value: "keyword" }
 *
 * Multi-clause format:
 * { operator: "AND", clauses: [{path, op, value}, ...] }
 */

export interface SimpleFilter {
  field: string;
  contains?: string;
  contains_any?: string[];  // Match if field contains ANY of these values (OR logic)
  equals?: any;
  not_equals?: any;
  greater_than?: number;
  less_than?: number;
  starts_with?: string;
  ends_with?: string;
  case_insensitive?: boolean;
}

export interface ClauseFilter {
  path: string;
  op: string;
  value: any;
}

export interface MultiClauseFilter {
  operator?: 'AND' | 'OR';
  clauses: ClauseFilter[];
}

export type FilterConfig = SimpleFilter | ClauseFilter | MultiClauseFilter;

/**
 * Get a nested value from an object using dot notation path
 * Supports array indexing: "data[0].score" or "data.0.score"
 */
function getNestedValueStrict(data: any, path: string): any {
  if (data === null || data === undefined) return undefined;

  // Handle array notation like data[0].score -> data.0.score
  const normalizedPath = path.replace(/\[(\d+)\]/g, '.$1');
  const parts = normalizedPath.split('.');

  let current = data;
  for (const part of parts) {
    if (current === null || current === undefined) return undefined;

    // Try numeric index for arrays
    if (/^\d+$/.test(part)) {
      const idx = parseInt(part, 10);
      if (Array.isArray(current) && idx >= 0 && idx < current.length) {
        current = current[idx];
      } else {
        return undefined;
      }
    } else if (typeof current === 'object') {
      current = current[part];
    } else {
      return undefined;
    }
  }
  return current;
}

/**
 * Flexible path resolution - tries multiple path variants to handle
 * inconsistency between agents using/not using trigger_data. prefix
 *
 * Order of attempts:
 * 1. Path as-is
 * 2. With trigger_data. prefix added (if not already present)
 * 3. With trigger_data. prefix stripped (if present)
 */
function getNestedValue(data: any, path: string): any {
  // Try path as-is first
  let result = getNestedValueStrict(data, path);
  if (result !== undefined) {
    return result;
  }

  // Try with trigger_data. prefix if not already present
  if (!path.startsWith('trigger_data.')) {
    result = getNestedValueStrict(data, `trigger_data.${path}`);
    if (result !== undefined) {
      console.log(`[filter] Path "${path}" resolved via trigger_data. prefix`);
      return result;
    }
  }

  // Try without trigger_data. prefix if present
  if (path.startsWith('trigger_data.')) {
    const strippedPath = path.substring('trigger_data.'.length);
    result = getNestedValueStrict(data, strippedPath);
    if (result !== undefined) {
      console.log(`[filter] Path "${path}" resolved by stripping trigger_data. prefix`);
      return result;
    }
  }

  return undefined;
}

/**
 * Compare two values using the specified operator
 */
function compareValues(actual: any, op: string, expected: any, caseInsensitive: boolean = false): boolean {
  // Handle null/undefined
  if (actual === null || actual === undefined) {
    if (op === 'exists') return false;
    if (op === 'not_exists') return true;
    return false;
  }

  // Existence operators
  if (op === 'exists') return true;
  if (op === 'not_exists') return false;

  // Numeric comparisons
  if (['<', '>', '<=', '>=', 'less_than', 'greater_than'].includes(op)) {
    const numActual = parseFloat(actual);
    const numExpected = parseFloat(expected);
    if (isNaN(numActual) || isNaN(numExpected)) {
      return false;
    }
    switch (op) {
      case '<':
      case 'less_than':
        return numActual < numExpected;
      case '>':
      case 'greater_than':
        return numActual > numExpected;
      case '<=':
        return numActual <= numExpected;
      case '>=':
        return numActual >= numExpected;
    }
  }

  // String operations (apply case insensitivity)
  let strActual = String(actual);
  let strExpected = String(expected);

  if (caseInsensitive) {
    strActual = strActual.toLowerCase();
    strExpected = strExpected.toLowerCase();
  }

  switch (op) {
    case '==':
    case 'eq':
    case 'equals':
      return caseInsensitive ? strActual === strExpected : actual == expected;
    case '!=':
    case 'neq':
    case 'not_equals':
      return caseInsensitive ? strActual !== strExpected : actual != expected;
    case 'contains':
      return strActual.includes(strExpected);
    case 'contains_any':
      // Match if actual contains ANY of the expected values (expected should be an array)
      if (!Array.isArray(expected)) {
        console.warn('contains_any operator requires an array value');
        return false;
      }
      return expected.some(keyword => {
        const kw = caseInsensitive ? String(keyword).toLowerCase() : String(keyword);
        return strActual.includes(kw);
      });
    case 'not_contains':
      return !strActual.includes(strExpected);
    case 'starts_with':
      return strActual.startsWith(strExpected);
    case 'ends_with':
      return strActual.endsWith(strExpected);
    default:
      console.warn(`Unknown comparison operator: ${op}`);
      return false;
  }
}

/**
 * Evaluate a simple filter format
 * { field: "text", contains: "keyword", case_insensitive: true }
 */
function evaluateSimpleFilter(data: any, filter: SimpleFilter): boolean {
  const fieldValue = getNestedValue(data, filter.field);
  const caseInsensitive = filter.case_insensitive ?? true; // Default to case insensitive

  if (filter.contains !== undefined) {
    return compareValues(fieldValue, 'contains', filter.contains, caseInsensitive);
  }
  // contains_any: match if field contains ANY of the values (OR logic)
  if (filter.contains_any !== undefined && Array.isArray(filter.contains_any)) {
    if (fieldValue === null || fieldValue === undefined) {
      return false;
    }
    let strValue = String(fieldValue);
    if (caseInsensitive) {
      strValue = strValue.toLowerCase();
    }
    return filter.contains_any.some(keyword => {
      const kw = caseInsensitive ? keyword.toLowerCase() : keyword;
      return strValue.includes(kw);
    });
  }
  if (filter.equals !== undefined) {
    return compareValues(fieldValue, 'equals', filter.equals, caseInsensitive);
  }
  if (filter.not_equals !== undefined) {
    return compareValues(fieldValue, 'not_equals', filter.not_equals, caseInsensitive);
  }
  if (filter.greater_than !== undefined) {
    return compareValues(fieldValue, 'greater_than', filter.greater_than, false);
  }
  if (filter.less_than !== undefined) {
    return compareValues(fieldValue, 'less_than', filter.less_than, false);
  }
  if (filter.starts_with !== undefined) {
    return compareValues(fieldValue, 'starts_with', filter.starts_with, caseInsensitive);
  }
  if (filter.ends_with !== undefined) {
    return compareValues(fieldValue, 'ends_with', filter.ends_with, caseInsensitive);
  }

  // No recognized operator - pass through
  console.warn('Filter has no recognized operator:', filter);
  return true;
}

/**
 * Evaluate a clause filter format
 * { path: "text", op: "contains", value: "keyword" }
 */
function evaluateClauseFilter(data: any, clause: ClauseFilter): boolean {
  const actual = getNestedValue(data, clause.path);
  return compareValues(actual, clause.op, clause.value, true); // Default case insensitive
}

/**
 * Check if filter is a simple format (has 'field' key)
 */
function isSimpleFilter(filter: any): filter is SimpleFilter {
  return filter && typeof filter === 'object' && 'field' in filter;
}

/**
 * Check if filter is a clause format (has 'path' and 'op' keys)
 */
function isClauseFilter(filter: any): filter is ClauseFilter {
  return filter && typeof filter === 'object' && 'path' in filter && 'op' in filter;
}

/**
 * Check if filter is a multi-clause format (has 'clauses' array)
 */
function isMultiClauseFilter(filter: any): filter is MultiClauseFilter {
  return filter && typeof filter === 'object' && 'clauses' in filter && Array.isArray(filter.clauses);
}

/**
 * Main filter evaluation function
 *
 * @param data - The data to evaluate against (event_data, webhook payload, etc.)
 * @param filter - The filter configuration from trigger_config.filter
 * @returns true if data passes the filter, false otherwise
 */
export function evaluateFilter(data: any, filter: FilterConfig | null | undefined): boolean {
  // No filter = pass through
  if (!filter) {
    return true;
  }

  try {
    // Simple format: { field: "text", contains: "keyword" }
    if (isSimpleFilter(filter)) {
      return evaluateSimpleFilter(data, filter);
    }

    // Clause format: { path: "text", op: "contains", value: "keyword" }
    if (isClauseFilter(filter)) {
      return evaluateClauseFilter(data, filter);
    }

    // Multi-clause format: { operator: "AND", clauses: [...] }
    if (isMultiClauseFilter(filter)) {
      const operator = (filter.operator || 'AND').toUpperCase();
      const clauses = filter.clauses;

      if (clauses.length === 0) {
        return true;
      }

      // Recursively evaluate each clause - can be simple clause or nested multi-clause
      const evaluateClause = (clause: any): boolean => {
        if (isMultiClauseFilter(clause)) {
          // Nested multi-clause - recurse
          return evaluateFilter(data, clause);
        } else if (isClauseFilter(clause)) {
          return evaluateClauseFilter(data, clause);
        } else {
          console.warn('Unknown clause format:', clause);
          return true;
        }
      };

      if (operator === 'AND') {
        return clauses.every(evaluateClause);
      } else if (operator === 'OR') {
        return clauses.some(evaluateClause);
      }

      console.warn(`Unknown logical operator: ${operator}`);
      return false;
    }

    // Unknown format - pass through with warning
    console.warn('Unknown filter format:', filter);
    return true;

  } catch (error) {
    console.error('Error evaluating filter:', error);
    return true; // Pass through on error to avoid blocking events
  }
}

/**
 * Convenience function to check if an item matches a trigger_config's filter
 *
 * @param item - The normalized event data item
 * @param triggerConfig - The full trigger_config object
 * @returns true if item passes the filter (or no filter exists), false otherwise
 */
export function matchesTriggerFilter(item: any, triggerConfig: any): boolean {
  const filter = triggerConfig?.filter;
  return evaluateFilter(item, filter);
}

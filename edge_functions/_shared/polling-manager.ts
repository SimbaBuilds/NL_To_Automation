// Polling infrastructure for services that don't support webhooks
// Refactored to use automation_records as single source of truth
// No more POLLING_TOOL_MAP - automation trigger_config specifies exact tool to poll

import { createClient } from "jsr:@supabase/supabase-js@2";
import { evaluateFilter, matchesTriggerFilter } from "./filter-utils.ts";

// Aggregation modes for polling results
// - per_item: Create one event per item (default for messages, tasks)
// - latest: Create one event with only the latest item (default for health time-series)
// - batch: Create one event containing all items
// - summary: Create one event with computed stats (min, max, avg, count)
export type AggregationMode = 'per_item' | 'latest' | 'batch' | 'summary';

export interface PollingAutomation {
  id: string;
  user_id: string;
  name: string;
  trigger_config: {
    service: string;
    source_tool: string;         // Exact tool name to execute
    event_type: string;          // Event type to emit
    tool_params?: Record<string, any>; // Parameters for the tool
    filter?: any;                // Trigger filter - only create events for matching items
    filters?: any;               // Legacy: condition filters for matching
    aggregation_mode?: AggregationMode; // How to aggregate multiple items into events
  };
  actions: any[];
  variables?: Record<string, any>;
  next_poll_at?: string;
  last_poll_cursor?: string;
  polling_interval_minutes?: number;
}

export interface PollingResult {
  success: boolean;
  items_found: number;
  new_cursor?: string;
  error?: string;
  events_created: number;
}

// Default polling intervals per service category (minutes)
const DEFAULT_POLLING_INTERVALS: Record<string, number> = {
  'oura': 60,
  'fitbit': 15,
  'todoist': 5,
  'google_calendar': 10,
  'outlook_calendar': 10,
  'excel': 10,
  'word': 15,
  'notion': 10,
  'default': 15
};

// Note: Normalization/flattening was removed to maintain consistency between
// what the EDA agent sees in schema discovery (service_tools.returns) and
// what trigger_data actually contains. The agent should use exact paths
// as documented in the returns schema.

export class PollingManager {
  private supabaseClient: any;      // For automations schema
  private supabasePublic: any;      // For public schema (service_tools, services, tags)
  private fastApiUrl: string;
  private healthServiceCache: Map<string, boolean> = new Map();  // Cache for health service lookups

  constructor() {
    this.supabaseClient = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
      {
        db: { schema: 'automations' },
        auth: {
          autoRefreshToken: false,
          persistSession: false
        }
      }
    );
    // Public schema client for querying service_tools, services, and tags
    this.supabasePublic = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? "",
      {
        db: { schema: 'public' },
        auth: {
          autoRefreshToken: false,
          persistSession: false
        }
      }
    );
    this.fastApiUrl = Deno.env.get("FASTAPI_URL") ?? "";
    if (!this.fastApiUrl) {
      console.warn("FASTAPI_URL environment variable not set");
    }
  }

  /**
   * Check if a tool belongs to a "Health and Wellness" service.
   * Uses the tagging system: service_tool → service → tags → name == "Health and Wellness"
   * Results are cached for the lifetime of this PollingManager instance.
   */
  private async isHealthService(toolName: string): Promise<boolean> {
    // Check cache first
    if (this.healthServiceCache.has(toolName)) {
      return this.healthServiceCache.get(toolName)!;
    }

    try {
      // 1. Get service_id from service_tools
      const { data: toolData, error: toolError } = await this.supabasePublic
        .from('service_tools')
        .select('service_id')
        .eq('name', toolName)
        .single();

      if (toolError || !toolData) {
        console.log(`Tool ${toolName} not found in service_tools`);
        this.healthServiceCache.set(toolName, false);
        return false;
      }

      // 2. Get service's tag IDs
      const { data: serviceData, error: serviceError } = await this.supabasePublic
        .from('services')
        .select('tag_1_id, tag_2_id, tag_3_id, tag_4_id, tag_5_id')
        .eq('id', toolData.service_id)
        .single();

      if (serviceError || !serviceData) {
        console.log(`Service not found for tool ${toolName}`);
        this.healthServiceCache.set(toolName, false);
        return false;
      }

      // 3. Collect all tag IDs
      const tagIds = [
        serviceData.tag_1_id,
        serviceData.tag_2_id,
        serviceData.tag_3_id,
        serviceData.tag_4_id,
        serviceData.tag_5_id
      ].filter(Boolean);

      if (tagIds.length === 0) {
        this.healthServiceCache.set(toolName, false);
        return false;
      }

      // 4. Check if any tag is "Health and Wellness"
      const { data: tags, error: tagsError } = await this.supabasePublic
        .from('tags')
        .select('id')
        .in('id', tagIds)
        .eq('name', 'Health and Wellness');

      const isHealth = !tagsError && tags && tags.length > 0;
      this.healthServiceCache.set(toolName, isHealth);

      if (isHealth) {
        console.log(`Tool ${toolName} is a Health and Wellness service - will use 'latest' aggregation by default`);
      }

      return isHealth;
    } catch (error) {
      console.error(`Error checking if ${toolName} is health service:`, error);
      this.healthServiceCache.set(toolName, false);
      return false;
    }
  }

  /**
   * Determine the effective aggregation mode for a polling automation.
   * - If explicitly set in trigger_config, use that
   * - If tool is a health service, default to 'latest'
   * - Otherwise, default to 'per_item'
   */
  private async getEffectiveAggregationMode(triggerConfig: PollingAutomation['trigger_config']): Promise<AggregationMode> {
    // Explicit mode takes precedence
    if (triggerConfig.aggregation_mode) {
      return triggerConfig.aggregation_mode;
    }

    // Check if this is a health service tool
    const isHealth = await this.isHealthService(triggerConfig.source_tool);
    if (isHealth) {
      return 'latest';
    }

    // Default to per_item for everything else
    return 'per_item';
  }

  // Get polling automations that are due for execution
  async getPollingAutomationsDue(): Promise<PollingAutomation[]> {
    try {
      const now = new Date().toISOString();

      const { data, error } = await this.supabaseClient
        .from('automation_records')
        .select(`
          id,
          user_id,
          name,
          trigger_config,
          actions,
          variables,
          next_poll_at,
          last_poll_cursor,
          polling_interval_minutes
        `)
        .eq('active', true)
        .eq('trigger_type', 'polling')
        .or(`next_poll_at.is.null,next_poll_at.lte.${now}`);

      if (error) {
        console.error('Failed to fetch polling automations:', error);
        return [];
      }

      console.log(`Found ${data?.length || 0} polling automations due for execution`);
      return data || [];
    } catch (error) {
      console.error('Error fetching polling automations:', error);
      return [];
    }
  }

  // Get a specific polling automation by ID (for force-poll, ignores next_poll_at)
  async getPollingAutomationById(automationId: string): Promise<PollingAutomation | null> {
    try {
      const { data, error } = await this.supabaseClient
        .from('automation_records')
        .select(`
          id,
          user_id,
          name,
          trigger_config,
          actions,
          variables,
          next_poll_at,
          last_poll_cursor,
          polling_interval_minutes
        `)
        .eq('id', automationId)
        .eq('active', true)
        .eq('trigger_type', 'polling')
        .single();

      if (error) {
        console.error('Failed to fetch polling automation:', error);
        return null;
      }

      return data;
    } catch (error) {
      console.error('Error fetching polling automation:', error);
      return null;
    }
  }

  // Poll and process a single automation
  async pollAutomation(automation: PollingAutomation): Promise<PollingResult> {
    const triggerConfig = automation.trigger_config;

    if (!triggerConfig?.source_tool) {
      return {
        success: false,
        items_found: 0,
        events_created: 0,
        error: `Automation ${automation.id} missing source_tool in trigger_config`
      };
    }

    console.log(`Polling automation "${automation.name}" (${automation.id}) using tool: ${triggerConfig.source_tool}`);

    try {
      // Build tool parameters
      const params = this.buildToolParams(
        triggerConfig.source_tool,
        triggerConfig.tool_params || {},
        automation.last_poll_cursor
      );

      // Execute the tool via FastAPI
      const toolResult = await this.executeToolViaFastApi(
        triggerConfig.source_tool,
        automation.user_id,
        params
      );

      if (!toolResult.success) {
        return {
          success: false,
          items_found: 0,
          events_created: 0,
          error: toolResult.error
        };
      }

      // Get raw output for filtering - filter paths should match schema discovery
      const rawOutput = toolResult.output;
      const triggerFilter = triggerConfig.filter || triggerConfig.filters;

      // Determine aggregation mode (health services default to 'latest')
      const aggregationMode = await this.getEffectiveAggregationMode(triggerConfig);
      console.log(`Using aggregation mode: ${aggregationMode}`);

      // For 'latest' mode (health services): apply filter to RAW output, not extracted items
      // This ensures filter paths match schema discovery (e.g., "data.0.bpm" for Oura)
      // For other modes: extract items first, then filter each item
      let eventsCreated = 0;
      let eventsFiltered = 0;

      if (aggregationMode === 'latest') {
        // Health services: filter on raw output, store raw output as trigger_data
        // This keeps paths consistent with schema discovery
        console.log(`Evaluating filter on raw output: ${JSON.stringify(triggerFilter)}`);
        console.log(`Raw output structure: ${JSON.stringify(rawOutput).substring(0, 200)}...`);

        // Apply trigger filter to raw output
        const filterPasses = !triggerFilter || evaluateFilter(rawOutput, triggerFilter);
        console.log(`Filter result: ${filterPasses}`);

        if (filterPasses) {
          // Extract items to find latest for cursor calculation and ID
          const items = this.extractItemsFromOutput(rawOutput);
          const sortedItems = [...items].sort((a, b) => {
            const dateA = this.extractItemDate(a) || '';
            const dateB = this.extractItemDate(b) || '';
            return dateB.localeCompare(dateA);
          });
          const latestItem = sortedItems[0];

          // Build event_data preserving original structure (array or object)
          // This ensures trigger_data paths match schema discovery exactly
          // e.g., if tool returns [{date, metrics}], trigger_data.0.metrics works
          let eventData: any;
          if (Array.isArray(rawOutput)) {
            // Preserve array structure - don't spread (which would create {"0": {...}})
            eventData = rawOutput;
          } else if (typeof rawOutput === 'object' && rawOutput !== null) {
            // Object output - safe to spread with metadata
            eventData = {
              type: triggerConfig.event_type,
              ...rawOutput,
              automation_id: automation.id,
              _aggregation: { mode: 'latest', total_items: items.length }
            };
          } else {
            // String or primitive output (e.g., "No daily readiness data found")
            // Don't spread - wrap in a proper object structure
            console.log(`Tool returned non-object output: ${String(rawOutput).substring(0, 100)}`);
            eventData = {
              type: triggerConfig.event_type,
              message: rawOutput,
              automation_id: automation.id,
              _aggregation: { mode: 'latest', total_items: 0 }
            };
          }

          await this.createPollingEvent(
            automation.user_id,
            triggerConfig.service,
            triggerConfig.event_type,
            this.extractItemId(latestItem || rawOutput),
            eventData
          );
          eventsCreated = 1;
        } else {
          eventsFiltered = 1;
        }

        // Calculate cursor from extracted items
        const items = this.extractItemsFromOutput(rawOutput);
        const newCursor = this.calculateNewCursor(items, automation.last_poll_cursor);
        await this.updateAutomationPollingState(automation, newCursor);

        if (eventsFiltered > 0) {
          console.log(`Filter did not match - no event created`);
        }

        return {
          success: true,
          items_found: items.length,
          events_created: eventsCreated,
          new_cursor: newCursor
        };
      }

      // For non-latest modes: use original per-item logic
      const items = this.extractItemsFromOutput(rawOutput);
      console.log(`Tool returned ${items.length} items`);

      // Filter to only new items (after last_poll_cursor)
      const newItems = this.filterNewItems(items, automation.last_poll_cursor);
      console.log(`${newItems.length} new items after cursor filter`);

      if (aggregationMode === 'per_item') {
        // Original behavior: one event per item
        for (const item of newItems) {
          // Apply trigger filter - skip items that don't match
          if (triggerFilter && !evaluateFilter(item, triggerFilter)) {
            eventsFiltered++;
            continue;
          }

          await this.createPollingEvent(
            automation.user_id,
            triggerConfig.service,
            triggerConfig.event_type,
            this.extractItemId(item),
            {
              type: triggerConfig.event_type,
              ...item,
              automation_id: automation.id
            }
          );
          eventsCreated++;
        }
      } else if (aggregationMode === 'batch') {
        // Create one event containing all items
        if (newItems.length > 0) {
          // Apply filter to all items, only include matching ones
          const matchingItems = triggerFilter
            ? newItems.filter(item => evaluateFilter(item, triggerFilter))
            : newItems;

          eventsFiltered = newItems.length - matchingItems.length;

          if (matchingItems.length > 0) {
            await this.createPollingEvent(
              automation.user_id,
              triggerConfig.service,
              triggerConfig.event_type,
              `batch_${Date.now()}`,
              {
                type: triggerConfig.event_type,
                items: matchingItems,
                count: matchingItems.length,
                automation_id: automation.id,
                _aggregation: { mode: 'batch', total_items: newItems.length, filtered_items: eventsFiltered }
              }
            );
            eventsCreated = 1;
          }
        }
      } else if (aggregationMode === 'summary') {
        // Create one event with computed statistics
        if (newItems.length > 0) {
          // Apply filter first
          const matchingItems = triggerFilter
            ? newItems.filter(item => evaluateFilter(item, triggerFilter))
            : newItems;

          eventsFiltered = newItems.length - matchingItems.length;

          if (matchingItems.length > 0) {
            // Compute summary stats for numeric fields
            const summary = this.computeSummaryStats(matchingItems);

            await this.createPollingEvent(
              automation.user_id,
              triggerConfig.service,
              triggerConfig.event_type,
              `summary_${Date.now()}`,
              {
                type: triggerConfig.event_type,
                ...summary,
                automation_id: automation.id,
                _aggregation: { mode: 'summary', total_items: newItems.length, filtered_items: eventsFiltered }
              }
            );
            eventsCreated = 1;
          }
        }
      }

      if (eventsFiltered > 0) {
        console.log(`${eventsFiltered} items filtered out by trigger filter`);
      }

      // Calculate new cursor
      const newCursor = this.calculateNewCursor(items, automation.last_poll_cursor);

      // Update automation's polling state
      await this.updateAutomationPollingState(automation, newCursor);

      return {
        success: true,
        items_found: items.length,
        events_created: eventsCreated,
        new_cursor: newCursor
      };

    } catch (error) {
      console.error(`Error polling automation ${automation.id}:`, error);
      return {
        success: false,
        items_found: 0,
        events_created: 0,
        error: error.message
      };
    }
  }

  // Build tool parameters with date resolution
  private buildToolParams(
    toolName: string,
    configParams: Record<string, any>,
    lastCursor?: string
  ): Record<string, any> {
    const today = new Date().toISOString().split('T')[0];
    const yesterday = new Date(Date.now() - 24 * 60 * 60 * 1000).toISOString().split('T')[0];

    // Start with configured params
    const params = { ...configParams };

    // Resolve template variables
    for (const [key, value] of Object.entries(params)) {
      if (typeof value === 'string') {
        params[key] = value
          .replace('{{today}}', today)
          .replace('{{yesterday}}', yesterday)
          .replace('{{last_cursor}}', lastCursor || yesterday);
      }
    }

    // Add default date params if tool seems to need them and none provided
    const toolLower = toolName.toLowerCase();
    if ((toolLower.includes('oura') || toolLower.includes('fitbit')) &&
        !params.start_date && !params.date) {
      params.start_date = lastCursor || yesterday;
      params.end_date = today;
    }

    return params;
  }

  // Execute a tool via FastAPI endpoint
  private async executeToolViaFastApi(
    toolName: string,
    userId: string,
    params: Record<string, any>
  ): Promise<{ success: boolean; output?: any; error?: string }> {
    try {
      const response = await fetch(`${this.fastApiUrl}/api/automations/tools/execute`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")}`
        },
        body: JSON.stringify({
          tool_name: toolName,
          user_id: userId,
          parameters: params,
          timeout: 30.0
        })
      });

      if (!response.ok) {
        const errorText = await response.text();
        throw new Error(`FastAPI error: ${response.status} - ${errorText}`);
      }

      const result = await response.json();
      return {
        success: result.success,
        output: result.output,
        error: result.error
      };
    } catch (error) {
      console.error(`FastAPI tool execution failed:`, error);
      return { success: false, error: error.message };
    }
  }

  // Extract items array from tool output
  private extractItemsFromOutput(output: any): any[] {
    if (!output) return [];
    if (Array.isArray(output)) return output;

    // Handle nested data structures
    if (output.data && Array.isArray(output.data)) return output.data;
    if (output.items && Array.isArray(output.items)) return output.items;
    if (output.files && Array.isArray(output.files)) return output.files;
    if (output.events && Array.isArray(output.events)) return output.events;
    if (output.tasks && Array.isArray(output.tasks)) return output.tasks;
    if (output.sleep && Array.isArray(output.sleep)) return output.sleep;
    if (output.summary) return [output.summary];

    // Single object
    if (typeof output === 'object') return [output];

    return [];
  }

  // Check if a string looks like a Slack/numeric timestamp (e.g., "1765920004.574")
  private isNumericTimestamp(value: string): boolean {
    return /^\d+(\.\d+)?$/.test(value);
  }

  // Check if a string is RFC2822 date format (e.g., "Thu, 27 Feb 2025 19:47:01 -0600")
  // This format is used by Gmail's Date header per email standards (RFC 5322)
  private isRfc2822Date(value: string): boolean {
    return /^[A-Za-z]{3},\s\d{1,2}\s[A-Za-z]{3}\s\d{4}/.test(value);
  }

  // Parse RFC2822 date to timestamp for proper chronological comparison
  // String comparison fails for RFC2822 because day names sort alphabetically (Thu > Sat)
  private parseRfc2822ToTimestamp(dateStr: string): number {
    try {
      return new Date(dateStr).getTime();
    } catch {
      return 0;
    }
  }

  // Filter items to only those that represent changes
  // For date-based data (sleep, activities): compare dates to cursor
  // For timestamp-based data (Slack messages): compare timestamps numerically
  // For state-based data (presence, status): compare value signature to cursor
  private filterNewItems(items: any[], lastCursor?: string): any[] {
    if (!lastCursor) return items;

    return items.filter(item => {
      const itemDate = this.extractItemDate(item);

      if (itemDate) {
        // Handle numeric timestamps (Slack ts like "1765920004.574")
        if (this.isNumericTimestamp(itemDate)) {
          // If cursor is also numeric, compare numerically
          if (this.isNumericTimestamp(lastCursor)) {
            return parseFloat(itemDate) > parseFloat(lastCursor);
          }
          // Cursor is a date string (legacy) - include all timestamp items
          // This handles transition from date-based to timestamp-based cursor
          return true;
        }

        // Handle RFC2822 dates (Gmail format: "Thu, 27 Feb 2025 19:47:01 -0600")
        // String comparison fails for these - must parse to timestamps
        if (this.isRfc2822Date(itemDate)) {
          if (this.isRfc2822Date(lastCursor)) {
            const itemTs = this.parseRfc2822ToTimestamp(itemDate);
            const cursorTs = this.parseRfc2822ToTimestamp(lastCursor);
            return itemTs > cursorTs;
          }
          // Cursor is different format (transitioning) - include item to be safe
          return true;
        }

        // ISO date comparison (YYYY-MM-DD format) - string comparison works correctly
        return itemDate > lastCursor;
      }

      // State-based comparison (presence, status, etc.)
      // Only include if value signature differs from cursor
      const valueSignature = this.extractValueSignature(item);
      if (valueSignature) {
        return valueSignature !== lastCursor;
      }

      // Fallback: include item (shouldn't happen often)
      console.warn('Item has no date or value signature, including by default');
      return true;
    });
  }

  // Extract date/timestamp from item for cursor comparison
  private extractItemDate(item: any): string | null {
    // Slack message timestamp (e.g., "1765920004.574")
    // These are comparable as strings since they're numeric
    if (item.ts) return item.ts;

    // Standard date fields
    if (item.day) return item.day;
    if (item.dateOfSleep) return item.dateOfSleep;
    if (item.date) return item.date;
    if (item.modifiedTime) return item.modifiedTime.split('T')[0];
    if (item.lastModifiedDateTime) return item.lastModifiedDateTime.split('T')[0];
    if (item.updated_at) return item.updated_at.split('T')[0];
    if (item.created_at) return item.created_at.split('T')[0];
    // Outlook/email specific date fields
    if (item.received_date) return item.received_date;
    if (item.receivedDateTime) return item.receivedDateTime;
    return null;
  }

  // Extract value signature for state-based change detection
  // Used for presence, status, and other non-date-based data
  private extractValueSignature(item: any): string | null {
    // Presence-based (active/away)
    if (item.presence !== undefined) {
      return `presence:${item.presence}`;
    }

    // Status-based (custom status text/emoji)
    if (item.status_text !== undefined || item.status_emoji !== undefined) {
      return `status:${item.status_text || ''}|${item.status_emoji || ''}`;
    }

    // Task completion state
    if (item.is_completed !== undefined) {
      return `task:${item.id}:${item.is_completed}`;
    }

    // Generic state fields
    if (item.state !== undefined) {
      return `state:${item.state}`;
    }

    if (item.status !== undefined) {
      return `status:${item.status}`;
    }

    return null;
  }

  // Extract unique ID from item
  private extractItemId(item: any): string {
    return item.id || item.logId || item.day || item.date || `${Date.now()}`;
  }

  /**
   * Compute summary statistics for a list of items.
   * Finds all numeric fields and computes min, max, avg for each.
   */
  private computeSummaryStats(items: any[]): Record<string, any> {
    if (items.length === 0) {
      return { count: 0 };
    }

    const summary: Record<string, any> = {
      count: items.length,
      latest: items[0],  // Include the first (latest) item for reference
    };

    // Find all numeric fields in the first item
    const numericFields: string[] = [];
    for (const [key, value] of Object.entries(items[0])) {
      if (typeof value === 'number' && !key.startsWith('_')) {
        numericFields.push(key);
      }
    }

    // Compute stats for each numeric field
    for (const field of numericFields) {
      const values = items
        .map(item => item[field])
        .filter(v => typeof v === 'number' && !isNaN(v));

      if (values.length > 0) {
        summary[`${field}_min`] = Math.min(...values);
        summary[`${field}_max`] = Math.max(...values);
        summary[`${field}_avg`] = values.reduce((a, b) => a + b, 0) / values.length;
      }
    }

    return summary;
  }

  // Compare two date strings, handling RFC2822 format properly
  private compareDates(date1: string, date2: string): number {
    // Both RFC2822 - parse to timestamps
    if (this.isRfc2822Date(date1) && this.isRfc2822Date(date2)) {
      return this.parseRfc2822ToTimestamp(date1) - this.parseRfc2822ToTimestamp(date2);
    }
    // Both numeric timestamps
    if (this.isNumericTimestamp(date1) && this.isNumericTimestamp(date2)) {
      return parseFloat(date1) - parseFloat(date2);
    }
    // Default to string comparison (works for ISO dates)
    return date1.localeCompare(date2);
  }

  // Calculate new cursor based on items
  // Returns either a date (for date-based data) or value signature (for state-based data)
  private calculateNewCursor(items: any[], currentCursor?: string): string {
    const today = new Date().toISOString().split('T')[0];

    if (items.length === 0) {
      return currentCursor || today;
    }

    // Check if items are date-based or state-based
    const firstItem = items[0];
    const itemDate = this.extractItemDate(firstItem);

    if (itemDate) {
      // Date-based: find the most recent date
      let latestDate = currentCursor || '';
      for (const item of items) {
        const date = this.extractItemDate(item);
        if (date && (!latestDate || this.compareDates(date, latestDate) > 0)) {
          latestDate = date;
        }
      }
      return latestDate || today;
    }

    // State-based: return value signature of first item
    const valueSignature = this.extractValueSignature(firstItem);
    if (valueSignature) {
      return valueSignature;
    }

    // Fallback to today's date
    return today;
  }

  // Create a polling event in the database
  private async createPollingEvent(
    userId: string,
    serviceName: string,
    eventType: string,
    eventId: string,
    eventData: any
  ): Promise<void> {
    try {
      const { error } = await this.supabaseClient
        .from('automation_events')
        .insert({
          user_id: userId,
          service_name: serviceName.toLowerCase(),
          event_type: eventType,
          event_id: `${serviceName}_${eventId}_${Date.now()}`,
          event_data: eventData,
          processed: false,
          retry_count: 0
        });

      if (error) {
        console.error('Failed to create polling event:', error);
        throw error;
      }
    } catch (error) {
      console.error('Error creating polling event:', error);
      throw error;
    }
  }

  // Update automation's polling state after poll completes
  private async updateAutomationPollingState(
    automation: PollingAutomation,
    newCursor: string
  ): Promise<void> {
    try {
      // Get default interval for service, or use configured interval
      const serviceLower = automation.trigger_config.service?.toLowerCase() || 'default';
      const defaultInterval = DEFAULT_POLLING_INTERVALS[serviceLower] || DEFAULT_POLLING_INTERVALS['default'];
      const interval = automation.polling_interval_minutes || defaultInterval;

      const nextPollAt = new Date(Date.now() + interval * 60 * 1000);

      const { error } = await this.supabaseClient
        .from('automation_records')
        .update({
          last_poll_cursor: newCursor,
          next_poll_at: nextPollAt.toISOString(),
          polling_interval_minutes: interval
        })
        .eq('id', automation.id);

      if (error) {
        console.error(`Failed to update polling state for automation ${automation.id}:`, error);
      } else {
        console.log(`Updated automation ${automation.id}: next_poll_at=${nextPollAt.toISOString()}, cursor=${newCursor}`);
      }
    } catch (error) {
      console.error('Error updating automation polling state:', error);
    }
  }

  // ============================================================================
  // LEGACY COMPATIBILITY - Keep for backward compatibility during migration
  // These methods map to the old service_polling_state based approach
  // Can be removed once all polling automations are migrated
  // ============================================================================

  async getActivePollConfigs(): Promise<any[]> {
    // Return empty - no longer using service_polling_state
    // All polling should now go through automation_records
    console.warn('getActivePollConfigs() is deprecated - use getPollingAutomationsDue() instead');
    return [];
  }

  async pollService(config: any): Promise<any> {
    // Deprecated - polling now happens per-automation, not per-service
    console.warn('pollService() is deprecated - use pollAutomation() instead');
    return {
      success: false,
      items_found: 0,
      error: 'pollService is deprecated - use automation-driven polling'
    };
  }
}

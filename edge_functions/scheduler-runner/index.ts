// Setup type definitions for built-in Supabase Runtime APIs
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { BaseAutomationHandler } from "../_shared/base-handler.ts";
import { PollingManager } from "../_shared/polling-manager.ts";

interface ScheduledAutomation {
  id: string;
  user_id: string;
  name: string;
  script_code?: string;  // For legacy script-based automations
  actions?: any[];       // For declarative automations
  trigger_type: string;  // schedule_once or schedule_recurring
  trigger_config: any;
  execution_params: any;
  dependencies: string[];
  last_executed_at?: string;
}

interface CronSchedule {
  interval: string; // '5min', '15min', '30min', '1hr', '6hr', 'daily'
  cron_expression: string;
}

class SchedulerRunner extends BaseAutomationHandler {
  private pollingManager: PollingManager;

  constructor() {
    super();
    this.pollingManager = new PollingManager();
  }

  // Convert day_of_week (string or number) to numeric day (0-6, Sunday-Saturday)
  private getDayNumber(dayOfWeek: string | number | undefined): number {
    if (dayOfWeek == null) return 0; // Default to Sunday
    if (typeof dayOfWeek === 'number') return dayOfWeek;
    const dayMap: Record<string, number> = {
      'sunday': 0, 'monday': 1, 'tuesday': 2, 'wednesday': 3,
      'thursday': 4, 'friday': 5, 'saturday': 6
    };
    return dayMap[dayOfWeek.toLowerCase()] ?? 0;
  }

  // Preset cron intervals to reduce sprawl
  private readonly CRON_SCHEDULES: Record<string, CronSchedule> = {
    '5min': { interval: '5min', cron_expression: '*/5 * * * *' },
    '15min': { interval: '15min', cron_expression: '*/15 * * * *' },
    '30min': { interval: '30min', cron_expression: '*/30 * * * *' },
    '1hr': { interval: '1hr', cron_expression: '0 * * * *' },
    '6hr': { interval: '6hr', cron_expression: '0 */6 * * *' },
    'daily': { interval: 'daily', cron_expression: '0 0 * * *' },
    'weekly': { interval: 'weekly', cron_expression: '0 0 * * 0' },
    'once': { interval: 'once', cron_expression: '*/5 * * * *' }  // Check every 5 min for due one-time jobs
  };

  // Get scheduled automations due for execution
  private async getScheduledAutomations(interval: string): Promise<ScheduledAutomation[]> {
    try {
      const currentTime = new Date();
      const intervalMinutes = this.getIntervalMinutes(interval);
      // Add 10-minute buffer to cutoff to account for execution time variation within each run.
      // Without this buffer, automations that execute late in a batch (e.g., 16:00:50) would be
      // skipped the next day if the scheduler runs at exactly 16:00:00, causing alternating-day execution.
      const bufferMinutes = 10;
      const cutoffTime = new Date(currentTime.getTime() - ((intervalMinutes - bufferMinutes) * 60 * 1000));

      // Step 1: Fetch automations (no subquery - PostgREST doesn't support them)
      const { data: automations, error } = await this.supabaseClient
        .from('automation_records')
        .select('id, user_id, name, script_code, actions, trigger_type, trigger_config, execution_params, dependencies')
        .eq('active', true)
        .in('trigger_type', ['schedule_once', 'schedule_recurring'])
        .order('created_at', { ascending: true });

      if (error) {
        console.error("Failed to fetch scheduled automations:", error);
        return [];
      }

      if (!automations || automations.length === 0) {
        console.log(`No scheduled automations found`);
        return [];
      }

      // Step 2: Filter by interval (can't use jsonb filter reliably in all cases)
      const matchingAutomations = automations.filter(a =>
        a.trigger_config?.interval === interval
      );

      if (matchingAutomations.length === 0) {
        console.log(`No automations found for interval: ${interval}`);
        return [];
      }

      // Step 3: Fetch last SCHEDULED execution times for matching automations
      // Only count scheduled executions, not manual triggers - this allows
      // manual testing without blocking the next scheduled run
      const automationIds = matchingAutomations.map(a => a.id);
      const { data: execLogs, error: execError } = await this.supabaseClient
        .from('automation_execution_logs')
        .select('automation_id, created_at, trigger_type')
        .in('automation_id', automationIds)
        .in('trigger_type', ['schedule', 'schedule_once', 'schedule_recurring'])
        .order('created_at', { ascending: false });

      // Build a map of automation_id -> last_executed_at
      const lastExecutedMap: Record<string, string> = {};
      if (execLogs && !execError) {
        for (const log of execLogs) {
          // Only keep the first (most recent) execution per automation
          if (!lastExecutedMap[log.automation_id]) {
            lastExecutedMap[log.automation_id] = log.created_at;
          }
        }
      }

      // Step 4: Filter out automations that have been executed recently or aren't due
      const dueAutomations = matchingAutomations.filter(automation => {
        const triggerConfig = automation.trigger_config || {};

        // Check if automation hasn't been executed recently
        const lastExecutedAt = lastExecutedMap[automation.id];
        if (lastExecutedAt) {
          const lastExecuted = new Date(lastExecutedAt);
          if (lastExecuted > cutoffTime) {
            return false; // Already executed within this interval
          }
        }

        // Check specific time constraints for daily/weekly automations (5-minute precision)
        if (triggerConfig.time_of_day && (interval === 'daily' || interval === 'weekly')) {
          const [targetHour, targetMinute] = triggerConfig.time_of_day.split(':').map(Number);
          const currentHour = currentTime.getUTCHours();
          const currentMinute = currentTime.getUTCMinutes();

          // Convert to total minutes for easier comparison
          const targetTotalMinutes = targetHour * 60 + (targetMinute || 0);
          const currentTotalMinutes = currentHour * 60 + currentMinute;

          // Calculate the 5-minute window containing current time
          const windowStart = Math.floor(currentTotalMinutes / 5) * 5;
          const windowEnd = windowStart + 5;

          // Only run if target time falls within current 5-minute window
          if (targetTotalMinutes < windowStart || targetTotalMinutes >= windowEnd) {
            return false;
          }

          // For weekly automations, also check day_of_week
          if (interval === 'weekly' && triggerConfig.day_of_week != null) {
            const targetDay = this.getDayNumber(triggerConfig.day_of_week);
            const currentDay = currentTime.getUTCDay();
            if (targetDay !== currentDay) {
              return false;
            }
          }
        }

        // For weekly without time_of_day, check day_of_week only
        if (interval === 'weekly' && !triggerConfig.time_of_day && triggerConfig.day_of_week != null) {
          const targetDay = this.getDayNumber(triggerConfig.day_of_week);
          const currentDay = currentTime.getUTCDay();
          if (targetDay !== currentDay) {
            return false;
          }
        }

        return true;
      });

      // Add last_executed_at to the automations for logging
      const enrichedAutomations = dueAutomations.map(a => ({
        ...a,
        last_executed_at: lastExecutedMap[a.id] || null
      }));

      console.log(`Found ${enrichedAutomations.length} automations due for ${interval} execution (of ${matchingAutomations.length} total for this interval)`);
      return enrichedAutomations;

    } catch (error) {
      console.error("Error fetching scheduled automations:", error);
      return [];
    }
  }

  // Convert interval string to minutes
  private getIntervalMinutes(interval: string): number {
    switch (interval) {
      case '5min': return 5;
      case '15min': return 15;
      case '30min': return 30;
      case '1hr': return 60;
      case '6hr': return 360;
      case 'daily': return 1440;
      case 'weekly': return 10080;
      case 'once': return 5;  // Check every 5 min
      default: return 60;
    }
  }

  // Get one-time automations that are due for execution
  private async getOneTimeAutomations(): Promise<ScheduledAutomation[]> {
    try {
      const currentTime = new Date();

      // Fetch all active one-time scheduled automations
      const { data: automations, error } = await this.supabaseClient
        .from('automation_records')
        .select('id, user_id, name, script_code, actions, trigger_type, trigger_config, execution_params, dependencies')
        .eq('active', true)
        .in('trigger_type', ['schedule_once', 'schedule_recurring'])
        .order('created_at', { ascending: true });

      if (error) {
        console.error("Failed to fetch one-time automations:", error);
        return [];
      }

      if (!automations || automations.length === 0) {
        return [];
      }

      // Filter for one-time automations where run_at has passed
      const dueAutomations = automations.filter(a => {
        const triggerConfig = a.trigger_config || {};

        // Must be a one-time schedule
        if (triggerConfig.interval !== 'once' && triggerConfig.type !== 'once') {
          return false;
        }

        // Must have a run_at time
        const runAt = triggerConfig.run_at;
        if (!runAt) {
          console.warn(`One-time automation ${a.id} missing run_at`);
          return false;
        }

        // Check if run_at has passed
        const runAtTime = new Date(runAt);
        return runAtTime <= currentTime;
      });

      console.log(`Found ${dueAutomations.length} one-time automations due for execution`);
      return dueAutomations;

    } catch (error) {
      console.error("Error fetching one-time automations:", error);
      return [];
    }
  }

  // Deactivate automation after one-time execution
  private async deactivateAutomation(automationId: string): Promise<void> {
    try {
      const { error } = await this.supabaseClient
        .from('automation_records')
        .update({ active: false })
        .eq('id', automationId);

      if (error) {
        console.error(`Failed to deactivate automation ${automationId}:`, error);
      } else {
        console.log(`Deactivated one-time automation ${automationId}`);
      }
    } catch (error) {
      console.error(`Error deactivating automation ${automationId}:`, error);
    }
  }

  // Execute scheduled automation
  private async executeScheduledAutomation(
    automation: ScheduledAutomation,
    scheduledTime: string
  ): Promise<boolean> {
    try {
      console.log(`Executing scheduled automation ${automation.id} (${automation.name})`);

      // Call script executor with service role permissions
      const scriptExecutorUrl = `${Deno.env.get("SUPABASE_URL")}/functions/v1/script-executor/scheduled`;
      
      const response = await fetch(scriptExecutorUrl, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")}`
        },
        body: JSON.stringify({
          automation_id: automation.id,
          scheduled_time: scheduledTime,
          trigger_data: {
            trigger_type: automation.trigger_type,
            scheduled_time: scheduledTime,
            interval: automation.trigger_config?.interval,
            automation_name: automation.name
          }
        })
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`Scheduled execution failed for automation ${automation.id}: ${errorText}`);
        return false;
      }

      const result = await response.json();
      console.log(`Scheduled automation ${automation.id} executed:`, result.execution_id);
      
      return result.success;

    } catch (error) {
      console.error(`Failed to execute scheduled automation ${automation.id}:`, error);
      return false;
    }
  }

  // Run scheduled automations for a specific interval
  async runScheduledInterval(req: Request): Promise<Response> {
    try {
      const { interval } = await req.json();

      if (!interval || !this.CRON_SCHEDULES[interval]) {
        return this.createErrorResponse(
          `Invalid interval. Supported: ${Object.keys(this.CRON_SCHEDULES).join(', ')}`,
          400
        );
      }

      const scheduledTime = new Date().toISOString();
      console.log(`Running ${interval} scheduled automations at ${scheduledTime}`);

      // Get due automations - use special method for one-time jobs
      const dueAutomations = interval === 'once'
        ? await this.getOneTimeAutomations()
        : await this.getScheduledAutomations(interval);

      if (dueAutomations.length === 0) {
        return this.createSuccessResponse({
          message: `No ${interval} automations due for execution`,
          interval,
          scheduled_time: scheduledTime,
          executed: 0
        });
      }

      // Execute automations with concurrency limit
      const maxConcurrent = 5;
      const results: { automation: ScheduledAutomation; success: boolean }[] = [];

      for (let i = 0; i < dueAutomations.length; i += maxConcurrent) {
        const batch = dueAutomations.slice(i, i + maxConcurrent);

        const batchPromises = batch.map(async (automation) => {
          const success = await this.executeScheduledAutomation(automation, scheduledTime);
          return { automation, success };
        });

        const batchResults = await Promise.all(batchPromises);
        results.push(...batchResults);

        // Small delay between batches to prevent overwhelming
        if (i + maxConcurrent < dueAutomations.length) {
          await new Promise(resolve => setTimeout(resolve, 1000));
        }
      }

      // For one-time jobs, deactivate successfully executed automations
      if (interval === 'once') {
        for (const result of results) {
          if (result.success) {
            await this.deactivateAutomation(result.automation.id);
          }
        }
      }

      // Count successful executions
      const successCount = results.filter(r => r.success).length;
      const failureCount = results.length - successCount;

      const response = {
        message: `${interval} scheduled automations executed`,
        interval,
        scheduled_time: scheduledTime,
        total_due: dueAutomations.length,
        executed: successCount,
        failed: failureCount,
        deactivated: interval === 'once' ? successCount : 0,
        automations: dueAutomations.map(a => ({
          id: a.id,
          name: a.name,
          user_id: a.user_id
        }))
      };

      console.log("Scheduled execution results:", response);
      return this.createSuccessResponse(response);

    } catch (error) {
      console.error("Scheduled interval execution error:", error);
      return this.createErrorResponse("Scheduled execution failed", 500);
    }
  }

  // Get next scheduled runs for monitoring
  async getScheduledRuns(req: Request): Promise<Response> {
    try {
      const { interval, user_id, limit = 20 } = await req.json().catch(() => ({}));

      // Step 1: Fetch automations (no subquery)
      let query = this.supabaseClient
        .from('automation_records')
        .select('id, name, user_id, trigger_config, created_at')
        .eq('active', true)
        .in('trigger_type', ['schedule_once', 'schedule_recurring'])
        .order('created_at', { ascending: true })
        .limit(limit);

      if (user_id) {
        query = query.eq('user_id', user_id);
      }

      const { data: automations, error } = await query;

      if (error) {
        return this.createErrorResponse(`Failed to fetch scheduled automations: ${error.message}`, 500);
      }

      if (!automations || automations.length === 0) {
        return this.createSuccessResponse({
          scheduled_runs: [],
          total_count: 0,
          overdue_count: 0
        });
      }

      // Filter by interval if specified (do this in JS since JSONB filtering can be finicky)
      let filteredAutomations = automations;
      if (interval) {
        filteredAutomations = automations.filter(a => a.trigger_config?.interval === interval);
      }

      // Step 2: Fetch last execution times
      const automationIds = filteredAutomations.map(a => a.id);
      const { data: execLogs } = await this.supabaseClient
        .from('automation_execution_logs')
        .select('automation_id, created_at')
        .in('automation_id', automationIds)
        .order('created_at', { ascending: false });

      // Build last executed map
      const lastExecutedMap: Record<string, string> = {};
      if (execLogs) {
        for (const log of execLogs) {
          if (!lastExecutedMap[log.automation_id]) {
            lastExecutedMap[log.automation_id] = log.created_at;
          }
        }
      }

      // Calculate next run times
      const currentTime = new Date();
      const scheduledRuns = filteredAutomations.map(automation => {
        const triggerConfig = automation.trigger_config || {};
        const automationInterval = triggerConfig.interval || 'daily';
        const intervalMinutes = this.getIntervalMinutes(automationInterval);
        const lastExecutedAt = lastExecutedMap[automation.id];

        let nextRunTime = new Date(currentTime);

        if (lastExecutedAt) {
          const lastExecuted = new Date(lastExecutedAt);
          nextRunTime = new Date(lastExecuted.getTime() + (intervalMinutes * 60 * 1000));
        }

        // Handle daily scheduling with specific time
        if (automationInterval === 'daily' && triggerConfig.time_of_day) {
          const [hour, minute] = triggerConfig.time_of_day.split(':').map(Number);
          nextRunTime.setUTCHours(hour, minute || 0, 0, 0);

          // If time has passed today, schedule for tomorrow
          if (nextRunTime <= currentTime) {
            nextRunTime.setDate(nextRunTime.getDate() + 1);
          }
        }

        // Handle weekly scheduling with specific time and/or day
        if (automationInterval === 'weekly') {
          // Set time if specified, otherwise default to midnight
          if (triggerConfig.time_of_day) {
            const [hour, minute] = triggerConfig.time_of_day.split(':').map(Number);
            nextRunTime.setUTCHours(hour, minute || 0, 0, 0);
          } else {
            nextRunTime.setUTCHours(0, 0, 0, 0);
          }

          // Calculate target day (default to Sunday if not specified)
          const targetDay = this.getDayNumber(triggerConfig.day_of_week);
          const currentDay = nextRunTime.getUTCDay();
          let daysUntilTarget = targetDay - currentDay;

          // If target day has passed this week (or same day but time passed), schedule for next week
          if (daysUntilTarget < 0 || (daysUntilTarget === 0 && nextRunTime <= currentTime)) {
            daysUntilTarget += 7;
          }

          if (daysUntilTarget > 0) {
            nextRunTime.setDate(nextRunTime.getDate() + daysUntilTarget);
          }
        }

        return {
          automation_id: automation.id,
          automation_name: automation.name,
          user_id: automation.user_id,
          interval: automationInterval,
          time_of_day: triggerConfig.time_of_day || null,
          day_of_week: triggerConfig.day_of_week || null,
          last_executed_at: lastExecutedAt || null,
          next_run_time: nextRunTime.toISOString(),
          is_overdue: nextRunTime < currentTime
        };
      });

      return this.createSuccessResponse({
        scheduled_runs: scheduledRuns,
        total_count: scheduledRuns.length,
        overdue_count: scheduledRuns.filter(run => run.is_overdue).length
      });

    } catch (error) {
      console.error("Error getting scheduled runs:", error);
      return this.createErrorResponse("Failed to get scheduled runs", 500);
    }
  }

  // Service categories for filtering
  private readonly SERVICE_CATEGORIES: Record<string, string[]> = {
    'health': ['oura', 'fitbit', 'apple health', 'google health connect'],
    'email': ['gmail'],
    'calendar': ['google calendar'],
    'messaging': ['slack'],
    'productivity': ['todoist', 'notion'],
    'documents': ['gsheets', 'gdocs', 'excel', 'word', 'google sheets', 'google docs']
  };

  // Run scheduled polling for automations with trigger_type='polling'
  async runScheduledPolling(req: Request): Promise<Response> {
    try {
      // Parse optional filters from request body
      let category: string | null = null;
      let automationId: string | null = null;
      try {
        const body = await req.json();
        category = body?.category || null;
        automationId = body?.automation_id || null;
      } catch {
        // No body or invalid JSON - poll all automations
      }

      // If automation_id provided, force-poll that specific automation (ignore next_poll_at)
      if (automationId) {
        console.log(`Force-polling specific automation: ${automationId}`);
        const automation = await this.pollingManager.getPollingAutomationById(automationId);

        if (!automation) {
          return this.createErrorResponse('Polling automation not found or not active', 404);
        }

        const result = await this.pollingManager.pollAutomation(automation);

        return this.createSuccessResponse({
          message: 'Force-poll completed',
          automation_id: automationId,
          automation_name: automation.name,
          timestamp: new Date().toISOString(),
          items_found: result.items_found,
          events_created: result.events_created,
          success: result.success,
          error: result.error
        });
      }

      console.log(`Starting scheduled polling run... ${category ? `(category: ${category})` : '(all automations)'}`);

      // Get all polling automations that are due
      let automations = await this.pollingManager.getPollingAutomationsDue();

      // Filter by category if specified
      if (category && this.SERVICE_CATEGORIES[category]) {
        const allowedServices = this.SERVICE_CATEGORIES[category];
        automations = automations.filter(a =>
          allowedServices.includes(a.trigger_config?.service?.toLowerCase())
        );
        console.log(`Filtered to ${automations.length} automations in category '${category}'`);
      }

      console.log(`Found ${automations.length} polling automations due for execution`);

      if (automations.length === 0) {
        return this.createSuccessResponse({
          message: category ? `No polling automations due in category '${category}'` : 'No polling automations due',
          category: category || 'all',
          polled_automations: 0,
          timestamp: new Date().toISOString()
        });
      }

      // Poll automations in parallel (with concurrency limit)
      const results = await this.pollAutomationsInBatches(automations, 5);

      // Aggregate results
      const summary = this.aggregatePollingResults(results);

      console.log(`Polling completed: ${summary.successful_polls}/${summary.total_polls} successful`);

      return this.createSuccessResponse({
        message: category ? `Polling completed for category '${category}'` : 'Scheduled polling completed',
        category: category || 'all',
        timestamp: new Date().toISOString(),
        ...summary
      });

    } catch (error) {
      console.error('Scheduled polling error:', error);
      return this.createErrorResponse('Failed to run scheduled polling', 500);
    }
  }

  // Poll automations in batches to manage concurrency
  private async pollAutomationsInBatches(automations: any[], batchSize: number): Promise<any[]> {
    const results = [];

    for (let i = 0; i < automations.length; i += batchSize) {
      const batch = automations.slice(i, i + batchSize);
      console.log(`Polling batch ${Math.floor(i / batchSize) + 1}/${Math.ceil(automations.length / batchSize)}`);

      const batchPromises = batch.map(async (automation) => {
        const startTime = Date.now();
        try {
          const result = await this.pollingManager.pollAutomation(automation);
          const duration = Date.now() - startTime;

          // Track polling usage
          await this.trackUsage(
            automation.user_id,
            automation.id,
            automation.trigger_config?.service || 'unknown',
            0, // No tokens used for polling
            duration
          );

          return {
            ...result,
            automation_id: automation.id,
            automation_name: automation.name,
            user_id: automation.user_id,
            service_name: automation.trigger_config?.service,
            duration_ms: duration
          };
        } catch (error) {
          return {
            success: false,
            items_found: 0,
            events_created: 0,
            error: error.message,
            automation_id: automation.id,
            automation_name: automation.name,
            user_id: automation.user_id,
            service_name: automation.trigger_config?.service,
            duration_ms: Date.now() - startTime
          };
        }
      });

      const batchResults = await Promise.all(batchPromises);
      results.push(...batchResults);

      // Add small delay between batches to avoid overwhelming services
      if (i + batchSize < automations.length) {
        await new Promise(resolve => setTimeout(resolve, 1000));
      }
    }

    return results;
  }

  // Aggregate polling results
  private aggregatePollingResults(results: any[]): any {
    return {
      total_polls: results.length,
      successful_polls: results.filter(r => r.success).length,
      failed_polls: results.filter(r => !r.success).length,
      total_items_found: results.reduce((sum, r) => sum + (r.items_found || 0), 0),
      total_events_created: results.reduce((sum, r) => sum + (r.events_created || 0), 0),
      average_duration_ms: results.length > 0 ?
        Math.round(results.reduce((sum, r) => sum + (r.duration_ms || 0), 0) / results.length) : 0,
      automations_polled: results.map(r => ({
        id: r.automation_id,
        name: r.automation_name,
        service: r.service_name
      })),
      errors: results.filter(r => !r.success).map(r => ({
        automation_id: r.automation_id,
        automation_name: r.automation_name,
        service: r.service_name,
        user_id: r.user_id,
        error: r.error
      }))
    };
  }

  // Manually trigger a scheduled automation
  async triggerScheduledAutomation(req: Request): Promise<Response> {
    try {
      const { automation_id, user_id } = await req.json();

      if (!automation_id) {
        return this.createErrorResponse("automation_id is required", 400);
      }

      // Verify automation exists and belongs to user (if user_id provided)
      let query = this.supabaseClient
        .from('automation_records')
        .select('*')
        .eq('id', automation_id)
        .eq('active', true)
        .in('trigger_type', ['schedule_once', 'schedule_recurring']);

      if (user_id) {
        query = query.eq('user_id', user_id);
      }

      const { data: automation, error } = await query.single();

      if (error || !automation) {
        return this.createErrorResponse("Scheduled automation not found", 404);
      }

      // Execute automation
      const scheduledTime = new Date().toISOString();
      const success = await this.executeScheduledAutomation(automation, scheduledTime);

      if (success) {
        return this.createSuccessResponse({
          message: "Scheduled automation triggered successfully",
          automation_id,
          automation_name: automation.name,
          triggered_at: scheduledTime
        });
      } else {
        return this.createErrorResponse("Failed to trigger scheduled automation", 500);
      }

    } catch (error) {
      console.error("Manual trigger error:", error);
      return this.createErrorResponse("Failed to trigger automation", 500);
    }
  }
}

// Main handler with routing
Deno.serve(async (req) => {
  const scheduler = new SchedulerRunner();
  
  // Handle CORS
  const corsResponse = scheduler.handleCors(req);
  if (corsResponse) return corsResponse;

  const url = new URL(req.url);
  const path = url.pathname;

  try {
    // Route to appropriate handler
    if (path.includes('/run')) {
      return await scheduler.runScheduledInterval(req);
    } else if (path.includes('/polling')) {
      return await scheduler.runScheduledPolling(req);
    } else if (path.includes('/scheduled-runs')) {
      return await scheduler.getScheduledRuns(req);
    } else if (path.includes('/trigger')) {
      return await scheduler.triggerScheduledAutomation(req);
    } else {
      return await scheduler.runScheduledInterval(req);
    }
  } catch (error) {
    console.error("Scheduler routing error:", error);
    return scheduler.createErrorResponse("Internal server error", 500);
  }
});

/**
 * API Endpoints:
 *
 * 1. Run Scheduled Interval:
 * POST /functions/v1/scheduler-runner/run
 * Body: { "interval": "5min" | "15min" | "30min" | "1hr" | "6hr" | "daily" }
 *
 * 2. Run Service Polling:
 * GET /functions/v1/scheduler-runner/polling
 * (No body required - polls all active services)
 *
 * 3. Get Scheduled Runs:
 * POST /functions/v1/scheduler-runner/scheduled-runs
 * Body: { "interval": "daily", "user_id": "uuid", "limit": 20 }
 *
 * 4. Manual Trigger:
 * POST /functions/v1/scheduler-runner/trigger
 * Body: { "automation_id": "uuid", "user_id": "uuid" }
 *
 * Cron Jobs (to be set up):
 * - Scheduled Automations:
 *   - 5min: every 5 minutes (call with interval="5min")
 *   - 15min: every 15 minutes (call with interval="15min")  
 *   - 30min: every 30 minutes (call with interval="30min")
 *   - 1hr: hourly (call with interval="1hr")
 *   - 6hr: every 6 hours (call with interval="6hr")  
 *   - Daily: daily at midnight (call with interval="daily")
 *
 * - Service Polling:
 *   - Every 5 minutes: GET /polling
 *
 * Environment Variables:
 * - SUPABASE_URL: Supabase project URL
 * - SUPABASE_SERVICE_ROLE_KEY: Service role key for API calls
 */
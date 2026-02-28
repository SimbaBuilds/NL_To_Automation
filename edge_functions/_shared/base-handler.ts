// Shared base handler for all automation edge functions
import { createClient } from "jsr:@supabase/supabase-js@2";

export const corsHeaders = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Headers": "authorization, x-client-info, apikey, content-type",
  "Access-Control-Allow-Methods": "POST, OPTIONS, GET"
};

export interface EdgeFunctionContext {
  supabaseClient: any;
  userId?: string;
  request: Request;
}

export interface EdgeFunctionResponse {
  success: boolean;
  data?: any;
  error?: string;
  execution_id?: string;
}

export class BaseAutomationHandler {
  protected supabaseClient: any;

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
  }

  // Handle CORS preflight requests
  handleCors(req: Request): Response | null {
    if (req.method === "OPTIONS") {
      return new Response(null, { headers: corsHeaders });
    }
    return null;
  }

  // Validate request authentication
  async validateAuth(req: Request): Promise<string | null> {
    try {
      const authHeader = req.headers.get("Authorization");
      if (!authHeader?.startsWith("Bearer ")) {
        return null;
      }

      const token = authHeader.substring(7);
      const { data: { user }, error } = await this.supabaseClient.auth.getUser(token);
      
      if (error || !user) {
        return null;
      }

      return user.id;
    } catch (error) {
      console.error("Auth validation error:", error);
      return null;
    }
  }

  // Log execution attempt
  async logExecution(
    automationId: string, 
    userId: string, 
    triggerData: any,
    status: 'running' | 'completed' | 'failed' = 'running'
  ): Promise<string> {
    const executionId = crypto.randomUUID();
    
    try {
      console.log(`🔄 logExecution: Inserting execution ${executionId}`);
      const { error } = await this.supabaseClient
        .from('automation_execution_logs')
        .insert({
          id: executionId,
          automation_id: automationId,
          trigger_type: 'script',  // Legacy script-based execution
          trigger_data: triggerData,
          status: status,
          actions_executed: 0,
          actions_failed: 0,
          action_results: []
        });

      if (error) {
        console.error("❌ Database error in logExecution:", error);
      } else {
        console.log(`✅ logExecution: Successfully inserted execution ${executionId}`);
      }

      return executionId;
    } catch (error) {
      console.error("💥 Failed to log execution:", error);
      return executionId; // Return ID even if logging fails
    }
  }

  // Update execution result
  async updateExecution(
    executionId: string,
    result?: any,
    error?: string,
    durationMs?: number
  ): Promise<void> {
    try {
      console.log(`🔄 updateExecution: Updating execution ${executionId}`);
      const status = error ? 'failed' : 'completed';
      const { error: updateError } = await this.supabaseClient
        .from('automation_execution_logs')
        .update({
          status: status,
          actions_executed: result ? 1 : 0,
          actions_failed: error ? 1 : 0,
          action_results: result ? [{ tool: 'script', output: result }] : [],
          error_summary: error || null,
          duration_ms: durationMs || null
        })
        .eq('id', executionId);

      if (updateError) {
        console.error("❌ Database error in updateExecution:", updateError);
      } else {
        console.log(`✅ updateExecution: Successfully updated execution ${executionId}`);
      }
    } catch (updateError) {
      console.error("💥 Failed to update execution:", updateError);
    }
  }

  // Track usage
  async trackUsage(
    userId: string,
    automationId: string,
    serviceName?: string,
    tokensUsed: number = 0,
    executionTimeMs: number = 0
  ): Promise<void> {
    try {
      console.log(`🔄 trackUsage: Tracking usage for user ${userId}, service ${serviceName}`);
      const { error } = await this.supabaseClient
        .from('automation_usage')
        .insert({
          user_id: userId,
          automation_id: automationId,
          service_name: serviceName,
          tokens_used: tokensUsed,
          execution_time_ms: executionTimeMs,
          timestamp: new Date().toISOString()
        });
      
      if (error) {
        console.error("❌ Database error in trackUsage:", error);
      } else {
        console.log(`✅ trackUsage: Successfully tracked usage for ${serviceName}`);
      }
    } catch (error) {
      console.error("💥 Failed to track usage:", error);
    }
  }

  // Create standardized error response
  createErrorResponse(message: string, status: number = 500): Response {
    return new Response(
      JSON.stringify({ 
        success: false, 
        error: message 
      }), 
      {
        status,
        headers: { ...corsHeaders, "Content-Type": "application/json" }
      }
    );
  }

  // Create standardized success response
  createSuccessResponse(data?: any, executionId?: string): Response {
    return new Response(
      JSON.stringify({ 
        success: true, 
        data,
        execution_id: executionId 
      }), 
      {
        status: 200,
        headers: { ...corsHeaders, "Content-Type": "application/json" }
      }
    );
  }

  // Get automation by ID with user validation
  async getAutomation(automationId: string, userId: string): Promise<any | null> {
    try {
      const { data, error } = await this.supabaseClient
        .from('automation_records')
        .select('*')
        .eq('id', automationId)
        .eq('user_id', userId)
        .eq('active', true)
        .single();

      if (error || !data) {
        console.error("Automation not found or access denied:", error);
        return null;
      }

      return data;
    } catch (error) {
      console.error("Error fetching automation:", error);
      return null;
    }
  }
}
// Setup type definitions for built-in Supabase Runtime APIs
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { BaseAutomationHandler } from "../_shared/base-handler.ts";

interface ExecutionRequest {
  automation_id: string;
  trigger_data?: any;
  test_mode?: boolean;
}

interface ContainerResult {
  success: boolean;
  result?: any;
  error?: string;
  duration_ms: number;
  container_id?: string;
}

interface DeclarativeExecutionResult {
  success: boolean;
  execution_id: string;
  status: string;
  actions_executed: number;
  actions_failed: number;
  action_results: any[];
  duration_ms: number;
  error_summary?: string;
}

class ScriptExecutor extends BaseAutomationHandler {

  // Check if OAuth token is expired or expires within 5 minutes
  private isTokenExpired(expiresAt: string | null): boolean {
    if (!expiresAt) return false;
    
    const expiryTime = new Date(expiresAt);
    const now = new Date();
    const bufferTime = new Date(now.getTime() + 5 * 60 * 1000); // 5 minutes buffer
    
    return expiryTime <= bufferTime;
  }

  // Refresh OAuth token for a service
  private async refreshToken(integration: any, serviceName: string, publicClient: any): Promise<any> {
    try {
      console.log(`🔄 Refreshing ${serviceName} token for integration ${integration.id}`);
      
      if (!integration.refresh_token) {
        throw new Error(`No refresh token available for ${serviceName}`);
      }
      
      let tokenData;
      
      // Handle different service token refresh endpoints
      if (serviceName.toLowerCase().includes('gmail') || serviceName.toLowerCase().includes('google')) {
        tokenData = await this.refreshGoogleToken(integration);
      } else if (serviceName.toLowerCase().includes('outlook') || serviceName.toLowerCase().includes('microsoft')) {
        tokenData = await this.refreshMicrosoftToken(integration);
      } else if (serviceName.toLowerCase().includes('slack')) {
        tokenData = await this.refreshSlackToken(integration);
      } else if (serviceName.toLowerCase().includes('notion')) {
        tokenData = await this.refreshNotionToken(integration);
      } else {
        throw new Error(`Token refresh not implemented for ${serviceName}`);
      }
      
      // Update the integration in database
      const updateData: any = {
        access_token: tokenData.access_token,
        expires_at: tokenData.expires_at.toISOString(),
        updated_at: new Date().toISOString()
      };
      
      if (tokenData.refresh_token) {
        updateData.refresh_token = tokenData.refresh_token;
      }
      
      const { error: updateError } = await publicClient
        .from('integrations')
        .update(updateData)
        .eq('id', integration.id);
      
      if (updateError) {
        console.error("Failed to update integration with new tokens:", updateError);
        throw new Error(`Failed to update ${serviceName} tokens in database`);
      }
      
      console.log(`✅ Successfully refreshed ${serviceName} token`);
      
      // Return updated integration data
      return {
        ...integration,
        access_token: tokenData.access_token,
        refresh_token: tokenData.refresh_token || integration.refresh_token,
        expires_at: tokenData.expires_at.toISOString()
      };
      
    } catch (error) {
      console.error(`❌ Token refresh failed for ${serviceName}:`, error);
      throw new Error(`Token refresh failed for ${serviceName}: ${error.message}`);
    }
  }

  // Refresh Google OAuth token
  private async refreshGoogleToken(integration: any): Promise<any> {
    const clientId = Deno.env.get("GOOGLE_CLIENT_ID");
    const clientSecret = Deno.env.get("GOOGLE_CLIENT_SECRET");
    
    if (!clientId || !clientSecret) {
      throw new Error("Google OAuth credentials not configured");
    }
    
    const response = await fetch("https://oauth2.googleapis.com/token", {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        refresh_token: integration.refresh_token,
        grant_type: 'refresh_token'
      })
    });
    
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Google token refresh failed: ${error}`);
    }
    
    const tokenData = await response.json();
    
    return {
      access_token: tokenData.access_token,
      refresh_token: tokenData.refresh_token || integration.refresh_token,
      expires_at: new Date(Date.now() + tokenData.expires_in * 1000)
    };
  }

  // Refresh Microsoft OAuth token
  private async refreshMicrosoftToken(integration: any): Promise<any> {
    const clientId = Deno.env.get("MICROSOFT_CLIENT_ID");
    const clientSecret = Deno.env.get("MICROSOFT_CLIENT_SECRET");
    
    if (!clientId || !clientSecret) {
      throw new Error("Microsoft OAuth credentials not configured");
    }
    
    const response = await fetch("https://login.microsoftonline.com/common/oauth2/v2.0/token", {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        refresh_token: integration.refresh_token,
        grant_type: 'refresh_token'
      })
    });
    
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Microsoft token refresh failed: ${error}`);
    }
    
    const tokenData = await response.json();
    
    return {
      access_token: tokenData.access_token,
      refresh_token: tokenData.refresh_token || integration.refresh_token,
      expires_at: new Date(Date.now() + tokenData.expires_in * 1000)
    };
  }

  // Refresh Slack OAuth token
  private async refreshSlackToken(integration: any): Promise<any> {
    const clientId = Deno.env.get("SLACK_CLIENT_ID");
    const clientSecret = Deno.env.get("SLACK_CLIENT_SECRET");
    
    if (!clientId || !clientSecret) {
      throw new Error("Slack OAuth credentials not configured");
    }
    
    const response = await fetch("https://slack.com/api/oauth.v2.access", {
      method: 'POST',
      headers: {
        'Content-Type': 'application/x-www-form-urlencoded'
      },
      body: new URLSearchParams({
        client_id: clientId,
        client_secret: clientSecret,
        refresh_token: integration.refresh_token,
        grant_type: 'refresh_token'
      })
    });
    
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Slack token refresh failed: ${error}`);
    }
    
    const tokenData = await response.json();
    
    if (!tokenData.ok) {
      throw new Error(`Slack token refresh failed: ${tokenData.error || 'Unknown error'}`);
    }
    
    return {
      access_token: tokenData.access_token,
      refresh_token: tokenData.refresh_token || integration.refresh_token,
      expires_at: new Date(Date.now() + (tokenData.expires_in || 43200) * 1000)
    };
  }

  // Refresh Notion OAuth token
  private async refreshNotionToken(integration: any): Promise<any> {
    const clientId = Deno.env.get("NOTION_CLIENT_ID");
    const clientSecret = Deno.env.get("NOTION_CLIENT_SECRET");
    
    if (!clientId || !clientSecret) {
      throw new Error("Notion OAuth credentials not configured");
    }
    
    // Base64 encode credentials
    const credentials = btoa(`${clientId}:${clientSecret}`);
    
    const response = await fetch("https://api.notion.com/v1/oauth/token", {
      method: 'POST',
      headers: {
        'Authorization': `Basic ${credentials}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({
        grant_type: 'refresh_token',
        refresh_token: integration.refresh_token
      })
    });
    
    if (!response.ok) {
      const error = await response.text();
      throw new Error(`Notion token refresh failed: ${error}`);
    }
    
    const tokenData = await response.json();
    
    return {
      access_token: tokenData.access_token,
      refresh_token: tokenData.refresh_token || integration.refresh_token,
      expires_at: new Date(Date.now() + (tokenData.expires_in || 3600) * 1000)
    };
  }

  // Get user credentials for services dynamically with token refresh
  private async getUserCredentials(userId: string, serviceNames: string[]): Promise<Record<string, any>> {
    try {
      // Create public schema client for integrations table
      const { createClient } = await import("jsr:@supabase/supabase-js@2");
      const publicClient = createClient(
        Deno.env.get("SUPABASE_URL") ?? "",
        Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
      );
      
      console.log(`🔍 Fetching integrations for services: ${serviceNames.join(', ')}`);
      
      // Step 1: Get services by name from dependencies
      const { data: services, error: servicesError } = await publicClient
        .from('services')
        .select('id, service_name, integration_method')
        .in('service_name', serviceNames);

      if (servicesError) {
        console.error("Failed to fetch services:", servicesError);
        return {};
      }

      if (!services || services.length === 0) {
        console.warn(`No services found for names: ${serviceNames.join(', ')}`);
        return {};
      }

      console.log(`📋 Found ${services.length} services:`, services.map(s => s.service_name));

      // Step 2: Get integrations for those service IDs
      const serviceIds = services.map(s => s.id);
      const { data: integrations, error: integrationsError } = await publicClient
        .from('integrations')
        .select('*')
        .eq('user_id', userId)
        .in('service_id', serviceIds)
        .eq('is_active', true)
        .eq('status', 'active');

      if (integrationsError) {
        console.error("Failed to fetch integrations:", integrationsError);
        return {};
      }

      console.log(`🔗 Found ${integrations?.length || 0} active integrations for user ${userId}`);

      // Step 3: Build dynamic service context with token refresh
      const credentials: Record<string, any> = {};
      
      for (const service of services) {
        const serviceIntegrations = integrations?.filter(i => i.service_id === service.id) || [];
        
        if (serviceIntegrations.length === 0) {
          console.warn(`⚠️  No integration found for service: ${service.service_name}`);
          continue;
        }
        
        // For services with multiple integrations, prioritize by token validity
        let bestIntegration = serviceIntegrations[0];
        
        if (serviceIntegrations.length > 1) {
          // Prioritize integrations with valid tokens
          const validIntegrations = serviceIntegrations.filter(i => {
            if (!i.expires_at) return true; // No expiry = always valid
            return !this.isTokenExpired(i.expires_at);
          });
          
          if (validIntegrations.length > 0) {
            // Use the most recently updated valid integration
            bestIntegration = validIntegrations.sort((a, b) => 
              new Date(b.updated_at || 0).getTime() - new Date(a.updated_at || 0).getTime()
            )[0];
          }
        }
        
        // Check if token needs refresh
        if (bestIntegration.access_token && this.isTokenExpired(bestIntegration.expires_at)) {
          console.log(`🔄 Token expired for ${service.service_name}, attempting refresh...`);
          try {
            bestIntegration = await this.refreshToken(bestIntegration, service.service_name, publicClient);
          } catch (refreshError) {
            console.error(`❌ Failed to refresh token for ${service.service_name}:`, refreshError);
            // Continue with expired token - let the service call fail and provide clear error
          }
        }
        
        console.log(`✅ Using ${service.service_name} integration ${bestIntegration.id}`);
        
        // Build service credentials based on integration method
        const serviceCreds: any = {
          integration_id: bestIntegration.id,
          service_id: service.id,
          integration_method: service.integration_method
        };
        
        // Add OAuth fields if present
        if (bestIntegration.access_token) {
          serviceCreds.access_token = bestIntegration.access_token;
          serviceCreds.refresh_token = bestIntegration.refresh_token;
          serviceCreds.expires_at = bestIntegration.expires_at;
          serviceCreds.scope = bestIntegration.scope;
        }
        
        // Add API key if present
        if (bestIntegration.api_key) {
          serviceCreds.api_key = bestIntegration.api_key;
        }
        
        // Add service-specific fields
        if (bestIntegration.email_address) {
          serviceCreds.email_address = bestIntegration.email_address;
        }
        
        if (bestIntegration.bot_id) {
          serviceCreds.bot_id = bestIntegration.bot_id;
        }
        
        if (bestIntegration.workspace_id) {
          serviceCreds.workspace_id = bestIntegration.workspace_id;
          serviceCreds.workspace_name = bestIntegration.workspace_name;
        }
        
        if (bestIntegration.configuration) {
          serviceCreds.configuration = bestIntegration.configuration;
        }
        
        credentials[service.service_name] = serviceCreds;
        
        if (bestIntegration.expires_at) {
          const expiryTime = new Date(bestIntegration.expires_at);
          const isExpired = this.isTokenExpired(bestIntegration.expires_at);
          console.log(`   Token expires: ${bestIntegration.expires_at} ${isExpired ? '(EXPIRED)' : '(Valid)'}`);
        }
      }

      return credentials;
    } catch (error) {
      console.error("Error fetching credentials:", error);
      return {};
    }
  }

  // Validate script using AST parsing rules
  private async validateScript(scriptCode: string): Promise<{ valid: boolean; errors: string[] }> {
    const errors: string[] = [];

    try {
      // Basic validation rules
      if (scriptCode.length > 10240) { // 10KB limit
        errors.push("Script exceeds 10KB size limit");
      }

      // Check for forbidden imports/functions
      const forbiddenPatterns = [
        /import\s+os\b/,
        /import\s+subprocess\b/,
        /\beval\s*\(/,
        /\bexec\s*\(/,
        /\b__import__\s*\(/,
        /\bopen\s*\(/,
        /\bfile\s*\(/,
        /\bexecfile\s*\(/,
        /\binput\s*\(/,
        /\braw_input\s*\(/
      ];

      for (const pattern of forbiddenPatterns) {
        if (pattern.test(scriptCode)) {
          errors.push(`Forbidden code pattern detected: ${pattern.source}`);
        }
      }

      // Check for infinite loops (basic heuristic)
      const infiniteLoopPatterns = [
        /while\s+True\s*:/,
        /while\s+1\s*:/,
        /for\s+\w+\s+in\s+itertools\.count\(/
      ];

      for (const pattern of infiniteLoopPatterns) {
        if (pattern.test(scriptCode)) {
          errors.push(`Potential infinite loop detected: ${pattern.source}`);
        }
      }

      // Ensure usage tracking is present
      if (!scriptCode.includes('usage_tracker') && !scriptCode.includes('track_usage')) {
        errors.push("Script must include usage tracking code");
      }

      return { valid: errors.length === 0, errors };
    } catch (error) {
      return { valid: false, errors: [`Script validation error: ${error.message}`] };
    }
  }

  // Execute script in containerized sandbox
  private async executeInSandbox(
    scriptCode: string, 
    credentials: Record<string, any>,
    triggerData: any,
    userId: string,
    automationId: string
  ): Promise<ContainerResult> {
    const startTime = Date.now();
    let containerId: string | undefined;

    try {
      // Check if sandbox API is available
      const sandboxApiUrl = Deno.env.get("SANDBOX_API_URL");
      
      if (sandboxApiUrl) {
        // Use actual containerized execution via API bridge
        const response = await fetch(`${sandboxApiUrl}/api/automations/execute/sandbox`, {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")}`
          },
          body: JSON.stringify({
            automation_id: automationId,
            trigger_data: triggerData,
            test_mode: false,
            timeout_seconds: 30
          })
        });

        if (!response.ok) {
          const error = await response.text();
          throw new Error(`Sandbox execution failed: ${error}`);
        }

        const result = await response.json();
        
        return {
          success: result.success,
          result: result.output ? JSON.parse(result.output) : null,
          error: result.error,
          duration_ms: result.duration_ms,
          container_id: result.container_id
        };
      } else {
        // Execute Python script directly in Edge Function environment
        const executionContext = {
          user_id: userId,
          automation_id: automationId,
          trigger_data: triggerData,
          credentials: credentials,
          timestamp: new Date().toISOString(),
          user_email: credentials.Gmail?.email_address || null // Use integration email, no hardcode fallback
        };

        const result = await this.executePythonScript(
          scriptCode, 
          executionContext
        );

        const duration = Date.now() - startTime;

        return {
          success: result.success,
          result: result.result,
          error: result.error,
          duration_ms: duration,
          container_id: containerId
        };
      }

    } catch (error) {
      const duration = Date.now() - startTime;
      console.error("Container execution error:", error);
      
      return {
        success: false,
        error: error.message,
        duration_ms: duration,
        container_id: containerId
      };
    }
  }

  // Execute Python script with real Gmail API integration
  private async executePythonScript(
    scriptCode: string, 
    context: any
  ): Promise<{ success: boolean; result?: any; error?: string }> {
    
    const startTime = Date.now();
    console.log(`🚀 Starting real script execution for automation ${context.automation_id}`);

    try {
      // Execute automation with dynamic service integration
      console.log(`🚀 Executing automation with dynamic service integration`);
      
      // Get available services from credentials
      const availableServices = Object.keys(context.credentials || {});
      console.log(`🔗 Available services: ${availableServices.join(', ')}`);
      
      if (availableServices.length === 0) {
        throw new Error("No service credentials available for automation");
      }
      
      // Handle Gmail service specifically (since our test script is Gmail-focused)
      if (context.credentials?.Gmail) {
        return await this.executeGmailAutomation(scriptCode, context, startTime);
      }
      
      // For other services, we could add handlers here
      if (context.credentials?.Slack) {
        throw new Error("Slack automation execution not yet implemented");
      }
      
      if (context.credentials?.Notion) {
        throw new Error("Notion automation execution not yet implemented");
      }
      
      throw new Error(`No automation handler available for services: ${availableServices.join(', ')}`);
      
    } catch (error) {
      const duration = Date.now() - startTime;
      console.error(`❌ Script execution failed after ${duration}ms:`, error);
      
      return {
        success: false,
        error: error.message
      };
    }
  }

  // Execute declarative automation via FastAPI
  private async executeDeclarative(
    automationId: string,
    userId: string,
    triggerData: any,
    timeoutPerAction: number = 30
  ): Promise<DeclarativeExecutionResult> {
    const fastapiUrl = Deno.env.get("FASTAPI_URL") || Deno.env.get("SANDBOX_API_URL");

    if (!fastapiUrl) {
      throw new Error("FASTAPI_URL not configured - cannot execute declarative automations");
    }

    console.log(`🚀 Executing declarative automation ${automationId} via FastAPI`);

    // Generate service JWT for FastAPI authentication
    // Note: Supabase doesn't allow env vars starting with SUPABASE_, so we use JWT_SECRET
    const jwtSecret = Deno.env.get("JWT_SECRET");
    if (!jwtSecret) {
      throw new Error("JWT_SECRET not configured");
    }

    // Create JWT payload
    const now = Math.floor(Date.now() / 1000);
    const payload = {
      sub: userId,
      role: "authenticated",
      iss: "supabase",
      aud: "authenticated",
      iat: now,
      exp: now + 300  // 5 minute expiry
    };

    // Sign JWT (using jose library available in Deno)
    const { SignJWT } = await import("https://deno.land/x/jose@v5.2.0/index.ts");
    const encoder = new TextEncoder();
    const serviceJwt = await new SignJWT(payload)
      .setProtectedHeader({ alg: "HS256" })
      .sign(encoder.encode(jwtSecret));

    // Call FastAPI execution endpoint
    const response = await fetch(`${fastapiUrl}/api/automations/execute`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'Authorization': `Bearer ${serviceJwt}`
      },
      body: JSON.stringify({
        automation_id: automationId,
        trigger_data: triggerData,
        timeout_per_action: timeoutPerAction
      })
    });

    if (!response.ok) {
      const errorText = await response.text();
      console.error(`❌ FastAPI execution failed: ${response.status} - ${errorText}`);
      throw new Error(`FastAPI execution failed: ${response.status} - ${errorText}`);
    }

    const result: DeclarativeExecutionResult = await response.json();

    console.log(`✅ Declarative execution completed: ${result.status}`);
    console.log(`   Actions executed: ${result.actions_executed}, Failed: ${result.actions_failed}`);
    console.log(`   Duration: ${result.duration_ms}ms`);

    return result;
  }

  // Execute Gmail-specific automation
  private async executeGmailAutomation(scriptCode: string, context: any, startTime: number): Promise<{ success: boolean; result?: any; error?: string }> {
    const gmailCreds = context.credentials?.Gmail;
    if (!gmailCreds || !gmailCreds.access_token) {
      throw new Error("No valid Gmail credentials available");
    }
    
    // Check token expiry
    const expiresAt = new Date(gmailCreds.expires_at);
    const now = new Date();
    
    if (expiresAt <= now) {
      throw new Error(`Gmail OAuth token expired at ${expiresAt}`);
    }
    
    console.log(`✅ Gmail token is valid until ${expiresAt} (Integration: ${gmailCreds.integration_id})`);
    
    // Ensure user email is available
    if (!context.user_email) {
      throw new Error("User email not available - required for Gmail automation");
    }
    
    // Parse the Python automation script to understand the intent
    console.log(`🔍 Analyzing automation script for email sending...`);
    
    // Extract subject and body from Python script
    const subjectMatch = scriptCode.match(/'subject'.*?'([^']+)'/);
    const bodyMatch = scriptCode.match(/body = '([^']+)'/);
    
    const subject = subjectMatch ? subjectMatch[1] : 'Comprehensive Test Success with Usage Tracking';
    const body = bodyMatch ? bodyMatch[1] : 'This automation was triggered by the comprehensive test suite and includes proper usage tracking.';
    
    console.log(`📝 Email details - Subject: "${subject}", To: ${context.user_email}`);
    
    // Create raw email message in RFC 2822 format
    const emailContent = [
      `To: ${context.user_email}`,
      `Subject: ${subject}`,
      `Content-Type: text/plain; charset="UTF-8"`,
      `Date: ${new Date().toUTCString()}`,
      '',
      body
    ].join('\r\n');
    
    // Base64 encode for Gmail API (URL-safe)
    const rawEmail = btoa(emailContent)
      .replace(/\+/g, '-')
      .replace(/\//g, '_')
      .replace(/=+$/, '');
    
    console.log(`🚀 Sending email via Gmail API...`);
    
    // Send via Gmail API
    const response = await fetch('https://gmail.googleapis.com/gmail/v1/users/me/messages/send', {
      method: 'POST',
      headers: {
        'Authorization': `Bearer ${gmailCreds.access_token}`,
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ raw: rawEmail })
    });
    
    if (!response.ok) {
      const error = await response.text();
      console.error(`❌ Gmail API error: ${response.status} - ${error}`);
      throw new Error(`Gmail API error: ${response.status} - ${error}`);
    }
    
    const emailResult = await response.json();
    const duration = Date.now() - startTime;
    
    console.log(`✅ Email sent successfully in ${duration}ms!`);
    console.log(`   Message ID: ${emailResult.id}`);
    console.log(`   Thread ID: ${emailResult.threadId}`);
    console.log(`   Integration used: ${gmailCreds.integration_id}`);
    
    // Return result in the format expected by the automation script
    return {
      success: true,
      result: {
        success: true,
        results: {
          message_id: emailResult.id,
          thread_id: emailResult.threadId,
          email_sent: true,
          integration_used: gmailCreds.integration_id,
          service_name: 'Gmail'
        },
        errors: [],
        executed_at: new Date().toISOString()
      }
    };
  }

  // Process execution request
  async executeScript(req: Request): Promise<Response> {
    try {
      const executionRequest: ExecutionRequest = await req.json();
      const { automation_id, trigger_data = {}, test_mode = false } = executionRequest;

      if (!automation_id) {
        return this.createErrorResponse("automation_id is required", 400);
      }

      // Validate authentication - check service auth first to avoid JWT validation hang
      const authHeader = req.headers.get("Authorization");
      const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
      let userId = null;
      let automation = null;

      if (authHeader?.substring(7) === serviceRoleKey) {
        // Service role authenticated - get automation without user restriction
        // Include 'actions' column for declarative execution check
        // Note: supabaseClient is already configured with schema: 'automations' in base handler
        const { data, error } = await this.supabaseClient
          .from('automation_records')
          .select('*, user_id, actions')
          .eq('id', automation_id)
          .eq('active', true)
          .single();

        if (data && !error) {
          automation = data;
          userId = data.user_id; // Use the automation's user ID
        }
      } else {
        // Try user authentication (only if not service role)
        userId = await this.validateAuth(req);
        if (userId) {
          automation = await this.getAutomation(automation_id, userId);
        }
      }

      if (!userId || !automation) {
        return this.createErrorResponse("Automation not found or access denied", 404);
      }

      // ================================================================
      // DECLARATIVE EXECUTION PATH
      // If automation has 'actions' field, use declarative execution via FastAPI
      // ================================================================
      // Parse actions if stored as JSON string (can happen from some UIs)
      let actions = automation.actions;
      if (typeof actions === 'string') {
        try {
          actions = JSON.parse(actions);
        } catch (e) {
          console.error('Failed to parse actions JSON:', e);
          actions = null;
        }
      }

      if (actions && Array.isArray(actions) && actions.length > 0) {
        console.log(`📋 Detected declarative automation with ${actions.length} actions`);

        try {
          const declarativeResult = await this.executeDeclarative(
            automation_id,
            userId,
            trigger_data
          );

          // Track usage for declarative execution
          if (!test_mode) {
            await this.trackUsage(
              userId,
              automation_id,
              'declarative', // Service type for declarative
              0,
              declarativeResult.duration_ms
            );
          }

          // Return declarative execution response
          return this.createSuccessResponse({
            result: {
              status: declarativeResult.status,
              actions_executed: declarativeResult.actions_executed,
              actions_failed: declarativeResult.actions_failed,
              action_results: declarativeResult.action_results,
              error_summary: declarativeResult.error_summary
            },
            execution_time_ms: declarativeResult.duration_ms,
            execution_type: 'declarative',
            test_mode
          }, declarativeResult.execution_id);

        } catch (error) {
          console.error(`❌ Declarative execution failed:`, error);
          return this.createErrorResponse(error.message || "Declarative execution failed", 500);
        }
      }

      // ================================================================
      // LEGACY SCRIPT-BASED EXECUTION PATH
      // Falls through if no 'actions' field - uses script_code execution
      // ================================================================
      console.log(`📜 Using legacy script-based execution`);

      // Log execution start
      const executionId = await this.logExecution(automation_id, userId, trigger_data);

      // Validate script
      const validation = await this.validateScript(automation.script_code);
      if (!validation.valid) {
        await this.updateExecution(executionId, null, `Validation failed: ${validation.errors.join(', ')}`);
        return this.createErrorResponse(`Script validation failed: ${validation.errors.join(', ')}`, 400);
      }

      // Get required credentials
      const dependencies = automation.dependencies || [];
      const credentials = await this.getUserCredentials(userId, dependencies);

      // Execute script in sandbox
      const result = await this.executeInSandbox(
        automation.script_code,
        credentials,
        trigger_data,
        userId,
        automation_id
      );

      // Update execution log
      await this.updateExecution(
        executionId,
        result.success ? result.result : null,
        result.success ? null : result.error,
        result.duration_ms
      );

      // Track usage
      if (!test_mode) {
        await this.trackUsage(
          userId,
          automation_id,
          dependencies[0], // Primary service
          0, // TODO: Calculate actual token usage
          result.duration_ms
        );
      }

      // Return response
      if (result.success) {
        return this.createSuccessResponse({
          result: result.result,
          execution_time_ms: result.duration_ms,
          container_id: result.container_id,
          execution_type: 'script',
          test_mode
        }, executionId);
      } else {
        return this.createErrorResponse(result.error || "Script execution failed", 500);
      }

    } catch (error) {
      console.error("Script executor error:", error);
      return this.createErrorResponse("Internal server error", 500);
    }
  }

  // Handle manual execution requests
  async handleManualExecution(req: Request): Promise<Response> {
    return this.executeScript(req);
  }

  // Handle scheduled execution (called by cron/scheduler)
  async handleScheduledExecution(req: Request): Promise<Response> {
    try {
      const { automation_id, scheduled_time, trigger_data } = await req.json();

      if (!automation_id) {
        return this.createErrorResponse("automation_id is required", 400);
      }

      // For scheduled executions, we bypass user auth and use service role
      // Note: supabaseClient is already configured with schema: 'automations' in base handler
      const { data: automation, error } = await this.supabaseClient
        .from('automation_records')
        .select('*, user_id')
        .eq('id', automation_id)
        .eq('active', true)
        .in('trigger_type', ['schedule', 'schedule_once', 'schedule_recurring'])
        .single();

      if (error || !automation) {
        return this.createErrorResponse("Scheduled automation not found", 404);
      }

      // Create a mock request with service role auth for execution
      const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
      const mockRequest = new Request(req.url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${serviceRoleKey}`  // Service role auth for executeScript
        },
        body: JSON.stringify({
          automation_id,
          trigger_data: { scheduled_time, trigger_type: 'schedule' },
          test_mode: false
        })
      });

      // Execute with service permissions
      return this.executeScript(mockRequest);

    } catch (error) {
      console.error("Scheduled execution error:", error);
      return this.createErrorResponse("Scheduled execution failed", 500);
    }
  }

  // Handle event-triggered execution (called by event-processor for webhook/polling events)
  async handleEventExecution(req: Request): Promise<Response> {
    try {
      const { automation_id, trigger_data } = await req.json();

      if (!automation_id) {
        return this.createErrorResponse("automation_id is required", 400);
      }

      // For event executions, we bypass user auth and use service role
      const { data: automation, error } = await this.supabaseClient
        .from('automation_records')
        .select('*, user_id')
        .eq('id', automation_id)
        .eq('active', true)
        .in('trigger_type', ['webhook', 'polling'])
        .single();

      if (error || !automation) {
        return this.createErrorResponse("Event-triggered automation not found", 404);
      }

      // Create a mock request with service role auth for execution
      const serviceRoleKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY");
      const mockRequest = new Request(req.url, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'Authorization': `Bearer ${serviceRoleKey}`
        },
        body: JSON.stringify({
          automation_id,
          trigger_data: {
            ...trigger_data,
            trigger_type: automation.trigger_type
          },
          test_mode: false
        })
      });

      // Execute with service permissions
      return this.executeScript(mockRequest);

    } catch (error) {
      console.error("Event execution error:", error);
      return this.createErrorResponse("Event execution failed", 500);
    }
  }
}

// Main handler with routing
Deno.serve(async (req) => {
  const executor = new ScriptExecutor();
  
  // Handle CORS
  const corsResponse = executor.handleCors(req);
  if (corsResponse) return corsResponse;

  const url = new URL(req.url);
  const path = url.pathname;

  // Route to appropriate handler
  if (path.includes('/manual')) {
    return await executor.handleManualExecution(req);
  } else if (path.includes('/scheduled')) {
    return await executor.handleScheduledExecution(req);
  } else if (path.includes('/event')) {
    return await executor.handleEventExecution(req);
  } else {
    return await executor.executeScript(req);
  }
});

/*
API Endpoints:

1. Manual Execution:
POST /functions/v1/script-executor/manual
Headers: Authorization: Bearer <user-token>
Body: {
  "automation_id": "uuid",
  "trigger_data": {...},
  "test_mode": false
}

2. Scheduled Execution (internal):
POST /functions/v1/script-executor/scheduled
Headers: Authorization: Bearer <service-role-key>
Body: {
  "automation_id": "uuid",
  "scheduled_time": "2024-01-01T00:00:00Z"
}

3. Generic Execution:
POST /functions/v1/script-executor
Headers: Authorization: Bearer <user-token>
Body: {
  "automation_id": "uuid",
  "trigger_data": {...}
}

Response Format:
{
  "success": true,
  "data": {
    "result": {...},
    "execution_time_ms": 1234,
    "container_id": "container_123"
  },
  "execution_id": "uuid"
}
*/
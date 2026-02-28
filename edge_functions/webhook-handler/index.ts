// Setup type definitions for built-in Supabase Runtime APIs
import "jsr:@supabase/functions-js/edge-runtime.d.ts";
import { createClient } from "jsr:@supabase/supabase-js@2";
import { BaseAutomationHandler } from "../_shared/base-handler.ts";
import {
  WebhookEvent,
  getServiceParser,
  SignatureVerifier
} from "../_shared/service-parsers.ts";
import { evaluateFilter } from "../_shared/filter-utils.ts";

class WebhookHandler extends BaseAutomationHandler {
  private signatureVerifier: SignatureVerifier;
  private publicClient: any;  // For querying public schema (integrations table)
  private fastApiUrl: string;

  constructor() {
    super();
    this.signatureVerifier = new SignatureVerifier();
    // Create separate client for public schema queries
    this.publicClient = createClient(
      Deno.env.get("SUPABASE_URL") ?? "",
      Deno.env.get("SUPABASE_SERVICE_ROLE_KEY") ?? ""
    );
    this.fastApiUrl = Deno.env.get("FASTAPI_URL") ?? "";
  }

  // Check Gmail history for actual new messages (filters out false positive notifications)
  private async checkGmailHistory(
    userId: string,
    historyId: string
  ): Promise<{ hasNewMessages: boolean; messageIds: string[]; latestHistoryId?: string; error?: string }> {
    try {
      if (!this.fastApiUrl) {
        console.warn("FASTAPI_URL not configured, skipping Gmail history check");
        return { hasNewMessages: true, messageIds: [] }; // Pass through if not configured
      }

      // Generate service account JWT for FastAPI auth
      const jwt = await this.generateServiceJwt(userId);

      const response = await fetch(`${this.fastApiUrl}/api/automations/gmail/check-history`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "Authorization": `Bearer ${jwt}`
        },
        body: JSON.stringify({
          user_id: userId,
          history_id: historyId
        })
      });

      if (!response.ok) {
        const errorText = await response.text();
        console.error(`Gmail history check failed: ${response.status} ${errorText}`);
        return { hasNewMessages: true, messageIds: [], error: errorText }; // Pass through on error
      }

      const result = await response.json();

      if (result.error) {
        console.warn(`Gmail history check error: ${result.error}`);
        // If history expired, we can't know - pass through
        return { hasNewMessages: true, messageIds: [], error: result.error };
      }

      console.log(`Gmail history check: ${result.new_message_ids?.length || 0} new messages, latest_history_id=${result.latest_history_id}`);
      return {
        hasNewMessages: result.has_new_messages,
        messageIds: result.new_message_ids || [],
        latestHistoryId: result.latest_history_id
      };

    } catch (error) {
      console.error("Gmail history check exception:", error);
      return { hasNewMessages: true, messageIds: [], error: String(error) }; // Pass through on error
    }
  }

  // Update the stored Gmail history ID after successful processing
  private async updateGmailHistoryId(userId: string, historyId: string): Promise<void> {
    // Gmail service_id from services table
    const GMAIL_SERVICE_ID = "a6fd4618-0cdc-4506-a371-df48e6413ea3";

    try {
      // Update the integrations table with the latest history ID
      const { error } = await this.publicClient
        .from('integrations')
        .update({ last_gmail_history_id: historyId })
        .eq('user_id', userId)
        .eq('service_id', GMAIL_SERVICE_ID)
        .eq('is_active', true);

      if (error) {
        console.warn(`Failed to update Gmail history ID: ${error.message}`);
      } else {
        console.log(`Updated last_gmail_history_id to ${historyId} for user ${userId}`);
      }
    } catch (error) {
      console.error(`Error updating Gmail history ID: ${error}`);
    }
  }

  // Generate a service JWT for authenticating to FastAPI
  private async generateServiceJwt(userId: string): Promise<string> {
    const jwtSecret = Deno.env.get("JWT_SECRET") ?? "";
    const now = Math.floor(Date.now() / 1000);

    // Create JWT payload matching Supabase format
    const payload = {
      aud: "authenticated",
      exp: now + 3600, // 1 hour expiry
      iat: now,
      iss: Deno.env.get("SUPABASE_URL") + "/auth/v1",
      sub: userId,
      role: "authenticated"
    };

    // Simple JWT encoding (Supabase uses HS256)
    const encoder = new TextEncoder();
    const header = { alg: "HS256", typ: "JWT" };

    const encodedHeader = btoa(JSON.stringify(header)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');
    const encodedPayload = btoa(JSON.stringify(payload)).replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');

    const data = encoder.encode(`${encodedHeader}.${encodedPayload}`);
    const key = await crypto.subtle.importKey(
      "raw",
      encoder.encode(jwtSecret),
      { name: "HMAC", hash: "SHA-256" },
      false,
      ["sign"]
    );

    const signature = await crypto.subtle.sign("HMAC", key, data);
    const encodedSignature = btoa(String.fromCharCode(...new Uint8Array(signature)))
      .replace(/=/g, '').replace(/\+/g, '-').replace(/\//g, '_');

    return `${encodedHeader}.${encodedPayload}.${encodedSignature}`;
  }
  
  // Verify webhook signature based on service
  private async verifySignature(
    service: string, 
    payload: string, 
    signature: string, 
    timestamp?: string
  ): Promise<boolean> {
    try {
      switch (service.toLowerCase()) {
        case 'slack':
          return await this.signatureVerifier.verifySlackSignature(payload, signature, timestamp || '');
        case 'gmail':
          return await this.signatureVerifier.verifyGoogleSignature(payload, signature);
        case 'outlook':
        case 'teams':
        case 'gcalendar':
        case 'microsoft':
          return await this.signatureVerifier.verifyMicrosoftSignature(payload, signature);
        case 'notion':
          return await this.signatureVerifier.verifyNotionSignature(payload, signature);
        case 'todoist':
          return await this.signatureVerifier.verifyTodoistSignature(payload, signature);
        case 'fitbit':
          return await this.signatureVerifier.verifyFitbitSignature(payload, signature);
        default:
          console.warn(`No signature verification implemented for service: ${service}`);
          return true; // Allow through for development
      }
    } catch (error) {
      console.error(`Signature verification failed for ${service}:`, error);
      return false;
    }
  }

  // Parse service-specific webhook format
  private parseWebhookData(service: string, rawPayload: any, userId?: string): WebhookEvent | null {
    const parser = getServiceParser(service);
    if (!parser) {
      console.error(`No parser available for service: ${service}`);
      return null;
    }

    if (!parser.validatePayload(rawPayload)) {
      console.error(`Invalid payload for service: ${service}`);
      return null;
    }

    return parser.parseWebhook(rawPayload, userId);
  }

  // Look up Juniper user_id from external workspace/team ID
  // The integrations table stores workspace_id for each service connection (public schema)
  // Note: Multiple users can share the same workspace (e.g., team members in same Slack workspace)
  // We pick the oldest integration to be consistent
  private async lookupUserByWorkspaceId(workspaceId: string): Promise<string | null> {
    try {
      // Use publicClient since integrations is in public schema
      // Use limit(1) instead of single() since multiple users can share a workspace
      const { data, error } = await this.publicClient
        .from('integrations')
        .select('user_id')
        .eq('workspace_id', workspaceId)
        .eq('is_active', true)
        .order('created_at', { ascending: true })
        .limit(1);

      if (error || !data || data.length === 0) {
        console.log(`No user found for workspace_id: ${workspaceId}`);
        return null;
      }

      return data[0].user_id;
    } catch (error) {
      console.error(`Failed to lookup user for workspace ${workspaceId}:`, error);
      return null;
    }
  }

  // Look up Juniper user_id from email address (used for Gmail)
  private async lookupUserByEmail(email: string): Promise<string | null> {
    try {
      const { data, error } = await this.publicClient
        .from('integrations')
        .select('user_id')
        .eq('email_address', email)
        .eq('is_active', true)
        .order('created_at', { ascending: true })
        .limit(1);

      if (error || !data || data.length === 0) {
        console.log(`No user found for email: ${email}`);
        return null;
      }

      return data[0].user_id;
    } catch (error) {
      console.error(`Failed to lookup user for email ${email}:`, error);
      return null;
    }
  }

  // Check and update webhook subscription status
  private async updateWebhookSubscription(
    userId: string, 
    service: string, 
    subscriptionData: any
  ): Promise<void> {
    try {
      // Update subscription expiration for Microsoft Graph services
      if (subscriptionData.subscription_expiration_datetime) {
        await this.supabaseClient
          .from('service_webhook_configs')
          .upsert({
            user_id: userId,
            service_name: service,
            subscription_id: subscriptionData.subscription_id,
            subscription_expires_at: subscriptionData.subscription_expiration_datetime,
            webhook_url: `${Deno.env.get("SUPABASE_URL")}/functions/v1/webhook-handler/${service}/${userId}`,
            active: true
          }, {
            onConflict: 'user_id,service_name'
          });
      }
    } catch (error) {
      console.error(`Failed to update webhook subscription for ${service}:`, error);
    }
  }

  // Check if event passes any automation's trigger filter
  // Returns true if event should be queued (passes at least one filter or no filters exist)
  private async checkAutomationFilters(event: WebhookEvent): Promise<{ shouldQueue: boolean; matchedAutomations: number; filteredOut: number }> {
    try {
      // Find webhook automations for this user/service/event_type
      const { data: automations, error } = await this.supabaseClient
        .from('automation_records')
        .select('id, name, trigger_config')
        .eq('user_id', event.user_id)
        .eq('trigger_type', 'webhook')
        .eq('active', true);

      if (error || !automations || automations.length === 0) {
        // No automations found - queue anyway so events aren't lost
        return { shouldQueue: true, matchedAutomations: 0, filteredOut: 0 };
      }

      // Filter to automations matching this service/event_type
      const matchingAutomations = automations.filter(a => {
        const tc = a.trigger_config || {};
        const serviceMatch = !tc.service || tc.service.toLowerCase() === event.service.toLowerCase();
        const eventMatch = !tc.event_type ||
          tc.event_type.toLowerCase() === event.event_type.toLowerCase() ||
          (tc.event_types && tc.event_types.some((et: string) => et.toLowerCase() === event.event_type.toLowerCase()));
        return serviceMatch && eventMatch;
      });

      if (matchingAutomations.length === 0) {
        // No matching automations - queue anyway so events aren't lost
        return { shouldQueue: true, matchedAutomations: 0, filteredOut: 0 };
      }

      // Check filters for matching automations
      let passedFilter = 0;
      let filteredOut = 0;

      for (const automation of matchingAutomations) {
        const filter = automation.trigger_config?.filter || automation.trigger_config?.filters;

        if (!filter) {
          // No filter = passes
          passedFilter++;
        } else if (evaluateFilter({ trigger_data: event.data }, filter)) {
          // Wrap in trigger_data to match filter path format (trigger_data.event.text, etc.)
          passedFilter++;
        } else {
          filteredOut++;
          console.log(`Webhook filtered out by automation "${automation.name}" filter`);
        }
      }

      // Queue if at least one automation's filter passes (or has no filter)
      return {
        shouldQueue: passedFilter > 0,
        matchedAutomations: matchingAutomations.length,
        filteredOut
      };

    } catch (error) {
      console.error('Error checking automation filters:', error);
      // On error, queue anyway to avoid losing events
      return { shouldQueue: true, matchedAutomations: 0, filteredOut: 0 };
    }
  }

  // Queue event for processing
  private async queueEvent(event: WebhookEvent): Promise<boolean> {
    try {
      // Check for duplicate events using composite key
      const { data: existing } = await this.supabaseClient
        .from('automation_events')
        .select('id')
        .eq('service_name', event.service)
        .eq('event_id', event.event_id)
        .eq('user_id', event.user_id)
        .single();

      if (existing) {
        console.log(`Duplicate event detected: ${event.service}:${event.event_id}`);
        return true; // Return success for duplicate events
      }

      // Insert new event
      const { error } = await this.supabaseClient
        .from('automation_events')
        .insert({
          user_id: event.user_id,
          service_name: event.service,
          event_type: event.event_type,
          event_id: event.event_id,
          event_data: event.data,
          processed: false,
          retry_count: 0
        });

      if (error) {
        console.error("Failed to queue event:", error);
        return false;
      }

      console.log(`Queued event: ${event.service}:${event.event_type} for user ${event.user_id}`);
      return true;
    } catch (error) {
      console.error("Error queueing event:", error);
      return false;
    }
  }

  async handleWebhook(req: Request): Promise<Response> {
    try {
      // Extract service from URL path
      const url = new URL(req.url);
      const pathParts = url.pathname.split('/');
      const service = pathParts[pathParts.length - 2]; // /webhooks/{service}/{user_id}
      const userId = pathParts[pathParts.length - 1];

      if (!service) {
        return this.createErrorResponse("Service not specified in webhook URL", 400);
      }

      // Handle Fitbit GET verification request
      // Fitbit sends GET ?verify=<code> and expects 204 for correct code, 404 for incorrect
      if (req.method === 'GET' && service === 'fitbit') {
        const verifyCode = url.searchParams.get('verify');
        if (verifyCode) {
          const expectedCode = Deno.env.get('FITBIT_VERIFY_CODE');
          console.log(`Fitbit verification request. Code: ${verifyCode}, Expected: ${expectedCode || 'NOT SET'}`);

          if (expectedCode && verifyCode === expectedCode) {
            console.log('Fitbit verification SUCCESS - returning 204');
            return new Response(null, { status: 204 });
          } else {
            console.log('Fitbit verification FAILED - returning 404');
            return new Response(null, { status: 404 });
          }
        }
      }

      // Handle Microsoft Graph validation request
      // When creating/renewing subscriptions, Microsoft sends POST with ?validationToken=<token>
      // Must echo back the token as plain text within 10 seconds
      if ((service === 'outlook' || service === 'teams' || service === 'microsoft') &&
          url.searchParams.has('validationToken')) {
        const validationToken = url.searchParams.get('validationToken');
        console.log(`Microsoft Graph validation request for ${service}. Token: ${validationToken?.substring(0, 20)}...`);
        return new Response(validationToken, {
          status: 200,
          headers: { 'Content-Type': 'text/plain' }
        });
      }

      const rawPayload = await req.text();
      let payload;
      
      try {
        payload = JSON.parse(rawPayload);
      } catch {
        payload = rawPayload; // Handle non-JSON payloads
      }

      // Handle Slack URL verification challenge
      if (service === 'slack' && payload.type === 'url_verification') {
        return new Response(payload.challenge, {
          status: 200,
          headers: { 'Content-Type': 'text/plain' }
        });
      }

      // Handle Notion webhook verification
      // Notion sends a POST with verification_token when setting up webhook
      if (service === 'notion' && payload.verification_token) {
        console.log('===========================================');
        console.log('NOTION VERIFICATION TOKEN RECEIVED:');
        console.log(payload.verification_token);
        console.log('===========================================');
        console.log('Copy this token and paste it into Notion webhook verification UI');

        // Return the token in the response so it can be seen in logs/response
        return this.createSuccessResponse({
          message: 'Notion verification token received',
          verification_token: payload.verification_token,
          instructions: 'Copy this token and paste it into the Notion webhook verification form'
        });
      }

      // Extract workspace_id from payload and look up Juniper user
      // This enables multi-tenant support with a single webhook URL per service
      let resolvedUserId = userId;
      let externalWorkspaceId: string | null = null;

      // Extract workspace/team ID based on service
      if (service === 'slack') {
        externalWorkspaceId = payload.team_id;
      } else if (service === 'notion') {
        externalWorkspaceId = payload.workspace?.id || payload.data?.workspace?.id;
      } else if (service === 'todoist') {
        // Todoist uses user_id - we'd need to store this during OAuth
        externalWorkspaceId = payload.user_id?.toString();
      } else if (service === 'fitbit') {
        externalWorkspaceId = Array.isArray(payload) ? payload[0]?.ownerId : payload.ownerId;
      } else if (service === 'outlook' || service === 'teams' || service === 'microsoft') {
        // Microsoft Graph uses clientState which we set to user_id during subscription creation
        const notifications = payload.value;
        if (Array.isArray(notifications) && notifications.length > 0) {
          const clientState = notifications[0].clientState;
          if (clientState) {
            // clientState IS the user_id, no lookup needed
            resolvedUserId = clientState;
            console.log(`Outlook clientState contains user_id: ${resolvedUserId}`);
          }
        }
      } else if (service === 'gmail') {
        // Gmail Pub/Sub sends emailAddress - look up user by email
        try {
          const pubsubMessage = payload.message;
          if (pubsubMessage?.data) {
            const decodedData = JSON.parse(atob(pubsubMessage.data));
            const emailAddress = decodedData.emailAddress;
            if (emailAddress) {
              const lookedUpUserId = await this.lookupUserByEmail(emailAddress);
              if (lookedUpUserId) {
                resolvedUserId = lookedUpUserId;
                console.log(`Gmail email ${emailAddress} → user ${resolvedUserId}`);
              } else {
                console.warn(`No user found for Gmail: ${emailAddress}`);
              }
            }
          }
        } catch (e) {
          console.error('Failed to extract Gmail email:', e);
        }
      }

      // Look up user if we have a workspace ID and no valid user_id from URL
      if (externalWorkspaceId && (!userId || userId === service || userId === 'webhook')) {
        const lookedUpUserId = await this.lookupUserByWorkspaceId(externalWorkspaceId);
        if (lookedUpUserId) {
          resolvedUserId = lookedUpUserId;
          console.log(`Resolved ${service} workspace ${externalWorkspaceId} → user ${resolvedUserId}`);
        } else {
          console.warn(`No user found for ${service} workspace: ${externalWorkspaceId}`);
        }
      }

      // Verify signature if provided
      const signature = req.headers.get('x-slack-signature') ||
                       req.headers.get('x-hub-signature-256') ||
                       req.headers.get('x-goog-signature') ||
                       req.headers.get('x-ms-signature') ||
                       req.headers.get('x-todoist-hmac-sha256') ||
                       req.headers.get('x-fitbit-signature');
      
      const timestamp = req.headers.get('x-slack-request-timestamp');
      
      if (signature) {
        const isValid = await this.verifySignature(service, rawPayload, signature, timestamp || undefined);
        if (!isValid) {
          console.error(`Invalid signature for ${service} webhook`);
          return this.createErrorResponse("Invalid signature", 401);
        }
      }

      // Parse webhook data
      const event = this.parseWebhookData(service, payload, resolvedUserId);
      if (!event) {
        return this.createErrorResponse("Failed to parse webhook data", 400);
      }

      // Use resolved user_id (from lookup or URL)
      if (resolvedUserId && resolvedUserId !== '{user_id}' && resolvedUserId !== service && resolvedUserId !== 'webhook') {
        event.user_id = resolvedUserId;
      }

      if (!event.user_id) {
        console.error(`No user found for ${service} webhook. Workspace: ${externalWorkspaceId}`);
        return this.createErrorResponse("User not found. Ensure the service is connected.", 400);
      }

      // Update webhook subscription info if applicable
      if (event.data.subscription_id) {
        await this.updateWebhookSubscription(event.user_id, service, event.data);
      }

      // For Gmail: Check if this notification represents actual new messages
      // Gmail sends push notifications for ALL mailbox changes, not just new messages
      console.log(`[Gmail Filter Debug] service=${service}, history_id=${event.data?.history_id}, fastApiUrl=${this.fastApiUrl?.substring(0, 30)}...`);
      if (service === 'gmail' && event.data.history_id) {
        console.log(`[Gmail Filter] Checking history_id ${event.data.history_id} for user ${event.user_id}`);
        const historyCheck = await this.checkGmailHistory(
          event.user_id,
          event.data.history_id.toString()
        );

        if (!historyCheck.hasNewMessages) {
          console.log(`Gmail notification filtered: no new inbox messages (history_id: ${event.data.history_id})`);

          // Still update the stored history_id even if no new messages (keeps us in sync)
          if (historyCheck.latestHistoryId) {
            await this.updateGmailHistoryId(event.user_id, historyCheck.latestHistoryId);
          }

          // Return success but don't queue - this is expected behavior
          return this.createSuccessResponse({
            message: "Gmail notification received - no new messages",
            filtered: true,
            history_id: event.data.history_id
          });
        }

        // If we have specific message IDs, create an event for each
        if (historyCheck.messageIds.length > 0) {
          console.log(`Gmail: ${historyCheck.messageIds.length} new message(s) detected`);

          // Queue an event for each new message (with message_id as event_id for dedup)
          let queuedCount = 0;
          for (const messageId of historyCheck.messageIds) {
            const messageEvent: WebhookEvent = {
              ...event,
              event_id: messageId, // Use message ID for deduplication
              data: {
                ...event.data,
                message_id: messageId,
                message_ids: historyCheck.messageIds // Include all for context
              }
            };

            const queued = await this.queueEvent(messageEvent);
            if (queued) queuedCount++;
          }

          console.log(`Gmail: Queued ${queuedCount} event(s) for new messages`);

          // Update stored history_id after successfully queueing events
          if (historyCheck.latestHistoryId) {
            await this.updateGmailHistoryId(event.user_id, historyCheck.latestHistoryId);
          }

          return this.createSuccessResponse({
            message: `Gmail: ${queuedCount} new message event(s) queued`,
            event_ids: historyCheck.messageIds,
            service: event.service,
            event_type: event.event_type
          });
        }
      }

      // Filter Outlook/Microsoft events - only process 'created' events for new messages
      // 'updated' events are triggered by read status, categorization, flagging, etc.
      if ((service === 'outlook' || service === 'microsoft') && event.data?.change_type === 'updated') {
        console.log(`Outlook notification filtered: ignoring 'updated' event (read status, flag, etc.)`);
        return this.createSuccessResponse({
          message: "Outlook notification received - update event filtered",
          filtered: true,
          change_type: event.data.change_type
        });
      }

      // Check automation filters before queueing
      const filterResult = await this.checkAutomationFilters(event);
      if (!filterResult.shouldQueue) {
        console.log(`Webhook filtered: all ${filterResult.matchedAutomations} automation(s) filtered out event`);
        return this.createSuccessResponse({
          message: "Webhook received - filtered by automation trigger filters",
          filtered: true,
          service: event.service,
          event_type: event.event_type,
          automations_checked: filterResult.matchedAutomations,
          filtered_out: filterResult.filteredOut
        });
      }

      // Queue event for processing (non-Gmail or Gmail without specific message IDs)
      const queued = await this.queueEvent(event);
      if (!queued) {
        return this.createErrorResponse("Failed to queue event", 500);
      }

      // Log successful webhook receipt
      console.log(`Webhook processed: ${service}:${event.event_type} for user ${event.user_id}`);
      if (filterResult.filteredOut > 0) {
        console.log(`  (${filterResult.filteredOut} automation filter(s) did not match)`);
      }

      // Return 200 immediately for webhook acknowledgment
      return this.createSuccessResponse({
        message: "Webhook received and queued",
        event_id: event.event_id,
        service: event.service,
        event_type: event.event_type
      });

    } catch (error) {
      console.error("Webhook handler error:", error);
      return this.createErrorResponse("Internal server error", 500);
    }
  }
}

// Main handler
Deno.serve(async (req) => {
  const handler = new WebhookHandler();
  
  // Handle CORS
  const corsResponse = handler.handleCors(req);
  if (corsResponse) return corsResponse;
  
  return await handler.handleWebhook(req);
});

/*
Multi-Tenant Webhook URLs (single URL per service - user resolved from payload):
- Slack: POST /functions/v1/webhook-handler/slack/webhook
- Notion: POST /functions/v1/webhook-handler/notion/webhook
- Todoist: POST /functions/v1/webhook-handler/todoist/webhook
- Fitbit: POST /functions/v1/webhook-handler/fitbit/webhook
- Gmail: POST /functions/v1/webhook-handler/gmail/webhook
- Teams: POST /functions/v1/webhook-handler/teams/webhook
- Outlook: POST /functions/v1/webhook-handler/outlook/webhook

User Resolution:
- Slack: team_id from payload → integrations.workspace_id → user_id
- Notion: workspace.id from payload → integrations.workspace_id → user_id
- Todoist: user_id from payload → integrations.workspace_id → user_id
- Fitbit: ownerId from payload → integrations.workspace_id → user_id

Environment Variables Required:
- SLACK_SIGNING_SECRET: For Slack signature verification
- TODOIST_CLIENT_SECRET: For Todoist HMAC-SHA256 verification
- FITBIT_CLIENT_SECRET: For Fitbit HMAC-SHA1 verification
- NOTION_WEBHOOK_SECRET: For Notion signature verification

Example - Slack webhook (team_id T091G354CNN resolves to Juniper user):
curl -X POST "https://[project].supabase.co/functions/v1/webhook-handler/slack/webhook" \
  -H "Content-Type: application/json" \
  -H "x-slack-signature: v0=..." \
  -d '{"type": "event_callback", "team_id": "T091G354CNN", "event": {...}}'
*/
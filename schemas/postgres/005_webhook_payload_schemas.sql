-- Migration: 005_webhook_payload_schemas.sql
-- Description: Add webhook payload schemas to service_capabilities
-- This tells the automation agent what fields are available in trigger_data for each webhook event

-- Add columns to service_capabilities
ALTER TABLE automations.service_capabilities
ADD COLUMN IF NOT EXISTS webhook_payload_schemas jsonb DEFAULT '{}'::jsonb,
ADD COLUMN IF NOT EXISTS notes TEXT;

COMMENT ON COLUMN automations.service_capabilities.webhook_payload_schemas IS
'JSON schemas for each webhook event type. Keys are event types, values describe the payload structure available in trigger_data. This is injected into the automation agent context so it knows how to reference webhook fields.';

COMMENT ON COLUMN automations.service_capabilities.notes IS
'Service-specific notes for the automation agent (e.g., sync delays, API quirks, recommended patterns).';

-- Example: Populate Slack webhook schemas
-- The agent uses this to know that Slack "message" events have event.text, event.user, etc.
/*
UPDATE automations.service_capabilities
SET webhook_events = ARRAY['message', 'app_mention', 'reaction_added'],
    webhook_payload_schemas = '{
    "message": {
        "description": "A message was posted to a channel",
        "trigger_data_fields": {
            "event.type": "message",
            "event.user": "User ID who sent the message",
            "event.channel": "Channel ID where message was posted",
            "event.text": "The message text content",
            "event.ts": "Message timestamp (unique ID)"
        },
        "example_condition": {"op": "contains", "path": "event.text", "value": "urgent", "case_insensitive": true}
    },
    "app_mention": {
        "description": "Your app was mentioned in a message",
        "trigger_data_fields": {
            "event.user": "User ID who mentioned the app",
            "event.channel": "Channel ID",
            "event.text": "Full message text including mention"
        }
    }
}'::jsonb
WHERE service_name = 'slack';
*/

-- Example: Gmail webhook (signal only - must fetch actual messages)
/*
UPDATE automations.service_capabilities
SET supports_webhooks = true,
    webhook_events = ARRAY['message_received'],
    webhook_payload_schemas = '{
    "message_received": {
        "description": "A new email notification (signal only - must fetch message details via API)",
        "trigger_data_fields": {
            "event_data.history_id": "Gmail history ID - use to fetch new messages",
            "event_data.email_address": "Email address that received the message"
        },
        "note": "Gmail webhooks are signals only. Use gmail_get_messages to fetch actual content."
    }
}'::jsonb,
    notes = 'Gmail webhooks notify of new messages but do not include message content. The automation must call gmail_get_messages or gmail_history_list to retrieve the actual email.'
WHERE service_name = 'gmail';
*/

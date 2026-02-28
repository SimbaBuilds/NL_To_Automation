-- Migration: 004_create_service_integration_tables.sql
-- Description: Create tables for service integration layer (Phase 3)
-- Date: 2025-08-16

-- Service webhook configurations
CREATE TABLE automations.service_webhook_configs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  service_name TEXT NOT NULL,
  webhook_url TEXT NOT NULL,
  webhook_secret TEXT,
  subscription_id TEXT, -- For services like Microsoft Graph
  subscription_expires_at TIMESTAMP WITH TIME ZONE,
  verification_token TEXT,
  active BOOLEAN DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(user_id, service_name)
);

-- Service polling state tracking
CREATE TABLE automations.service_polling_state (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  service_name TEXT NOT NULL,
  last_polled_at TIMESTAMP WITH TIME ZONE,
  last_cursor TEXT, -- For cursor-based pagination
  last_sync_token TEXT, -- For delta queries
  polling_interval_minutes INTEGER DEFAULT 15,
  next_poll_at TIMESTAMP WITH TIME ZONE,
  consecutive_empty_polls INTEGER DEFAULT 0,
  active BOOLEAN DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(user_id, service_name)
);

-- Service rate limiting tracking
CREATE TABLE automations.service_rate_limits (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  service_name TEXT NOT NULL,
  user_id UUID REFERENCES public.user_profiles(id) ON DELETE CASCADE, -- NULL for global limits
  rate_limit_type TEXT NOT NULL, -- 'per_user', 'per_minute', 'per_hour', 'per_day'
  limit_value INTEGER NOT NULL,
  current_usage INTEGER DEFAULT 0,
  reset_at TIMESTAMP WITH TIME ZONE NOT NULL,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(service_name, user_id, rate_limit_type)
);

-- Service API error tracking
CREATE TABLE automations.service_api_errors (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  service_name TEXT NOT NULL,
  error_type TEXT NOT NULL, -- 'rate_limit', 'auth_failure', 'service_down', 'timeout'
  error_message TEXT,
  error_code TEXT,
  occurred_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  resolved BOOLEAN DEFAULT false,
  resolved_at TIMESTAMP WITH TIME ZONE
);

-- Service capability metadata
CREATE TABLE automations.service_capabilities (
  service_name TEXT PRIMARY KEY,
  supports_webhooks BOOLEAN DEFAULT false,
  webhook_events TEXT[], -- Array of supported webhook event types
  supports_polling BOOLEAN DEFAULT true,
  polling_endpoints JSONB DEFAULT '{}', -- Endpoint configurations for polling
  rate_limits JSONB DEFAULT '{}', -- Default rate limit configurations
  auth_types TEXT[] DEFAULT ARRAY['oauth2'], -- Supported auth methods
  api_version TEXT,
  documentation_url TEXT,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Service automation templates
CREATE TABLE automations.service_automation_templates (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  service_name TEXT NOT NULL,
  template_name TEXT NOT NULL,
  description TEXT,
  trigger_pattern JSONB NOT NULL, -- Template for trigger configuration
  script_template TEXT NOT NULL, -- Python script template with placeholders
  parameter_schema JSONB, -- JSON schema for required parameters
  tags TEXT[] DEFAULT ARRAY[]::TEXT[],
  usage_count INTEGER DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  UNIQUE(service_name, template_name)
);

-- Update triggers for new tables
CREATE TRIGGER update_webhook_configs_updated_at 
  BEFORE UPDATE ON automations.service_webhook_configs 
  FOR EACH ROW EXECUTE FUNCTION automations.update_updated_at_column();

CREATE TRIGGER update_polling_state_updated_at 
  BEFORE UPDATE ON automations.service_polling_state 
  FOR EACH ROW EXECUTE FUNCTION automations.update_updated_at_column();

CREATE TRIGGER update_rate_limits_updated_at 
  BEFORE UPDATE ON automations.service_rate_limits 
  FOR EACH ROW EXECUTE FUNCTION automations.update_updated_at_column();

CREATE TRIGGER update_service_capabilities_updated_at 
  BEFORE UPDATE ON automations.service_capabilities 
  FOR EACH ROW EXECUTE FUNCTION automations.update_updated_at_column();

CREATE TRIGGER update_automation_templates_updated_at 
  BEFORE UPDATE ON automations.service_automation_templates 
  FOR EACH ROW EXECUTE FUNCTION automations.update_updated_at_column();

-- Indexes for performance
CREATE INDEX idx_webhook_configs_user_service ON automations.service_webhook_configs(user_id, service_name);
CREATE INDEX idx_webhook_configs_active ON automations.service_webhook_configs(active) WHERE active = true;
CREATE INDEX idx_webhook_configs_expires ON automations.service_webhook_configs(subscription_expires_at) WHERE subscription_expires_at IS NOT NULL;

CREATE INDEX idx_polling_state_user_service ON automations.service_polling_state(user_id, service_name);
CREATE INDEX idx_polling_state_next_poll ON automations.service_polling_state(next_poll_at) WHERE active = true;
CREATE INDEX idx_polling_state_active ON automations.service_polling_state(active) WHERE active = true;

CREATE INDEX idx_rate_limits_service_user ON automations.service_rate_limits(service_name, user_id);
CREATE INDEX idx_rate_limits_reset_at ON automations.service_rate_limits(reset_at);

CREATE INDEX idx_api_errors_user_service ON automations.service_api_errors(user_id, service_name);
CREATE INDEX idx_api_errors_occurred_at ON automations.service_api_errors(occurred_at);
CREATE INDEX idx_api_errors_unresolved ON automations.service_api_errors(resolved) WHERE resolved = false;

CREATE INDEX idx_automation_templates_service ON automations.service_automation_templates(service_name);
CREATE INDEX idx_automation_templates_tags ON automations.service_automation_templates USING GIN(tags);
CREATE INDEX idx_automation_templates_usage ON automations.service_automation_templates(usage_count DESC);
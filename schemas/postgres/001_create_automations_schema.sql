-- Migration: 001_create_automations_schema.sql
-- Description: Create automations schema and core tables for EDA system
-- Date: 2025-08-16

-- Create automations schema (public schema already exists)
CREATE SCHEMA IF NOT EXISTS automations;

-- Enable UUID extension if not already enabled
CREATE EXTENSION IF NOT EXISTS "uuid-ossp";

-- Automation definitions (automations schema)
CREATE TABLE automations.automations (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  description TEXT,
  trigger_type TEXT NOT NULL CHECK (trigger_type IN ('webhook', 'schedule', 'manual')),
  trigger_config JSONB NOT NULL DEFAULT '{}',
  script_code TEXT NOT NULL,
  execution_params JSONB DEFAULT '{}',
  dependencies TEXT[] DEFAULT ARRAY[]::TEXT[],
  active BOOLEAN DEFAULT true,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Execution history (automations schema)
CREATE TABLE automations.automation_runs (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  automation_id UUID NOT NULL REFERENCES automations.automations(id) ON DELETE CASCADE,
  trigger_data JSONB DEFAULT '{}',
  result JSONB DEFAULT '{}',
  error TEXT,
  duration_ms INTEGER,
  executed_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Service automation patterns (automations schema)
CREATE TABLE automations.service_automation_patterns (
  service_name TEXT PRIMARY KEY,
  optimization_hints JSONB DEFAULT '{}',
  webhook_config JSONB DEFAULT '{}',
  rate_limits JSONB DEFAULT '{}',
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Usage tracking (automations schema)
CREATE TABLE automations.automation_usage (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  automation_id UUID REFERENCES automations.automations(id) ON DELETE CASCADE,
  service_name TEXT,
  timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  tokens_used INTEGER DEFAULT 0,
  execution_time_ms INTEGER DEFAULT 0
);

-- Execution sessions (automations schema)
CREATE TABLE automations.automation_execution_sessions (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  automation_id UUID REFERENCES automations.automations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  session_token TEXT,
  sandbox_instance_id TEXT,
  started_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  completed_at TIMESTAMP WITH TIME ZONE,
  status TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'completed', 'failed'))
);

-- Audit log (automations schema)
CREATE TABLE automations.automation_audit_log (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  automation_id UUID REFERENCES automations.automations(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  service_accessed TEXT,
  action_performed TEXT,
  timestamp TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  success BOOLEAN DEFAULT false,
  error_message TEXT
);

-- Event queue table for webhook processing
CREATE TABLE automations.automation_events (
  id UUID PRIMARY KEY DEFAULT uuid_generate_v4(),
  user_id UUID NOT NULL REFERENCES public.user_profiles(id) ON DELETE CASCADE,
  service_name TEXT NOT NULL,
  event_type TEXT NOT NULL,
  event_id TEXT,
  event_data JSONB NOT NULL DEFAULT '{}',
  processed BOOLEAN DEFAULT false,
  retry_count INTEGER DEFAULT 0,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  processed_at TIMESTAMP WITH TIME ZONE,
  UNIQUE(service_name, event_id, user_id) -- Prevent duplicate events
);

-- Update timestamp trigger function
CREATE OR REPLACE FUNCTION automations.update_updated_at_column()
RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at = NOW();
  RETURN NEW;
END;
$$ language 'plpgsql';

-- Add update triggers
CREATE TRIGGER update_automations_updated_at 
  BEFORE UPDATE ON automations.automations 
  FOR EACH ROW EXECUTE FUNCTION automations.update_updated_at_column();

CREATE TRIGGER update_service_patterns_updated_at 
  BEFORE UPDATE ON automations.service_automation_patterns 
  FOR EACH ROW EXECUTE FUNCTION automations.update_updated_at_column();

-- Create indexes for performance
CREATE INDEX idx_automations_user_id ON automations.automations(user_id);
CREATE INDEX idx_automations_active ON automations.automations(active) WHERE active = true;
CREATE INDEX idx_automations_trigger_type ON automations.automations(trigger_type);
CREATE INDEX idx_automations_created_at ON automations.automations(created_at);

CREATE INDEX idx_automation_runs_automation_id ON automations.automation_runs(automation_id);
CREATE INDEX idx_automation_runs_executed_at ON automations.automation_runs(executed_at);

CREATE INDEX idx_automation_usage_user_id ON automations.automation_usage(user_id);
CREATE INDEX idx_automation_usage_automation_id ON automations.automation_usage(automation_id);
CREATE INDEX idx_automation_usage_timestamp ON automations.automation_usage(timestamp);

CREATE INDEX idx_automation_sessions_user_id ON automations.automation_execution_sessions(user_id);
CREATE INDEX idx_automation_sessions_status ON automations.automation_execution_sessions(status);
CREATE INDEX idx_automation_sessions_started_at ON automations.automation_execution_sessions(started_at);

CREATE INDEX idx_automation_audit_user_id ON automations.automation_audit_log(user_id);
CREATE INDEX idx_automation_audit_timestamp ON automations.automation_audit_log(timestamp);

CREATE INDEX idx_automation_events_user_id ON automations.automation_events(user_id);
CREATE INDEX idx_automation_events_processed ON automations.automation_events(processed) WHERE processed = false;
CREATE INDEX idx_automation_events_service ON automations.automation_events(service_name);
CREATE INDEX idx_automation_events_created_at ON automations.automation_events(created_at);
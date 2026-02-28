-- Migration: Add declarative actions support to automation_records
-- Date: 2025-12-08
-- Description: Add actions and variables columns for declarative automation execution,
--              and create execution logging table

-- ============================================================================
-- Add new columns to automation_records
-- ============================================================================

-- Add actions column for declarative tool-based execution
ALTER TABLE automations.automation_records
ADD COLUMN IF NOT EXISTS actions JSONB;

-- Add variables column for user-defined variables
ALTER TABLE automations.automation_records
ADD COLUMN IF NOT EXISTS variables JSONB DEFAULT '{}';

-- Add comments
COMMENT ON COLUMN automations.automation_records.actions IS 'Declarative JSON actions array referencing service tools';
COMMENT ON COLUMN automations.automation_records.variables IS 'User-defined variables available in action templates';

-- ============================================================================
-- Create automation_execution_logs table
-- ============================================================================

CREATE TABLE IF NOT EXISTS automations.automation_execution_logs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    automation_id UUID REFERENCES automations.automation_records(id) ON DELETE CASCADE NOT NULL,
    user_id UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,

    -- Execution metadata
    started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    completed_at TIMESTAMPTZ,
    duration_ms INTEGER,

    -- Trigger info
    trigger_type TEXT,  -- 'webhook', 'schedule', 'manual'
    trigger_data JSONB,

    -- Results
    status TEXT NOT NULL DEFAULT 'running',  -- 'running', 'completed', 'partial_failure', 'failed'
    actions_executed INTEGER DEFAULT 0,
    actions_failed INTEGER DEFAULT 0,

    -- Action-level details
    -- Array of {action_id, tool, success, output/error, duration_ms, skipped, condition_result}
    action_results JSONB,

    -- Error summary (if any actions failed)
    error_summary TEXT,

    created_at TIMESTAMPTZ DEFAULT now()
);

-- Add RLS policy for execution logs
ALTER TABLE automations.automation_execution_logs ENABLE ROW LEVEL SECURITY;

-- Policy: Users can only view their own execution logs
CREATE POLICY "Users can view their own execution logs" ON automations.automation_execution_logs
    FOR SELECT
    USING (auth.uid() = user_id);

-- Policy: Service role can insert execution logs
CREATE POLICY "Service role can insert execution logs" ON automations.automation_execution_logs
    FOR INSERT
    WITH CHECK (true);

-- Indexes for querying execution history
CREATE INDEX IF NOT EXISTS idx_execution_logs_user_id
    ON automations.automation_execution_logs(user_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_logs_automation_id
    ON automations.automation_execution_logs(automation_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_execution_logs_status
    ON automations.automation_execution_logs(status);

-- Add constraint for valid status values
ALTER TABLE automations.automation_execution_logs
ADD CONSTRAINT valid_status CHECK (status IN ('running', 'completed', 'partial_failure', 'failed'));

-- Comments
COMMENT ON TABLE automations.automation_execution_logs IS 'Logs of automation executions with action-level details';
COMMENT ON COLUMN automations.automation_execution_logs.action_results IS 'Array of action results: {action_id, tool, success, output, error, duration_ms, skipped, condition_result}';

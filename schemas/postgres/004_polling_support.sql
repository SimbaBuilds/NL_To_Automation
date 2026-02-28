-- Migration: Add polling columns to automation_records
-- Purpose: Enable automation-driven polling instead of service-level polling
-- This allows each polling automation to track its own polling state

-- Add polling-specific columns to automation_records
ALTER TABLE automations.automation_records
ADD COLUMN IF NOT EXISTS next_poll_at TIMESTAMPTZ,
ADD COLUMN IF NOT EXISTS last_poll_cursor TEXT,
ADD COLUMN IF NOT EXISTS polling_interval_minutes INT;

-- Create index for efficient polling queries
CREATE INDEX IF NOT EXISTS idx_automation_records_polling
ON automations.automation_records (trigger_type, next_poll_at)
WHERE active = true AND trigger_type = 'polling';

-- Comment the columns
COMMENT ON COLUMN automations.automation_records.next_poll_at IS 'When this polling automation should next be executed';
COMMENT ON COLUMN automations.automation_records.last_poll_cursor IS 'Cursor for deduplication (e.g., last date polled)';
COMMENT ON COLUMN automations.automation_records.polling_interval_minutes IS 'How often to poll in minutes';

-- Note: service_polling_state table is now deprecated
-- Polling state is tracked per-automation in automation_records

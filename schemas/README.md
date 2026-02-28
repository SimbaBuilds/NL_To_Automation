# Database Schemas

Portable PostgreSQL schemas for the automation system.

## Quick Start

Apply all migrations:

```bash
psql -U postgres -d your_database -f postgres/001_create_automations_schema.sql
psql -U postgres -d your_database -f postgres/002_service_integration_tables.sql
psql -U postgres -d your_database -f postgres/003_declarative_actions.sql
psql -U postgres -d your_database -f postgres/004_polling_support.sql
```

Or use Supabase migrations:

```bash
cp postgres/*.sql your-supabase-project/supabase/migrations/
supabase db push
```

## Schema Overview

### automations schema

Main namespace for automation tables:

- **automation_records** - Automation definitions (trigger, actions, variables)
- **automation_events** - Queued events from webhooks/polling
- **automation_execution_logs** - Execution history with action-level details
- **service_capabilities** - Service metadata (webhook support, polling support)

### Key Tables

#### automation_records

Stores the declarative automation definition:

```sql
{
  id: UUID,
  user_id: UUID,
  name: TEXT,
  trigger_type: ENUM('webhook', 'polling', 'schedule_once', 'schedule_recurring', 'manual'),
  trigger_config: JSONB,
  actions: JSONB,  -- Declarative action list
  variables: JSONB,
  status: ENUM('pending_review', 'active', 'paused', 'disabled'),
  next_poll_at: TIMESTAMPTZ,  -- For polling
  last_poll_cursor: TEXT       -- For polling pagination
}
```

#### automation_events

Event queue for webhooks and polling:

```sql
{
  id: UUID,
  user_id: UUID,
  service_name: TEXT,
  event_type: TEXT,
  event_data: JSONB,
  processed: BOOLEAN
}
```

#### automation_execution_logs

Execution history with detailed action results:

```sql
{
  id: UUID,
  automation_id: UUID,
  started_at: TIMESTAMPTZ,
  completed_at: TIMESTAMPTZ,
  status: TEXT,
  actions_executed: INTEGER,
  actions_failed: INTEGER,
  action_results: JSONB,  -- Array of per-action results
  error_summary: TEXT
}
```

## Row Level Security (RLS)

All tables have RLS policies:

- Users can only access their own automations
- Service role can access all records (for edge functions)

## Indexes

Optimized for:
- User-specific queries (`user_id`)
- Polling automation filtering (`trigger_type`, `next_poll_at`)
- Event processing (`processed`, `service_name`)
- Execution log retrieval (`automation_id`, `started_at`)

## Customization

To add custom fields:

1. Create a new migration file
2. Add columns to relevant tables
3. Update RLS policies if needed
4. Run migration

Example:

```sql
-- Add tags to automation_records
ALTER TABLE automations.automation_records
ADD COLUMN tags TEXT[];

-- Add GIN index for tag search
CREATE INDEX idx_automation_records_tags
ON automations.automation_records USING GIN(tags);
```

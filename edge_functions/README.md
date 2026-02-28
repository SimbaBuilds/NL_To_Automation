# Edge Functions

Supabase/Deno edge functions for automation execution.

## Functions

### webhook-handler
Receives and validates webhooks from external services (Gmail, Slack, Notion, etc.).

**Responsibilities:**
- Webhook signature verification
- Service-specific payload parsing
- Filter evaluation before queuing events
- Multi-tenant user resolution

**Deploy:**
```bash
supabase functions deploy webhook-handler
```

### script-executor
Executes declarative automations by calling the FastAPI backend.

**Responsibilities:**
- OAuth token refresh
- Credential fetching
- Declarative action execution via FastAPI
- Execution logging

**Deploy:**
```bash
supabase functions deploy script-executor
```

### scheduler-runner
Triggers polling and scheduled automations on intervals.

**Responsibilities:**
- Interval-based scheduling (5min, daily, weekly, etc.)
- Polling automation management
- Time-of-day constraints
- Concurrency control

**Deploy:**
```bash
supabase functions deploy scheduler-runner
```

## Shared Utilities

Located in `_shared/`:

- **polling-manager.ts** - Polling logic, state tracking, aggregation modes
- **filter-utils.ts** - Condition evaluation engine
- **base-handler.ts** - Base class with Supabase client, auth, logging

## Environment Variables

See each function's source code for required environment variables. Common ones:

```bash
SUPABASE_URL=your-project.supabase.co
SUPABASE_SERVICE_ROLE_KEY=your-service-role-key
FASTAPI_URL=https://your-backend.com
JWT_SECRET=your-jwt-secret

# OAuth Credentials (for token refresh)
GOOGLE_CLIENT_ID=...
GOOGLE_CLIENT_SECRET=...
# ... etc for other services
```

## Deployment

1. Install Supabase CLI: `npm install -g supabase`
2. Login: `supabase login`
3. Link project: `supabase link --project-ref your-project-ref`
4. Deploy all: `supabase functions deploy`

## Customization

To add support for new services:
1. Implement service parser in `_shared/service-parsers.ts` (or create plugin system)
2. Add webhook signature verification
3. Update `webhook-handler` to route to new parser

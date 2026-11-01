# Agentscale Web

React + TypeScript + Vite skeleton for the employee workspace migration.

## URL contract

- `/login` — auth entry point
- `/projects` — project index
- `/projects/:projectId` — project workspace
- `/projects/:projectId/chat` — new chat placeholder
- `/projects/:projectId/chat/:sessionId` — explicit chat session placeholder

The shell keeps cabinet, project, and session scope explicit. API transport lives under `src/api`; React contexts own session and work-context state. Backend integration is intentionally limited to typed boundaries until the live API contract is reconciled.

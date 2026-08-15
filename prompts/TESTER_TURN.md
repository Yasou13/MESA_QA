Execute the following test action against MESA using available MCP tools.

Action Details:
- Action ID: {action_id}
- Scenario Event ID: {scenario_event_id}
- Action Type: {action_type}
- Parameters: {parameters_json}

Instructions:
1. Call the appropriate MESA MCP tool to perform the action.
2. For every write, use the controller-supplied `idempotency_key` and retain the returned `operation_id` and `document_id`. If MESA reports `PENDING_APPROVAL`, return that result immediately so the controller can validate ownership and invoke MESA's official operator CLI. Never claim pending/accepted work as success. The controller will then poll `mesa_get_operation_status` to terminal finality.
3. Return a final JSON object adhering strictly to the tester_result schema:
{
  "action_id": "{action_id}",
  "scenario_event_id": "{scenario_event_id}",
  "tools_called": ["..."],
  "actual": {
    "answer": "...",
    "memory_ids": [...],
    "operation_ids": [...],
    "operation_id": "...",
    "document_id": "...",
    "operation_state": "COMMITTED|FAILED|REJECTED|PENDING_APPROVAL|..."
  },
  "tester_assessment": "pass|suspicious|infra_error",
  "reason": "...",
  "needs_recheck": false
}

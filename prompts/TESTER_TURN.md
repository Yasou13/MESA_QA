Execute the following test action against MESA using available MCP tools.

Action Details:
- Action ID: {action_id}
- Scenario Event ID: {scenario_event_id}
- Action Type: {action_type}
- Parameters: {parameters_json}

Instructions:
1. Call the appropriate MESA MCP tool to perform the action.
2. Return a final JSON object adhering strictly to the tester_result schema:
{
  "action_id": "{action_id}",
  "scenario_event_id": "{scenario_event_id}",
  "tools_called": ["..."],
  "actual": {
    "answer": "...",
    "memory_ids": [...],
    "operation_ids": [...]
  },
  "tester_assessment": "pass|suspicious|infra_error",
  "reason": "...",
  "needs_recheck": false
}

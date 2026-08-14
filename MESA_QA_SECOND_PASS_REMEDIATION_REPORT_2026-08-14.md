# MESA-QA Second-Pass Remediation Report

## Baseline and branch

- MESA-QA baseline: `c96ba8907b3f1994d6c1ff778f12dbb186de622e`
- MESA baseline observed: `d715cff6a2bbd30120caa635197875c68f92a42b` on `mvp/certification-round-3`
- Remediation branch: `fix/second-pass-remediation-2026-08-14`
- Original MESA checkout: read-only throughout this remediation.

## Implemented and tested remediation

| Audit ID | Status | Evidence |
|---|---|---|
| MQA-2P-002 | FIXED (unit coverage) | Canonical overlap, traversal and symlink rejection in `storage/paths.py`; 17 focused path/config tests pass. |
| MQA-2P-003 | FIXED (unit coverage) | Explicit placeholder rendering preserves JSON braces and loads `TESTER_SYSTEM.md`; all action kinds render. |
| MQA-2P-004 | PARTIAL | Binding now invokes the supported MESA console entrypoint, verifies binding files, doctor and active credential status, and launches Tester through `mesa codex run`. Live binding is blocked by the broken local Codex installation. |
| MQA-2P-005 | PARTIAL | Normal direct-HTTP fallback was removed; nonzero/unstructured Codex results are infra errors. Full live schema/finality proof remains blocked/not run. |
| MQA-2P-008 | PARTIAL | Oracle is now authoritative for evaluation, forgotten-value resurrection is rejected, replay is idempotent, and writes require terminal operation success. Full live operation polling still requires a functioning Tester. |
| MQA-2P-010 | FIXED (local doctor) | Doctor checks imports, supported MESA CLI, real Codex version/login, path safety, main hygiene and occupied ports; it correctly failed on the broken Codex environment. |
| MQA-2P-011 | PARTIAL | Added hard-gate unit coverage for paths, prompt, Codex failure, truth/replay, seed/reset, thread rotation and synthetic-regression refusal. |
| MQA-2P-012 | PARTIAL | Lite cadence restored to 45–120 seconds and repair is disabled by default. Resource-stop/retention wiring remains outstanding. |
| MQA-2P-001 | PARTIAL | Repair is default-disabled and cannot synthesize a captured-data regression or use `git add .`; it blocks without a genuine pre-fix source-path test. Full evidence/restart/live-repro pipeline remains outstanding. |

## Verification

- `pytest -q tests/unit --tb=short`: **41 passed**.
- `pytest -q tests/integration/test_fake_end_to_end.py --tb=short`: **1 passed**.
- `mesa-qa doctor`: **FAIL (expected)**. `codex --version` and `codex login status` fail because the installed Codex package is missing its Linux binary dependency. No API-key fallback was enabled.
- Real MCP smoke, Codex Tester, restart durability, and controlled repair E2E: **NOT RUN**. The Codex prerequisite is genuinely blocked; the remaining controller/control-plane hard gates are also incomplete.

## Remaining blockers / no-go items

`WAITING_FOR_CODEX` persistence/resume, operator pause/resume/stop/teardown, candidate worktree revalidation and bounded-diff lifecycle, evidence-derived reporting, enforced resource stops, full taxonomy, live MCP finality, restart/live repro and controlled repair E2E still require implementation and verification. Therefore this branch is **not ready for long-run autonomous testing** and has not been pushed as a finished remediation.

## Commits

- `6100214` fix: harden qa storage and run path containment
- `862640d` fix: repair tester prompt and mesa codex lifecycle
- `9108b0d` fix: wire authoritative ground truth and operation finality
- `e7c5860` fix: make doctor fail closed on codex and port readiness
- `2c5b624` fix: complete deterministic scenario and thread rotation lifecycle
- `4311b2f` fix: require genuine pre-fix regression evidence

You are repairing a confirmed MESA QA defect in an isolated QA candidate worktree.

Bug Evidence:
{evidence_summary}

Hard Rules:
1. Work ONLY in the current Git worktree directory.
2. NEVER checkout, merge, reset, push, or modify main.
3. NEVER modify forbidden paths (.github/, deploy/, uv.lock, pyproject.toml).
4. Do not change dependencies or database migration strategies automatically.
5. Reproduce the bug on the current candidate commit before patching.
6. Add a minimal regression test file in tests/ and prove it FAILS (PRE-FIX FAIL) before making any fix.
7. Identify the root cause and implement the smallest safe patch.
8. Run the new regression test and confirm it PASSES (POST-FIX PASS).
9. Run targeted tests for directly affected modules.
10. Do not change unrelated code or styling.

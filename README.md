# MESA-QA — Autonomous Resident Test Engineer

`MESA-QA` is a fully external, detachable, autonomous test-engineer system designed to evaluate long-term behavioral endurance, semantic memory correctness, temporal fact updates, cross-session persistence, and process restart durability of **MESA** through its canonical MCP interface.

When reproducible defects are detected, `MESA-QA` isolates the failure in a dedicated Git candidate worktree, writes pre-fix failing regression assertions, applies minimal patches, restarts the candidate runtime, and verifies resolution without risking the main MESA repository.

---

## 1. Safety Guarantees

1. **Main Repository Immunity**: The baseline MESA checkout (e.g., `/home/yasin/Desktop/MESA`) is strictly read-only. Edits occur exclusively inside an isolated Git candidate worktree (`/home/yasin/Desktop/MESA-QA-candidate`).
2. **Storage Isolation**: QA test data resides under `~/.local/share/mesa-qa/runs/<run_id>/mesa-storage`. Real user databases are never accessed or mutated.
3. **No Automatic Merge/Push**: Autonomous candidate repair commits are kept on `qa/autonomous-<run_id>`. Automatic merging or pushing to `main` is strictly forbidden.
4. **Independent Ground Truth**: Correctness is judged against a local SQLite Oracle database, never against subjective LLM opinions or self-referential database reads.
5. **Zero-Extra-Cost Mode**: Uses local Codex CLI, local Python environment, local SQLite, local Git, and local pytest. Zero paid third-party API dependencies.

---

## 2. Requirements

- Python 3.10–3.12. Python 3.13 is currently unsupported because the
  aiosqlite connection worker does not complete in the supported test/runtime
  stack.
- Git 2.30+
- MESA Repository Checkout (`/home/yasin/Desktop/MESA`)
- A supported candidate base interpreter (3.10–3.12; Python 3.12 is the
  default target). MESA-QA creates the actual candidate environment under its
  own run directory using MESA's locked `uv sync` bootstrap; it never modifies
  or reuses `/home/yasin/Desktop/MESA/.venv`.
- OpenAI Codex CLI (`codex`)

---

## 3. Quickstart & Installation

```bash
# Clone MESA-QA (independent repository)
git clone https://github.com/Yasou13/MESA_QA.git
cd MESA_QA

# Run prerequisite doctor check
PYTHONPATH=src /path/to/mesa-qa-python -m mesa_qa.cli doctor
```

---

## 4. Operational Runbook

### Prerequisites Check
```bash
mesa-qa doctor --mesa-repo /home/yasin/Desktop/MESA
```

### Initialize Run Workspace
```bash
mesa-qa init --mesa-repo /home/yasin/Desktop/MESA
```

### Run Endurance Test Profiles

**15-Minute Smoke Run:**
```bash
mesa-qa run --hours 0.25 --profile lite --mesa-repo /home/yasin/Desktop/MESA
```

**2-Hour Standard Run:**
```bash
mesa-qa run --hours 2.0 --profile standard --mesa-repo /home/yasin/Desktop/MESA
```

**8-Hour Full Endurance Run:**
```bash
mesa-qa run --hours 8.0 --profile lite --mesa-repo /home/yasin/Desktop/MESA
```

---

## 5. Control & Inspection Commands

```bash
# Check current active run status
mesa-qa status

# Pause the active endurance session
mesa-qa pause

# Resume a paused session
mesa-qa resume

# Emergency Stop
mesa-qa stop

# View Final Run Report
mesa-qa report <run_id>

# Teardown Candidate Worktree and Temporary Data
mesa-qa teardown <run_id>
```

---

## 6. Reviewing & Applying Autonomous Repairs

Candidate repair commits are created on `qa/autonomous-<run_id>` inside the candidate worktree.

**To Review Repairs:**
```bash
git -C /home/yasin/Desktop/MESA log -p main..qa/autonomous-<run_id>
```

**To Cherry-Pick Accepted Fixes to Main:**
```bash
git -C /home/yasin/Desktop/MESA checkout main
git -C /home/yasin/Desktop/MESA cherry-pick <commit_sha>
```

**To Discard Candidate Branch:**
```bash
git -C /home/yasin/Desktop/MESA branch -D qa/autonomous-<run_id>
```

---

## 7. Troubleshooting

- **MESA candidate runtime fails to start**: Ensure ports 18000 (MESA API) and 18765 (MCP Gateway) are free on localhost.
- **Codex CLI authentication**: Run `codex` once interactively to ensure your ChatGPT Plus / local subscription is active.
- **Fernet key errors**: Ensure `MESA_GATEWAY_ENCRYPTION_KEY` is a 32 url-safe base64 string.

---

## 8. Development & Testing

```bash
# Run all unit and integration tests
PYTHONPATH=src /path/to/mesa-qa-python -m pytest tests/unit tests/integration/
```

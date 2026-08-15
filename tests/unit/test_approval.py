from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from mesa_qa.mesa.approval import (
    ApprovalCommandError,
    CommandResult,
    OfficialApprovalLifecycle,
    OperationFinalityPoller,
    OperationOwnership,
    OperationOwnershipError,
)

OPERATION_ID = "op_" + "a" * 32


class FakeStatusClient:
    def __init__(self, statuses: list[str]):
        self.statuses = list(statuses)
        self.calls = 0

    async def get_operation_status(self, operation_id: str):
        index = min(self.calls, len(self.statuses) - 1)
        self.calls += 1
        return {"operation_id": operation_id, "status": self.statuses[index]}


class FakeCommandRunner:
    def __init__(self, result: CommandResult | None = None):
        self.result = result or CommandResult(
            0,
            '{"operation_id":"'
            + OPERATION_ID
            + '","status":"APPROVED","decision":"APPROVED"}\n',
        )
        self.commands: list[list[str]] = []

    async def __call__(self, command, _cwd, _env):
        self.commands.append(command)
        return self.result


@pytest.fixture
async def ledger(tmp_path: Path):
    control_db = tmp_path / "gateway.sqlite"
    async with aiosqlite.connect(control_db) as db:
        await db.executescript("""
            CREATE TABLE mcp_clients (
                client_id TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE mcp_project_bindings (
                binding_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                tenant_id TEXT NOT NULL,
                workspace_id TEXT NOT NULL,
                dataset_id TEXT NOT NULL,
                enabled INTEGER NOT NULL
            );
            CREATE TABLE mcp_operations (
                operation_id TEXT PRIMARY KEY,
                client_id TEXT NOT NULL,
                binding_id TEXT NOT NULL,
                tool_name TEXT NOT NULL,
                idempotency_key TEXT NOT NULL,
                status TEXT NOT NULL
            );
            INSERT INTO mcp_clients VALUES ('codex-qa-tester', 'local-qa-tester', 1);
            INSERT INTO mcp_project_bindings
            VALUES ('binding-qa', 'codex-qa-tester', 'default', 'default', 'default', 1);
            INSERT INTO mcp_operations
            VALUES (
                'op_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa',
                'codex-qa-tester',
                'binding-qa',
                'mesa_remember',
                'qa:run-1:act-1:1',
                'PENDING_APPROVAL'
            );
            """)
        await db.commit()
    return control_db


def ownership(**changes) -> OperationOwnership:
    values = {
        "run_id": "run-1",
        "idempotency_key": "qa:run-1:act-1:1",
        "tool_name": "mesa_remember",
        "client_id": "codex-qa-tester",
        "binding_id": "binding-qa",
        "principal_id": "local-qa-tester",
        "tenant_id": "default",
        "workspace_id": "default",
        "dataset_id": "default",
    }
    values.update(changes)
    return OperationOwnership(**values)


def lifecycle(
    tmp_path: Path,
    ledger: Path,
    statuses: list[str],
    runner: FakeCommandRunner,
    *,
    timeout: float = 0.1,
):
    status = FakeStatusClient(statuses)
    poller = OperationFinalityPoller(
        status, timeout_seconds=timeout, interval_seconds=0.001
    )
    return OfficialApprovalLifecycle(
        candidate_worktree=tmp_path,
        mesa_cli=tmp_path / "mesa",
        mesa_admin_cli=tmp_path / "mesa-v4-admin",
        control_db_path=ledger,
        policy_db_path=tmp_path / "rbac.sqlite",
        operator_principal="mesa-qa-operator",
        poller=poller,
        command_runner=runner,
    )


@pytest.mark.asyncio
async def test_owned_operation_may_use_official_approval_and_reach_committed(
    tmp_path, ledger
):
    runner = FakeCommandRunner()
    helper = lifecycle(
        tmp_path,
        ledger,
        ["PENDING_APPROVAL", "APPROVED", "PROCESSING", "COMMITTED"],
        runner,
    )

    result = await helper.approve_and_wait(OPERATION_ID, ownership())

    assert result.outcome == "PASS"
    assert result.final_status == "COMMITTED"
    assert result.approval_invoked is True
    assert [item["status"] for item in result.status_transitions] == [
        "APPROVED",
        "PROCESSING",
        "COMMITTED",
    ]
    assert runner.commands[0][1:3] == ["operations", "approve"]
    assert runner.commands[0][-1] == "MESA-QA synthetic test run run-1"


@pytest.mark.asyncio
async def test_identity_mismatch_fails_closed_without_approval(tmp_path, ledger):
    runner = FakeCommandRunner()
    helper = lifecycle(tmp_path, ledger, ["PENDING_APPROVAL"], runner)

    with pytest.raises(OperationOwnershipError) as caught:
        await helper.approve_and_wait(
            OPERATION_ID, ownership(idempotency_key="qa:run-1:other:1")
        )

    assert caught.value.outcome == "NEEDS_REVIEW"
    assert runner.commands == []


@pytest.mark.asyncio
async def test_official_approval_cli_nonzero_is_failure(tmp_path, ledger):
    runner = FakeCommandRunner(CommandResult(2, stderr="not authorized"))
    helper = lifecycle(tmp_path, ledger, ["PENDING_APPROVAL"], runner)

    with pytest.raises(ApprovalCommandError, match="not authorized"):
        await helper.approve_and_wait(OPERATION_ID, ownership())


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["FAILED", "REJECTED", "CANCELLED"])
async def test_accepted_approval_terminal_failure_is_fail(tmp_path, ledger, terminal):
    runner = FakeCommandRunner()
    helper = lifecycle(tmp_path, ledger, ["PENDING_APPROVAL", terminal], runner)

    result = await helper.approve_and_wait(OPERATION_ID, ownership())

    assert result.outcome == "FAIL"
    assert result.final_status == terminal


@pytest.mark.asyncio
async def test_accepted_approval_polling_timeout_is_failure(tmp_path, ledger):
    runner = FakeCommandRunner()
    helper = lifecycle(
        tmp_path, ledger, ["PENDING_APPROVAL", "PROCESSING"], runner, timeout=0.005
    )

    result = await helper.approve_and_wait(OPERATION_ID, ownership())

    assert result.outcome == "FAIL"
    assert result.final_status == "TIMEOUT"
    assert result.approval_invoked is True
    assert result.error["code"] == "FINALITY_TIMEOUT"


@pytest.mark.asyncio
async def test_already_committed_is_not_reapproved(tmp_path, ledger):
    runner = FakeCommandRunner()
    helper = lifecycle(tmp_path, ledger, ["COMMITTED"], runner)

    result = await helper.approve_and_wait(OPERATION_ID, ownership())

    assert result.outcome == "PASS"
    assert result.approval_invoked is False
    assert runner.commands == []


@pytest.mark.asyncio
async def test_unknown_operation_fails_closed(tmp_path, ledger):
    runner = FakeCommandRunner()
    helper = lifecycle(tmp_path, ledger, ["PENDING_APPROVAL"], runner)

    with pytest.raises(OperationOwnershipError, match="unknown"):
        await helper.approve_and_wait("op_" + "b" * 32, ownership())

    assert runner.commands == []


@pytest.mark.asyncio
async def test_unknown_status_fails_closed(tmp_path, ledger):
    runner = FakeCommandRunner()
    helper = lifecycle(tmp_path, ledger, ["MYSTERY"], runner)

    with pytest.raises(Exception, match="unknown operation state"):
        await helper.approve_and_wait(OPERATION_ID, ownership())

    assert runner.commands == []

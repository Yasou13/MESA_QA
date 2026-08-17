from __future__ import annotations

import asyncio
import json
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Mapping

import aiosqlite
import httpx

_OPERATION_ID = re.compile(r"op_[0-9a-f]{32}\Z")
_INTERMEDIATE_STATES = {
    "CREATED",
    "PENDING_APPROVAL",
    "APPROVED",
    "DISPATCHING",
    "SUBMITTED",
    "PROCESSING",
}
_FAILURE_STATES = {"FAILED", "REJECTED", "DENIED", "CANCELLED"}


class ApprovalLifecycleError(RuntimeError):
    def __init__(self, message: str, *, outcome: str = "FAIL") -> None:
        super().__init__(message)
        self.outcome = outcome


class OperationOwnershipError(ApprovalLifecycleError):
    def __init__(self, message: str) -> None:
        super().__init__(message, outcome="NEEDS_REVIEW")


class ApprovalCommandError(ApprovalLifecycleError):
    pass


class OperationFinalityTimeout(ApprovalLifecycleError):
    def __init__(self, message: str, *, transitions: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.transitions = transitions


@dataclass(frozen=True)
class OperationOwnership:
    run_id: str
    idempotency_key: str
    tool_name: str
    client_id: str
    binding_id: str
    principal_id: str
    tenant_id: str
    workspace_id: str
    dataset_id: str


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


@dataclass
class ApprovalLifecycleResult:
    operation_id: str
    outcome: str
    final_status: str
    ownership_verified: bool
    approval_invoked: bool
    approval_reason: str
    approval_command: list[str] | None = None
    approval_result: dict[str, Any] | None = None
    status_transitions: list[dict[str, Any]] = field(default_factory=list)
    error: dict[str, Any] | None = None

    def evidence(self) -> dict[str, Any]:
        return asdict(self)


CommandRunner = Callable[[list[str], Path, Mapping[str, str]], Awaitable[CommandResult]]


async def _run_command(
    command: list[str], cwd: Path, env: Mapping[str, str]
) -> CommandResult:
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=str(cwd),
        env=dict(env),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return CommandResult(
        returncode=process.returncode or 0,
        stdout=stdout.decode("utf-8", errors="replace"),
        stderr=stderr.decode("utf-8", errors="replace"),
    )


class MCPStatusClient:
    """Call the real public ``mesa_get_operation_status`` gateway tool."""

    def __init__(
        self,
        *,
        gateway_url: str,
        credential: str,
        request_timeout_seconds: float = 10.0,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self._credential = credential
        self.request_timeout_seconds = request_timeout_seconds

    async def get_operation_status(self, operation_id: str) -> dict[str, Any]:
        async with httpx.AsyncClient(timeout=self.request_timeout_seconds) as client:
            response = await client.post(
                f"{self.gateway_url}/mcp/v1/tools/call",
                headers={"Authorization": f"Bearer {self._credential}"},
                json={
                    "name": "mesa_get_operation_status",
                    "arguments": {"operation_id": operation_id},
                },
            )
        response.raise_for_status()
        envelope = response.json()
        if envelope.get("isError") is not False:
            raise ApprovalLifecycleError(
                "mesa_get_operation_status returned an MCP error"
            )
        try:
            result = json.loads(envelope["content"][0]["text"])
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise ApprovalLifecycleError(
                "mesa_get_operation_status returned an invalid envelope"
            ) from exc
        if not isinstance(result, dict) or result.get("operation_id") != operation_id:
            raise ApprovalLifecycleError(
                "mesa_get_operation_status returned the wrong operation identity"
            )
        return result


class OperationFinalityPoller:
    def __init__(
        self,
        status_client: MCPStatusClient,
        *,
        timeout_seconds: float,
        interval_seconds: float,
    ) -> None:
        if timeout_seconds <= 0 or interval_seconds <= 0:
            raise ValueError("poll timeout and interval must be positive")
        self.status_client = status_client
        self.timeout_seconds = timeout_seconds
        self.interval_seconds = interval_seconds

    async def wait(
        self, operation_id: str, *, initial: dict[str, Any] | None = None
    ) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
        started = time.monotonic()
        transitions: list[dict[str, Any]] = []
        current = initial
        last_status: str | None = None
        while True:
            if current is None:
                try:
                    current = await self.status_client.get_operation_status(
                        operation_id
                    )
                except ApprovalLifecycleError:
                    raise
                except Exception as exc:
                    raise ApprovalLifecycleError(
                        f"mesa_get_operation_status failed: {type(exc).__name__}"
                    ) from exc
            status = str(current.get("status", "")).strip().upper()
            if status != last_status:
                transitions.append(
                    {
                        "status": status or "UNKNOWN",
                        "observed_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
                last_status = status
            if status == "COMMITTED":
                return status, transitions, current
            if status in _FAILURE_STATES:
                return status, transitions, current
            if status not in _INTERMEDIATE_STATES:
                raise ApprovalLifecycleError(
                    f"unknown operation state {status or '<empty>'}; failing closed"
                )
            if time.monotonic() - started >= self.timeout_seconds:
                raise OperationFinalityTimeout(
                    f"operation {operation_id} did not reach terminal state within "
                    f"{self.timeout_seconds:g}s",
                    transitions=transitions,
                )
            await asyncio.sleep(self.interval_seconds)
            current = None


class OfficialApprovalLifecycle:
    """Ownership-safe adapter around MESA's official operator CLI and status tool."""

    def __init__(
        self,
        *,
        candidate_worktree: Path,
        mesa_cli: Path,
        mesa_admin_cli: Path,
        control_db_path: Path,
        policy_db_path: Path,
        operator_principal: str,
        poller: OperationFinalityPoller,
        command_runner: CommandRunner = _run_command,
    ) -> None:
        self.candidate_worktree = candidate_worktree.resolve()
        self.mesa_cli = mesa_cli.resolve()
        self.mesa_admin_cli = mesa_admin_cli.resolve()
        self.control_db_path = control_db_path.resolve()
        self.policy_db_path = policy_db_path.resolve()
        self.operator_principal = operator_principal
        self.poller = poller
        self._command_runner = command_runner

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        env["PYTHONPATH"] = str(self.candidate_worktree)
        env["VIRTUAL_ENV"] = str(self.mesa_cli.parent.parent)
        env["PATH"] = f"{self.mesa_cli.parent}:{env.get('PATH', '')}"
        return env

    async def provision_operator(self) -> dict[str, Any]:
        command = [
            str(self.mesa_admin_cli),
            "--policy-db",
            str(self.policy_db_path),
            "grant-control",
            "--principal",
            self.operator_principal,
        ]
        result = await self._command_runner(
            command, self.candidate_worktree, self._environment()
        )
        if result.returncode != 0:
            raise ApprovalCommandError(
                "official MESA control-role provisioning failed: "
                + (result.stderr or result.stdout).strip()[-500:]
            )
        return {
            "principal": self.operator_principal,
            "status": "ADMIN_GRANTED",
        }

    async def provision_qa_scope(
        self,
        *,
        mesa_api_url: str,
        mesa_api_key: str,
        api_principal: str,
        actor_id: str,
        tenant_id: str,
        workspace_id: str,
        dataset_id: str,
    ) -> dict[str, Any]:
        """Provision the public V4 prerequisites in QA storage via MESA CLIs/API."""
        commands = [
            [
                str(self.mesa_admin_cli),
                "--policy-db",
                str(self.policy_db_path),
                "grant-role",
                "--principal",
                api_principal,
                "--tenant",
                tenant_id,
                "--workspace",
                workspace_id,
                "--dataset",
                dataset_id,
                "--role",
                "OWNER",
            ],
            [
                str(self.mesa_admin_cli),
                "--policy-db",
                str(self.policy_db_path),
                "grant-agent",
                "--principal",
                api_principal,
                "--agent",
                actor_id,
                "--permission",
                "SESSION_CREATE",
            ],
        ]
        for command in commands:
            result = await self._command_runner(
                command, self.candidate_worktree, self._environment()
            )
            if result.returncode != 0:
                raise ApprovalCommandError(
                    "official MESA QA-scope provisioning failed: "
                    + (result.stderr or result.stdout).strip()[-500:]
                )
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.post(
                mesa_api_url.rstrip("/") + "/v4/catalog/datasets",
                headers={"X-API-Key": mesa_api_key},
                json={
                    "tenant_id": tenant_id,
                    "workspace_id": workspace_id,
                    "dataset_id": dataset_id,
                    "tenant_name": "MESA-QA",
                    "workspace_name": "MESA-QA",
                    "dataset_name": "MESA-QA synthetic data",
                },
            )
        if response.status_code not in {201, 409}:
            raise ApprovalCommandError(
                f"public MESA QA catalog provisioning failed with HTTP {response.status_code}"
            )
        return {
            "api_principal": api_principal,
            "actor_id": actor_id,
            "tenant_id": tenant_id,
            "workspace_id": workspace_id,
            "dataset_id": dataset_id,
            "status": "QA_SCOPE_READY",
        }

    async def approve_and_wait(
        self, operation_id: str, ownership: OperationOwnership
    ) -> ApprovalLifecycleResult:
        await self._inspect_and_validate(operation_id, ownership)
        reason = f"MESA-QA synthetic test run {ownership.run_id}"
        current = await self.poller.status_client.get_operation_status(operation_id)
        current_status = str(current.get("status", "")).upper()
        approval_invoked = False
        approval_command: list[str] | None = None
        approval_result: dict[str, Any] | None = None

        if current_status == "PENDING_APPROVAL":
            approval_command = self._approval_command(operation_id, reason)
            approval_result = await self._approve(operation_id, reason)
            approval_invoked = True
            current = None
        elif current_status not in _INTERMEDIATE_STATES | _FAILURE_STATES | {
            "COMMITTED"
        }:
            raise ApprovalLifecycleError(
                f"unknown operation state {current_status or '<empty>'}; failing closed"
            )

        try:
            final_status, transitions, terminal = await self.poller.wait(
                operation_id, initial=current
            )
        except OperationFinalityTimeout as exc:
            return ApprovalLifecycleResult(
                operation_id=operation_id,
                outcome="FAIL",
                final_status="TIMEOUT",
                ownership_verified=True,
                approval_invoked=approval_invoked,
                approval_reason=reason,
                approval_command=approval_command,
                approval_result=approval_result,
                status_transitions=exc.transitions,
                error={"code": "FINALITY_TIMEOUT", "message": str(exc)},
            )
        outcome = "PASS" if final_status == "COMMITTED" else "FAIL"
        error = (
            terminal.get("error") if isinstance(terminal.get("error"), dict) else None
        )
        return ApprovalLifecycleResult(
            operation_id=operation_id,
            outcome=outcome,
            final_status=final_status,
            ownership_verified=True,
            approval_invoked=approval_invoked,
            approval_reason=reason,
            approval_command=approval_command,
            approval_result=approval_result,
            status_transitions=transitions,
            error=error,
        )

    async def _approve(self, operation_id: str, reason: str) -> dict[str, Any]:
        command = self._approval_command(operation_id, reason)
        result = await self._command_runner(
            command, self.candidate_worktree, self._environment()
        )
        if result.returncode != 0:
            raise ApprovalCommandError(
                "official MESA approval CLI failed: "
                + (result.stderr or result.stdout).strip()[-500:]
            )
        try:
            payload = json.loads(result.stdout.strip().splitlines()[-1])
        except (IndexError, json.JSONDecodeError) as exc:
            raise ApprovalCommandError(
                "official MESA approval CLI returned invalid JSON"
            ) from exc
        if (
            payload.get("operation_id") != operation_id
            or payload.get("status") != "APPROVED"
            or payload.get("decision") != "APPROVED"
        ):
            raise ApprovalCommandError(
                "official MESA approval CLI returned an unexpected decision"
            )
        return payload

    def _approval_command(self, operation_id: str, reason: str) -> list[str]:
        return [
            str(self.mesa_cli),
            "operations",
            "approve",
            operation_id,
            "--control-db",
            str(self.control_db_path),
            "--policy-db",
            str(self.policy_db_path),
            "--principal",
            self.operator_principal,
            "--reason",
            reason,
        ]

    async def _inspect_and_validate(
        self, operation_id: str, expected: OperationOwnership
    ) -> dict[str, Any]:
        if not _OPERATION_ID.fullmatch(operation_id):
            raise OperationOwnershipError("operation_id has an invalid MESA format")
        query = """
            SELECT
                operation.operation_id,
                operation.client_id,
                operation.binding_id,
                operation.tool_name,
                operation.idempotency_key,
                operation.status,
                client.principal_id,
                client.enabled AS client_enabled,
                binding.tenant_id,
                binding.workspace_id,
                binding.dataset_id,
                binding.enabled AS binding_enabled
            FROM mcp_operations AS operation
            JOIN mcp_clients AS client
              ON client.client_id = operation.client_id
            JOIN mcp_project_bindings AS binding
              ON binding.binding_id = operation.binding_id
             AND binding.client_id = operation.client_id
            WHERE operation.operation_id = ?
        """
        try:
            async with aiosqlite.connect(self.control_db_path) as db:
                db.row_factory = aiosqlite.Row
                async with db.execute(query, (operation_id,)) as cursor:
                    row = await cursor.fetchone()
        except aiosqlite.Error as exc:
            raise OperationOwnershipError(
                "could not verify operation ownership in the MESA ledger"
            ) from exc
        if row is None:
            raise OperationOwnershipError(
                "operation is unknown to the current QA gateway ledger"
            )
        operation = dict(row)
        comparisons = {
            "operation_id": operation_id,
            "client_id": expected.client_id,
            "binding_id": expected.binding_id,
            "tool_name": expected.tool_name,
            "idempotency_key": expected.idempotency_key,
            "principal_id": expected.principal_id,
            "tenant_id": expected.tenant_id,
            "workspace_id": expected.workspace_id,
            "dataset_id": expected.dataset_id,
        }
        mismatches = [
            field
            for field, value in comparisons.items()
            if operation.get(field) != value
        ]
        if not operation.get("client_enabled"):
            mismatches.append("client_enabled")
        if not operation.get("binding_enabled"):
            mismatches.append("binding_enabled")
        if not expected.idempotency_key.startswith(f"qa:{expected.run_id}:"):
            mismatches.append("run_id")
        if mismatches:
            raise OperationOwnershipError(
                "operation ownership mismatch: " + ", ".join(sorted(set(mismatches)))
            )
        return operation

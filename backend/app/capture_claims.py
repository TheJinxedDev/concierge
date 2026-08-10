"""Disposable claim and lock coordination around the capture state store.

The lock file is the operating-system acquisition authority for P3.3. Its
validated ``CaptureLock`` payload mirrors the versioned state contract, while
``CaptureStateStore`` remains the durable state writer. No scheduler, live
source reader, MCP call, or production path is involved here.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import wraps
import json
import os
from pathlib import Path
from typing import Any
from uuid import uuid4

from .capture_batch import FailureDecision
from .capture_state import (
    CaptureLock,
    ClaimRecord,
    ClaimStatus,
    LockOutcome,
    ReasonCode,
    TerminalDisposition,
)
from .capture_state_store import CaptureStateStore
from .file_lock import exclusive_file_lock


class CaptureLockError(RuntimeError):
    """Base error for unsafe lock-file state."""


class LockOwnershipError(CaptureLockError):
    """The requested operation is not owned by the supplied run/token pair."""


class LockRecordError(CaptureLockError):
    """The lock file exists but does not contain a valid lock record."""


class LockRecoveryError(CaptureLockError):
    """A stale lock could not be safely reclaimed."""


class ClaimError(RuntimeError):
    """Base error for unsafe claim ownership or state transitions."""


class ClaimOwnershipError(ClaimError):
    """The active lock does not belong to the requested claim owner."""


class ClaimConflictError(ClaimError):
    """A claim ID was reused for a different source or content hash."""


class ClaimBusyError(ClaimError):
    """A non-stale claim is still owned or otherwise not reclaimable."""


class ClaimNotFoundError(ClaimError):
    """A transition referenced no persisted claim."""


class ClaimStateError(ClaimError):
    """A claim transition is invalid for its current status."""


@dataclass(frozen=True)
class LockAcquireResult:
    outcome: LockOutcome
    lock: CaptureLock
    reason_code: ReasonCode | None = None
    audit_path: Path | None = None


@dataclass(frozen=True)
class LockReleaseResult:
    outcome: LockOutcome
    lock: CaptureLock
    error: str | None = None


@dataclass(frozen=True)
class ClaimResult:
    claim: ClaimRecord
    recorded: bool
    recovered: bool


def _guard_claim_mutation(method):
    @wraps(method)
    def guarded(self, *args, **kwargs):
        with self.lock_manager.coordination_guard():
            return method(self, *args, **kwargs)

    return guarded


class CaptureLockManager:
    """Acquire, inspect, reclaim, and release one short-lived lock file."""

    def __init__(
        self,
        path: Path,
        *,
        audit_directory: Path | None = None,
        state_store: CaptureStateStore | None = None,
    ) -> None:
        self.path = Path(path)
        self.audit_directory = audit_directory or self.path.parent / f"{self.path.stem}-audit"
        self.state_store = state_store

    def _coordination_lock_path(self) -> Path:
        return self.path.with_name(f".{self.path.name}.coordination.lock")

    def coordination_guard(self):
        """Serialize cooperating lock and claim mutations for this lock path."""
        return exclusive_file_lock(self._coordination_lock_path())

    def current_lock(self) -> CaptureLock:
        """Read and validate the current lock record."""

        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            return CaptureLock.model_validate(payload)
        except FileNotFoundError as error:
            raise LockOwnershipError("capture lock is not held") from error
        except (OSError, json.JSONDecodeError, ValueError) as error:
            raise LockRecordError("capture lock is not a valid record") from error

    def acquire(
        self,
        owner_run_id: str,
        owner_token: str,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> LockAcquireResult:
        with self.coordination_guard():
            return self._acquire_unlocked(
                owner_run_id,
                owner_token,
                now=now,
                ttl=ttl,
            )

    def _acquire_unlocked(
        self,
        owner_run_id: str,
        owner_token: str,
        *,
        now: datetime,
        ttl: timedelta,
    ) -> LockAcquireResult:
        """Atomically acquire a lock or skip/reclaim an existing owner."""

        candidate = self._new_lock(owner_run_id, owner_token, now, ttl)
        try:
            self._create_exclusive(self.path, candidate.model_dump(mode="json"))
            try:
                self._sync_state_lock(candidate)
            except BaseException:
                self._unlink_after_failed_state_sync(candidate)
                raise
            return LockAcquireResult(LockOutcome.ACQUIRED, candidate)
        except FileExistsError:
            pass

        current = self.current_lock()
        if not current.is_stale(now):
            return LockAcquireResult(
                LockOutcome.SKIPPED,
                current,
                reason_code=ReasonCode.LOCK_SKIPPED,
            )

        audit_path = self._write_reclaim_audit(current, candidate, now)
        self._unlink_exact(current)

        reclaimed = self._new_lock(
            owner_run_id,
            owner_token,
            now,
            ttl,
            reclaimed_from_run_id=current.owner_run_id,
            reclaimed_at=now,
        )
        try:
            self._create_exclusive(self.path, reclaimed.model_dump(mode="json"))
        except FileExistsError as error:
            raise LockRecoveryError("another owner acquired the lock during reclaim") from error
        try:
            self._sync_state_lock(reclaimed)
        except BaseException:
            self._unlink_after_failed_state_sync(reclaimed)
            raise
        return LockAcquireResult(
            LockOutcome.RECLAIMED,
            reclaimed,
            reason_code=ReasonCode.STALE_LOCK_RECLAIMED,
            audit_path=audit_path,
        )

    def assert_owner(self, owner_run_id: str, owner_token: str) -> CaptureLock:
        """Return the lock only when the supplied run/token owns it."""

        lock = self.current_lock()
        if lock.owner_run_id != owner_run_id or lock.owner_token != owner_token:
            raise LockOwnershipError("capture lock is owned by another run")
        return lock

    def release(self, owner_run_id: str, owner_token: str) -> LockReleaseResult:
        """Release the lock, reporting filesystem failure without hiding it."""

        with self.coordination_guard():
            return self._release_unlocked(owner_run_id, owner_token)

    def _release_unlocked(self, owner_run_id: str, owner_token: str) -> LockReleaseResult:
        """Release a lock while the coordination guard is held."""

        lock = self.assert_owner(owner_run_id, owner_token)
        try:
            if self.current_lock() != lock:
                return LockReleaseResult(
                    LockOutcome.RELEASE_FAILED,
                    lock,
                    error="lock changed during release",
                )
            os.unlink(self.path)
        except OSError as error:
            return LockReleaseResult(
                LockOutcome.RELEASE_FAILED,
                lock,
                error=str(error),
            )
        try:
            self._sync_state_lock(None)
        except BaseException as error:
            return LockReleaseResult(
                LockOutcome.RELEASE_FAILED,
                lock,
                error=f"lock metadata release failed: {error}",
            )
        return LockReleaseResult(LockOutcome.RELEASED, lock)

    def _sync_state_lock(self, lock: CaptureLock | None) -> None:
        if self.state_store is None:
            return
        state = self.state_store.read()
        next_state = state.model_copy(deep=True)
        next_state.lock = lock
        self.state_store.replace(next_state)

    def _unlink_exact(self, expected: CaptureLock) -> None:
        try:
            observed = self.current_lock()
        except CaptureLockError as error:
            raise LockRecoveryError("lock changed or disappeared during reclaim") from error
        if observed != expected:
            raise LockRecoveryError("lock changed during stale reclaim")
        try:
            os.unlink(self.path)
        except FileNotFoundError as error:
            raise LockRecoveryError("stale lock disappeared during reclaim") from error
        except OSError as error:
            raise LockRecoveryError("stale lock could not be removed") from error

    def _unlink_after_failed_state_sync(self, expected: CaptureLock) -> None:
        try:
            if self.current_lock() != expected:
                return
            os.unlink(self.path)
        except (CaptureLockError, FileNotFoundError):
            pass

    @staticmethod
    def _new_lock(
        owner_run_id: str,
        owner_token: str,
        now: datetime,
        ttl: timedelta,
        *,
        reclaimed_from_run_id: str | None = None,
        reclaimed_at: datetime | None = None,
    ) -> CaptureLock:
        return CaptureLock(
            lock_name="concierge-capture",
            owner_run_id=owner_run_id,
            owner_token=owner_token,
            acquired_at=now,
            expires_at=now + ttl,
            reclaimed_from_run_id=reclaimed_from_run_id,
            reclaimed_at=reclaimed_at,
        )

    def _write_reclaim_audit(
        self,
        previous: CaptureLock,
        replacement: CaptureLock,
        now: datetime,
    ) -> Path:
        self.audit_directory.mkdir(parents=True, exist_ok=True)
        path = self.audit_directory / f"reclaim-{now.strftime('%Y%m%dT%H%M%S%fZ')}-{uuid4().hex}.json"
        previous_lock = previous.model_dump(mode="json")
        previous_lock["owner_token"] = "[REDACTED]"
        payload = {
            "event": "stale_lock_reclaimed",
            "reclaimed_at": now.isoformat(),
            "previous_owner_run_id": previous.owner_run_id,
            "previous_owner_token": "[REDACTED]",
            "replacement_owner_run_id": replacement.owner_run_id,
            "replacement_owner_token": "[REDACTED]",
            "previous_lock": previous_lock,
        }
        self._create_exclusive(path, payload)
        return path

    @staticmethod
    def _create_exclusive(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(
            path,
            os.O_CREAT | os.O_EXCL | os.O_WRONLY,
            0o600,
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(payload, handle, ensure_ascii=False, separators=(",", ":"), sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            raise


class CaptureClaimLedger:
    """Persist claims only while the caller owns the current capture lock."""

    def __init__(self, state_store: CaptureStateStore, lock_manager: CaptureLockManager):
        self.state_store = state_store
        self.lock_manager = lock_manager

    @_guard_claim_mutation
    def claim_action(
        self,
        claim_id: str,
        *,
        source_ref: str,
        content_hash: str,
        owner_run_id: str,
        owner_token: str,
        now: datetime,
        expires_at: datetime,
    ) -> ClaimResult:
        """Create or recover one claim under the current lock owner."""

        self._require_owner(owner_run_id, owner_token, now)
        state = self.state_store.read()
        existing = state.claims.get(claim_id)
        if existing is not None:
            if existing.source_ref != source_ref or existing.content_hash != content_hash:
                raise ClaimConflictError(
                    f"claim {claim_id!r} conflicts with its stored source identity"
                )
            if existing.status is ClaimStatus.TERMINAL:
                return ClaimResult(existing, recorded=False, recovered=False)
            if existing.status is ClaimStatus.BLOCKED:
                raise ClaimBusyError(
                    f"claim {claim_id!r} is blocked and requires independent outcome verification"
                )
            if existing.status is ClaimStatus.RETRYABLE:
                if now < existing.expires_at:
                    raise ClaimBusyError(f"claim {claim_id!r} is still within its retry lease")
                attempt_count = existing.attempt_count + 1
                recovered = True
            else:
                if existing.owner_run_id == owner_run_id and now < existing.expires_at:
                    return ClaimResult(existing, recorded=False, recovered=False)
                if now < existing.expires_at:
                    raise ClaimBusyError(f"claim {claim_id!r} is still owned by another run")
                attempt_count = existing.attempt_count + 1
                recovered = True
        else:
            attempt_count = 1
            recovered = False

        claim = ClaimRecord(
            claim_id=claim_id,
            source_ref=source_ref,
            content_hash=content_hash,
            status=ClaimStatus.IN_PROGRESS,
            owner_run_id=owner_run_id,
            attempt_count=attempt_count,
            claimed_at=now,
            updated_at=now,
            expires_at=expires_at,
            result_id=None,
            proposal_id=None,
            disposition=None,
            failure_class=None,
        )
        next_state = state.model_copy(deep=True)
        next_state.claims[claim_id] = claim
        self._require_owner(owner_run_id, owner_token, now)
        self.state_store.replace(next_state)
        return ClaimResult(claim, recorded=True, recovered=recovered)

    @_guard_claim_mutation
    def complete_claim(
        self,
        claim_id: str,
        *,
        source_ref: str,
        content_hash: str,
        owner_run_id: str,
        owner_token: str,
        now: datetime,
        disposition: TerminalDisposition,
        result_id: str | None = None,
        proposal_id: str | None = None,
    ) -> ClaimResult:
        """Record a terminal disposition without accepting orphan claims."""

        self._require_owner(owner_run_id, owner_token, now)
        state = self.state_store.read()
        existing = state.claims.get(claim_id)
        if existing is None:
            raise ClaimNotFoundError(f"claim {claim_id!r} does not exist")
        if existing.owner_run_id != owner_run_id:
            raise ClaimOwnershipError(f"claim {claim_id!r} belongs to another run")
        if existing.source_ref != source_ref or existing.content_hash != content_hash:
            raise ClaimConflictError(
                f"claim {claim_id!r} conflicts with its stored source identity"
            )
        if existing.status is ClaimStatus.TERMINAL:
            if (
                existing.disposition is not disposition
                or existing.result_id != result_id
                or existing.proposal_id != proposal_id
            ):
                raise ClaimConflictError(
                    f"terminal claim {claim_id!r} conflicts with its stored outcome"
                )
            return ClaimResult(existing, recorded=False, recovered=False)
        if existing.status is not ClaimStatus.IN_PROGRESS:
            raise ClaimStateError(
                f"claim {claim_id!r} cannot complete from {existing.status.value}"
            )

        payload = existing.model_dump(mode="python")
        payload.update(
            status=ClaimStatus.TERMINAL,
            updated_at=now,
            result_id=result_id,
            proposal_id=proposal_id,
            disposition=disposition,
            failure_class=None,
        )
        completed = ClaimRecord.model_validate(payload)
        next_state = state.model_copy(deep=True)
        next_state.claims[claim_id] = completed
        self._require_owner(owner_run_id, owner_token, now)
        self.state_store.replace(next_state)
        return ClaimResult(completed, recorded=True, recovered=False)

    @_guard_claim_mutation
    def fail_claim(
        self,
        claim_id: str,
        *,
        owner_run_id: str,
        owner_token: str,
        now: datetime,
        decision: FailureDecision,
        result_id: str | None = None,
        proposal_id: str | None = None,
    ) -> ClaimResult:
        """Persist a retryable, terminal, or blocked failure decision."""

        self._require_owner(owner_run_id, owner_token, now)
        state = self.state_store.read()
        existing = state.claims.get(claim_id)
        if existing is None:
            raise ClaimNotFoundError(f"claim {claim_id!r} does not exist")
        if existing.owner_run_id != owner_run_id:
            raise ClaimOwnershipError(f"claim {claim_id!r} belongs to another run")
        if existing.status is not ClaimStatus.IN_PROGRESS:
            raise ClaimStateError(
                f"claim {claim_id!r} cannot fail from {existing.status.value}"
            )

        payload = existing.model_dump(mode="python")
        payload.update(
            status=decision.claim_status,
            updated_at=now,
            result_id=result_id,
            proposal_id=proposal_id,
            disposition=decision.disposition,
            failure_class=(
                decision.failure_class
                if decision.claim_status in {ClaimStatus.RETRYABLE, ClaimStatus.BLOCKED}
                else None
            ),
        )
        failed = ClaimRecord.model_validate(payload)
        next_state = state.model_copy(deep=True)
        next_state.claims[claim_id] = failed
        self._require_owner(owner_run_id, owner_token, now)
        self.state_store.replace(next_state)
        return ClaimResult(failed, recorded=True, recovered=False)

    def _require_owner(self, owner_run_id: str, owner_token: str, now: datetime) -> CaptureLock:
        try:
            lock = self.lock_manager.assert_owner(owner_run_id, owner_token)
        except LockOwnershipError as error:
            raise ClaimOwnershipError(str(error)) from error
        if lock.is_stale(now):
            raise ClaimOwnershipError("capture lock has expired")
        return lock


def claim_counts_as_success(state: Any, claim_id: str) -> bool:
    """Return true only for a fully linked terminal successful claim."""

    claim = state.claims.get(claim_id)
    return bool(
        claim is not None
        and claim.status is ClaimStatus.TERMINAL
        and claim.disposition is TerminalDisposition.PROPOSED_SUCCESSFULLY
        and claim.result_id
        and claim.proposal_id
    )

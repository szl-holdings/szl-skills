"""Dependency-injected fail-closed orchestration for the CPU broker boundary.

This module is deliberately not a broker, permit authenticator, observer,
sandbox, or SSH client. It only orders trusted collaborators supplied by a
future broker. A successful state transition is still a local DECLARED result;
it does not certify a VM, model, isolation boundary, or scientific output.
"""

from copy import deepcopy
from dataclasses import dataclass

from tools.ssh_ci_admission import validate_permit
from tools.ssh_ci_contract import ContractError, canonical, sha256, validate_request


EXECUTION_SCHEMA = "szl.ssh-cpu-build.execution.v1"


@dataclass(frozen=True)
class OwnerPermit:
    """A loader result whose authentication was performed outside this module."""

    permit: object
    owner_authenticated: bool


def _result(result, stage, reason, *, claim_status="NOT_ATTEMPTED",
            executor_called=False, receipt_status="UNAVAILABLE"):
    return {
        "schema": EXECUTION_SCHEMA,
        "result": result,
        "evidence_class": "DECLARED",
        "stage": stage,
        "reason": reason,
        "claim_status": claim_status,
        "executor_called": executor_called,
        "receipt_status": receipt_status,
        "signed": False,
        "independent_witness": False,
    }


def execute(request, *, permit_loader, observation_reader, claim_store_factory,
            executor, clock):
    """Order injected owner and broker components without retries.

    The sequence is request validation, owner-authenticated permit loading,
    fresh observation and validation, durable claim acquisition, a second
    observation/validation while the lock is held, and only then executor
    invocation. A failed second observation or executor leaves the claim
    consumed. ``clock`` must return an aware UTC datetime. These collaborators
    are trusted integration points; this function does not authenticate them.
    """
    try:
        request = validate_request(request)
    except (ContractError, TypeError, ValueError):
        return _result("BLOCKED", "REQUEST", "INVALID_REQUEST")

    try:
        loaded = permit_loader(deepcopy(request))
    except Exception:
        return _result("BLOCKED", "PERMIT", "PERMIT_LOADER_UNAVAILABLE")
    if not isinstance(loaded, OwnerPermit):
        return _result("BLOCKED", "PERMIT", "PERMIT_LOADER_INVALID")
    if type(loaded.owner_authenticated) is not bool or not loaded.owner_authenticated:
        return _result("BLOCKED", "PERMIT", "OWNER_AUTHENTICATION_UNAVAILABLE")

    try:
        observation = observation_reader()
        now = clock()
        permit = validate_permit(request, loaded.permit, observation, now)
    except (ContractError, TypeError, ValueError):
        return _result("BLOCKED", "ADMISSION", "INITIAL_OBSERVATION_REJECTED")
    except Exception:
        return _result("BLOCKED", "ADMISSION", "INITIAL_OBSERVATION_UNAVAILABLE")

    # The v1 remote receipt contains positive runtime_before/after identity.
    # An idle permit waits for a separately versioned receipt profile.
    if permit["workload"]["mode"] != "pinned":
        return _result("BLOCKED", "ADMISSION", "IDLE_RECEIPT_PROFILE_UNSUPPORTED")

    request_digest = sha256(canonical(request))
    claim_status = "NOT_ATTEMPTED"
    try:
        with claim_store_factory() as store:
            try:
                store.claim(request["request_id"], request["permit_id"], request_digest)
                claim_status = "WRITTEN"
            except Exception:
                # A storage error may have left an O_EXCL claim behind. Never
                # report it as reusable and never attempt a second claim.
                return _result("BLOCKED", "CLAIM", "CLAIM_UNAVAILABLE",
                               claim_status="UNKNOWN")

            try:
                observation_after = observation_reader()
                now_after = clock()
                permit_after = validate_permit(
                    request, permit, observation_after, now_after)
            except (ContractError, TypeError, ValueError):
                return _result("BLOCKED", "REVALIDATION", "WORKLOAD_DRIFT",
                               claim_status=claim_status)
            except Exception:
                return _result("BLOCKED", "REVALIDATION",
                               "REOBSERVATION_UNAVAILABLE",
                               claim_status=claim_status)

            try:
                executor_result = executor(
                    deepcopy(request), deepcopy(permit_after),
                    {"request_sha256": request_digest},
                    deepcopy(observation_after),
                )
            except Exception:
                return _result("BLOCKED", "EXECUTOR", "EXECUTOR_FAILED",
                               claim_status=claim_status, executor_called=True)
            if type(executor_result) is not dict:
                return _result("BLOCKED", "EXECUTOR", "EXECUTOR_RESULT_INVALID",
                               claim_status=claim_status, executor_called=True)
            try:
                receipt = deepcopy(executor_result)
                if len(canonical(receipt)) > 1024 * 1024:
                    raise ValueError("executor result exceeds local bound")
            except Exception:
                return _result("BLOCKED", "EXECUTOR", "EXECUTOR_RESULT_INVALID",
                               claim_status=claim_status, executor_called=True)
            return _result("PASS", "EXECUTOR", "EXECUTED",
                           claim_status=claim_status, executor_called=True,
                           receipt_status="RETURNED") | {"receipt": receipt}
    except Exception:
        # Context acquisition/release failures cannot prove whether a claim was
        # consumed. Preserve deny-by-default and prohibit replay.
        return _result("BLOCKED", "CLAIM", "CLAIM_STORE_UNAVAILABLE",
                       claim_status=claim_status)


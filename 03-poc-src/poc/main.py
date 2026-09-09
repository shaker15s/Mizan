"""Main CLI presentation boundary for the Agent-Native ERP POC."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from poc.agent_runtime import AgentResult, CONFIRMATION_REQUIRED, CONFIRMED_EXECUTION
from poc.bootstrap import build_runtime

_SUCCESS_STATUSES = {"accepted", "replay"}
_NON_ERROR_OUTCOMES = {"text_only"}
_USAGE = "Usage: python -m poc.main \"طلب بالعربية\" | python -m poc.main --interactive"


def _build_payload(result: AgentResult) -> dict[str, Any]:
    structured_error = result.structured_error
    gateway_result = result.gateway_result
    status = gateway_result.status if gateway_result is not None else result.outcome
    if structured_error is not None:
        success = False
    elif status in _SUCCESS_STATUSES or result.outcome in _NON_ERROR_OUTCOMES:
        success = True
    elif status == CONFIRMATION_REQUIRED:
        success = None
    else:
        success = False
    payload: dict[str, Any] = {
        "success": success,
        "status": status,
        "response_ar": result.response_ar,
        "result": dict(gateway_result.result) if gateway_result is not None and gateway_result.result is not None else None,
        "error": structured_error.to_dict()["error"] if structured_error is not None else None,
        "audit_id": gateway_result.audit_id if gateway_result is not None else None,
    }
    # A replayed result that carries a stored failure is not a success (B4 fix).
    if success and isinstance(payload["result"], dict) and payload["result"].get("status") == "error":
        payload["success"] = False
        payload["error"] = {"code": payload["result"].get("error_code"), "message": "العملية السابقة فشلت؛ النتيجة معاد إرجاعها من السجل."}
    if gateway_result is not None and gateway_result.proposal is not None:
        payload["proposal"] = dict(gateway_result.proposal)
    return payload


def _print_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload, ensure_ascii=False), file=sys.stdout)


def _print_human(text: str) -> None:
    print(text, file=sys.stderr)


def _print_proposal(result: AgentResult) -> None:
    gateway_result = result.gateway_result
    proposal = gateway_result.proposal if gateway_result is not None else None
    if proposal is None:
        _print_human("العملية تحتاج تأكيدًا، لكن لا يوجد proposal صالح.")
        return
    arguments = json.dumps(proposal.get("arguments", {}), ensure_ascii=False, sort_keys=True)
    _print_human("⚠️  Confirmation required")
    _print_human(f"Tool: {proposal.get('tool_name')}")
    _print_human(f"Arguments: {arguments}")
    _print_human(f"Proposal ID: {proposal.get('proposal_id')}")
    _print_human(f"Operation hash: {proposal.get('operation_hash')}")
    _print_human(f"Expires: {proposal.get('expires_at')}")


def _ask_confirmation(
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = _print_human,
) -> bool:
    while True:
        try:
            answer = input_fn("Confirm? (y/n): ").strip().lower()
        except EOFError:
            raise
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        output_fn("Please answer y or n.")


def _run_request(
    runtime: Any,
    user_input: str,
    *,
    input_fn: Callable[[str], str] = input,
    output_fn: Callable[[str], None] = _print_human,
) -> tuple[dict[str, Any], int]:
    result = runtime.process(user_input)
    gateway_result = result.gateway_result
    original_proposal: dict[str, Any] | None = None
    if gateway_result is not None and gateway_result.status == CONFIRMATION_REQUIRED:
        _print_proposal(result)
        proposal = gateway_result.proposal
        if not proposal:
            payload = _build_payload(result)
            return payload, 1
        original_proposal = dict(proposal)
        approved = _ask_confirmation(input_fn=input_fn, output_fn=output_fn)
        result = runtime.confirm(proposal["proposal_id"]) if approved else runtime.decline(proposal["proposal_id"])
    payload = _build_payload(result)
    if original_proposal is not None and "proposal" not in payload:
        payload["proposal"] = original_proposal
    _print_human(result.response_ar)
    if payload["success"] is False:
        return payload, 1
    return payload, 0


def _run_interactive(runtime: Any, *, input_fn: Callable[[str], str] = input) -> int:
    _print_human("Agent-Native ERP POC. اكتب طلبك، أو اضغط Ctrl+C للخروج.")
    while True:
        try:
            user_input = input_fn("> ").strip()
        except EOFError:
            _print_human("تم الخروج.")
            return 0
        if not user_input:
            continue
        _payload, _exit_code = _run_request(runtime, user_input)
        # Stay in the loop on failures; only EOF/Ctrl+C ends the session (B7 fix).


def _parse_args(argv: list[str]) -> tuple[str | None, bool]:
    if "--interactive" in argv:
        positional = [argument for argument in argv if argument != "--interactive"]
        if positional:
            raise ValueError(_USAGE)
        return None, True
    if len(argv) != 1 or argv[0].startswith("-"):
        raise ValueError(_USAGE)
    user_input = argv[0].strip()
    if not user_input:
        raise ValueError(_USAGE)
    return user_input, False


def main(
    argv: list[str] | None = None,
    runtime_factory: Callable[..., Any] = build_runtime,
    input_fn: Callable[[str], str] = input,
) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    try:
        user_input, interactive = _parse_args(argv)
        runtime = runtime_factory()
        if interactive:
            return _run_interactive(runtime, input_fn=input_fn)
        _payload, exit_code = _run_request(runtime, user_input or "", input_fn=input_fn)
        _print_json(_payload)
        return exit_code
    except (EOFError, KeyboardInterrupt):
        _print_human("تم إيقاف العملية بواسطة المستخدم.")
        return 130
    except ValueError as error:
        _print_human(str(error))
        return 2
    except Exception:
        _print_human("حدث خطأ غير متوقع في CLI.")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

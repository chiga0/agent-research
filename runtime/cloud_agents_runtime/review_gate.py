from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


REVIEW_GATE_ARTIFACT = "review_gate.json"
DEFAULT_GATE_TYPE = "reviewer"
ALLOWED_DECISIONS = {"pass", "warn", "block", "needs_human"}
ALLOWED_SEVERITIES = {"none", "low", "medium", "high", "critical"}
BLOCKING_SEVERITIES = {"high", "critical"}
JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL | re.IGNORECASE)


@dataclass(frozen=True)
class ReviewFinding:
    id: str
    severity: str
    category: str
    message: str
    evidence: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ReviewGate:
    decision: str
    effective_decision: str
    severity: str
    reason: str
    findings: list[ReviewFinding] = field(default_factory=list)
    valid: bool = True
    error: str | None = None
    source_artifact: str = REVIEW_GATE_ARTIFACT
    gate_type: str = DEFAULT_GATE_TYPE

    @property
    def blocks(self) -> bool:
        return self.effective_decision in {"block", "needs_human"}

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["findings"] = [finding.to_dict() for finding in self.findings]
        data["blocks"] = self.blocks
        return data


def is_review_gate_task(profile_snapshot: dict[str, Any]) -> bool:
    return gate_type(profile_snapshot) == "reviewer"


def is_structured_gate_task(profile_snapshot: dict[str, Any]) -> bool:
    return gate_config(profile_snapshot) is not None


def gate_config(profile_snapshot: dict[str, Any]) -> dict[str, Any] | None:
    artifacts = profile_snapshot.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        return None
    gate = artifacts.get("gate") or {}
    if not isinstance(gate, dict) or not isinstance(gate.get("type"), str):
        return None
    return gate


def gate_type(profile_snapshot: dict[str, Any]) -> str | None:
    gate = gate_config(profile_snapshot)
    if gate is None:
        return None
    return str(gate["type"]).strip().lower() or None


def review_gate_artifact_name(profile_snapshot: dict[str, Any]) -> str:
    return gate_artifact_name(profile_snapshot)


def gate_artifact_name(profile_snapshot: dict[str, Any]) -> str:
    artifacts = profile_snapshot.get("artifacts") or {}
    if not isinstance(artifacts, dict):
        return REVIEW_GATE_ARTIFACT
    gate = artifacts.get("gate") or {}
    if isinstance(gate, dict) and isinstance(gate.get("artifact"), str):
        return gate["artifact"]
    return REVIEW_GATE_ARTIFACT


def load_review_gate(
    run_dir: Path,
    artifact_name: str = REVIEW_GATE_ARTIFACT,
    gate_type_name: str = DEFAULT_GATE_TYPE,
) -> ReviewGate:
    path = run_dir / artifact_name
    if not path.exists():
        return invalid_gate(
            f"missing required review gate artifact: {artifact_name}",
            source_artifact=artifact_name,
            gate_type_name=gate_type_name,
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return invalid_gate(
            f"invalid review gate json: {exc.msg}",
            source_artifact=artifact_name,
            gate_type_name=gate_type_name,
        )
    if not isinstance(payload, dict):
        return invalid_gate(
            "review gate artifact must be a JSON object",
            source_artifact=artifact_name,
            gate_type_name=gate_type_name,
        )
    return parse_review_gate(
        payload,
        source_artifact=artifact_name,
        gate_type_name=gate_type_name,
    )


def parse_review_gate_from_text(
    text: str,
    source_artifact: str = REVIEW_GATE_ARTIFACT,
    gate_type_name: str = DEFAULT_GATE_TYPE,
) -> ReviewGate:
    payload = extract_json_object(text)
    if payload is None:
        return invalid_gate(
            "could not extract review gate JSON object from agent text",
            source_artifact=source_artifact,
            gate_type_name=gate_type_name,
        )
    return parse_review_gate(
        payload,
        source_artifact=source_artifact,
        gate_type_name=gate_type_name,
    )


def extract_json_object(text: str) -> dict[str, Any] | None:
    for match in JSON_FENCE_RE.finditer(text):
        try:
            payload = json.loads(match.group(1))
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    decoder = json.JSONDecoder()
    for index, character in enumerate(text):
        if character != "{":
            continue
        try:
            payload, _end = decoder.raw_decode(text[index:])
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            return payload
    return None


def parse_review_gate(
    payload: dict[str, Any],
    source_artifact: str = REVIEW_GATE_ARTIFACT,
    gate_type_name: str = DEFAULT_GATE_TYPE,
) -> ReviewGate:
    decision = str(payload.get("decision") or "").strip().lower()
    if decision not in ALLOWED_DECISIONS:
        return invalid_gate(
            "decision must be pass, warn, block, or needs_human",
            source_artifact=source_artifact,
            gate_type_name=gate_type_name,
        )

    severity = normalize_severity(payload.get("severity"))
    findings_payload = payload.get("findings") or []
    if not isinstance(findings_payload, list):
        return invalid_gate(
            "findings must be a list",
            source_artifact=source_artifact,
            gate_type_name=gate_type_name,
        )

    findings: list[ReviewFinding] = []
    for index, item in enumerate(findings_payload, start=1):
        if not isinstance(item, dict):
            return invalid_gate(
                f"finding {index} must be an object",
                source_artifact=source_artifact,
                gate_type_name=gate_type_name,
            )
        finding = parse_finding(item, index)
        if finding is None:
            return invalid_gate(
                f"finding {index} is missing id, message, or structured evidence",
                source_artifact=source_artifact,
                gate_type_name=gate_type_name,
            )
        findings.append(finding)

    max_severity = max_severity_of([severity, *(finding.severity for finding in findings)])
    effective = decision
    if decision == "pass" and max_severity in BLOCKING_SEVERITIES:
        effective = "block"
    elif decision == "warn" and max_severity in BLOCKING_SEVERITIES:
        effective = "block"
    reason = str(payload.get("reason") or default_reason(effective, max_severity))
    return ReviewGate(
        decision=decision,
        effective_decision=effective,
        severity=max_severity,
        reason=reason,
        findings=findings,
        source_artifact=source_artifact,
        gate_type=gate_type_name,
    )


def invalid_gate(
    error: str,
    source_artifact: str = REVIEW_GATE_ARTIFACT,
    gate_type_name: str = DEFAULT_GATE_TYPE,
) -> ReviewGate:
    return ReviewGate(
        decision="needs_human",
        effective_decision="needs_human",
        severity="critical",
        reason="review gate could not be trusted",
        valid=False,
        error=error,
        source_artifact=source_artifact,
        gate_type=gate_type_name,
    )


def parse_finding(payload: dict[str, Any], index: int) -> ReviewFinding | None:
    finding_id = payload.get("id") or f"finding-{index}"
    severity = normalize_severity(payload.get("severity"))
    message = payload.get("message")
    if not isinstance(finding_id, str) or not isinstance(message, str) or not message.strip():
        return None
    evidence = payload.get("evidence") or {}
    if not isinstance(evidence, dict):
        return None
    return ReviewFinding(
        id=finding_id.strip(),
        severity=severity,
        category=str(payload.get("category") or "general"),
        message=message.strip(),
        evidence=dict(evidence),
    )


def normalize_severity(value: Any) -> str:
    severity = str(value or "none").strip().lower()
    if severity not in ALLOWED_SEVERITIES:
        return "critical"
    return severity


def max_severity_of(values: list[str]) -> str:
    order = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
    return max(values, key=lambda severity: order.get(severity, 4), default="none")


def default_reason(effective_decision: str, severity: str) -> str:
    if effective_decision == "block":
        return f"blocking review finding severity: {severity}"
    if effective_decision == "needs_human":
        return "review requires human decision"
    if effective_decision == "warn":
        return f"review completed with warning severity: {severity}"
    return "review gate passed"

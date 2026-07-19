from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pnu_event_gate.analysis import run_ai_analysis  # noqa: E402
from pnu_event_gate.evidence import EvidenceBundle, EvidenceChunk  # noqa: E402
from pnu_event_gate.nvidia import (  # noqa: E402
    DEFAULT_CHAT_MODEL,
    DEFAULT_EMBEDDING_MODEL,
    NvidiaClient,
)


DEFAULT_CASES = ROOT / "evaluation" / "ai_quality_cases.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate the NVIDIA notice-analysis flow.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES))
    parser.add_argument("--output")
    parser.add_argument("--api-key-env", default="NVIDIA_API_KEY")
    parser.add_argument("--chat-model", default=DEFAULT_CHAT_MODEL)
    parser.add_argument("--embedding-model", default=DEFAULT_EMBEDDING_MODEL)
    parser.add_argument("--no-embeddings", action="store_true")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args(argv)

    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))
    if args.limit is not None:
        cases = cases[: args.limit]
    client = NvidiaClient.from_env(args.api_key_env)
    results = []
    started = time.perf_counter()
    for case in cases:
        case_started = time.perf_counter()
        bundle = EvidenceBundle(
            chunks=[EvidenceChunk(**chunk) for chunk in case["chunks"]],
            warnings=[],
            redaction_counts={},
            source_count=len(case["chunks"]),
        )
        try:
            analysis = run_ai_analysis(
                client=client,
                request=case["request"],
                evidence=bundle,
                notice=case.get("notice") or {},
                chat_model=args.chat_model,
                embedding_model=args.embedding_model,
                use_embeddings=not args.no_embeddings,
                lexical_pool_size=max(40, len(bundle.chunks)),
                top_k=min(12, len(bundle.chunks)),
            )
            results.append(score_case(case, analysis, time.perf_counter() - case_started))
        except Exception as error:  # noqa: BLE001 - retain partial evaluation output.
            results.append(
                {
                    "id": case["id"],
                    "passed": False,
                    "error": f"{type(error).__name__}: {error}",
                    "latency_seconds": round(time.perf_counter() - case_started, 3),
                }
            )

    completed = [result for result in results if "error" not in result]
    total_fact_groups = sum(result["fact_group_count"] for result in completed)
    matched_fact_groups = sum(result["fact_group_matches"] for result in completed)
    total_intent_terms = sum(result["intent_term_count"] for result in completed)
    matched_intent_terms = sum(result["intent_term_matches"] for result in completed)
    report = {
        "type": "pnu_notice_ai_quality_report",
        "provider": "nvidia",
        "models": {
            "chat": args.chat_model,
            "embedding": None if args.no_embeddings else args.embedding_model,
        },
        "case_count": len(results),
        "completed_case_count": len(completed),
        "classification_accuracy": ratio(
            sum(result["classification_correct"] for result in completed), len(completed)
        ),
        "required_fact_recall": ratio(matched_fact_groups, total_fact_groups),
        "intent_term_recall": ratio(matched_intent_terms, total_intent_terms),
        "citation_valid_rate": ratio(
            sum(result["citations_valid"] for result in completed), len(completed)
        ),
        "retrieval_expected_hit_rate": ratio(
            sum(result["retrieval_expected_hit"] for result in completed), len(completed)
        ),
        "all_checks_pass_rate": ratio(
            sum(result["passed"] for result in results), len(results)
        ),
        "elapsed_seconds": round(time.perf_counter() - started, 3),
        "results": results,
    }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered + "\n", encoding="utf-8")
    return 0 if len(completed) == len(results) else 1


def score_case(case: dict[str, Any], analysis: dict[str, Any], latency: float) -> dict[str, Any]:
    decision = analysis["decision"]
    expected = case["expected_classification"]
    classification_correct = decision["classification"] == expected
    answer = normalize(
        " ".join(
            [decision.get("summary", "")]
            + [fact.get("text", "") for fact in decision.get("facts", [])]
        )
    )
    groups = case.get("required_fact_groups") or []
    fact_checks = [
        any(normalize(alternative) in answer for alternative in alternatives)
        for alternatives in groups
    ]
    valid_ids = {chunk["id"] for chunk in case["chunks"]}
    cited_ids = decision.get("evidence_ids") or []
    facts_grounded = all(fact.get("evidence_ids") for fact in decision.get("facts") or [])
    citations_valid = facts_grounded and all(item in valid_ids for item in cited_ids)
    retrieved_ids = {item["id"] for item in analysis["retrieval"]["selected"]}
    expected_ids = set(case.get("expected_evidence_ids") or [])
    retrieval_expected_hit = not expected_ids or bool(retrieved_ids.intersection(expected_ids))
    facts_pass = all(fact_checks)
    intent = analysis["intent"]
    intent_text = normalize(
        " ".join(
            [intent.get("request", ""), intent.get("time_scope") or ""]
            + intent.get("exact_terms", [])
            + intent.get("semantic_terms", [])
            + [
                str(entity.get("value") or "")
                for entity in intent.get("entities") or []
                if isinstance(entity, dict)
            ]
        )
    )
    expected_intent_terms = case.get("expected_intent_terms") or []
    intent_checks = [normalize(term) in intent_text for term in expected_intent_terms]
    intent_pass = all(intent_checks)
    passed = (
        classification_correct
        and facts_pass
        and intent_pass
        and citations_valid
        and retrieval_expected_hit
    )
    return {
        "id": case["id"],
        "passed": passed,
        "expected_classification": expected,
        "actual_classification": decision["classification"],
        "classification_correct": classification_correct,
        "fact_group_count": len(groups),
        "fact_group_matches": sum(fact_checks),
        "fact_checks": fact_checks,
        "intent_term_count": len(expected_intent_terms),
        "intent_term_matches": sum(intent_checks),
        "intent_checks": intent_checks,
        "citations_valid": citations_valid,
        "retrieval_expected_hit": retrieval_expected_hit,
        "cited_evidence_ids": cited_ids,
        "summary": decision.get("summary"),
        "facts": decision.get("facts"),
        "latency_seconds": round(latency, 3),
        "warnings": analysis.get("warnings") or [],
    }


def normalize(value: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", value).casefold())


def ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


if __name__ == "__main__":
    raise SystemExit(main())

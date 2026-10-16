"""Central pricing, approval, budget, and usage controls for paid AI calls.

Quotes contain no message text. They bind one reviewed price to the exact prompt
with a short-lived fingerprint so a changed prompt must be priced again.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import threading
import uuid

import anthropic


ROOT = Path(__file__).resolve().parents[1]
LOCAL = ROOT / ".ai-local"
SETTINGS = LOCAL / "settings.json"
LEDGER = LOCAL / "usage.jsonl"
DEFAULT_DAILY_LIMIT_USD = 1.00
QUOTE_TTL_SECONDS = 30 * 60

HAIKU = "claude-haiku-5-5"
SONNET = "claude-sonnet-5-5"
OPUS = "claude-opus-4-8"

# USD per one million tokens. Haiku automatically uses long-context pricing
# when the counted prompt exceeds 100,000 tokens.
MODEL_PRICES = {
    HAIKU: {"input": 0.10, "output": 0.50, "long_input": 0.50, "long_output": 2.50},
    SONNET: {"input": 2.00, "output": 10.00},
    OPUS: {"input": 5.00, "output": 25.00},
    "gpt-4o-mini": {"input": 0.15, "output": 0.60},
}

_lock = threading.RLock()


class AIApprovalError(RuntimeError):
    pass


class AIBudgetError(RuntimeError):
    pass


@dataclass(frozen=True)
class AIQuote:
    quote_id: str
    feature: str
    provider: str
    model: str
    input_tokens: int
    max_output_tokens: int
    expected_output_tokens: int
    input_cost_usd: float
    expected_output_cost_usd: float
    max_output_cost_usd: float
    expected_total_usd: float
    maximum_total_usd: float
    hard_ceiling_usd: float
    prompt_fingerprint: str
    created_at: str
    counted_by_provider: bool

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_value(cls, value: "AIQuote | dict") -> "AIQuote":
        return value if isinstance(value, cls) else cls(**value)


def _money(value: float) -> float:
    return round(float(value), 8)


def _payload(system: str, messages: list[dict], model: str, max_tokens: int) -> str:
    return json.dumps({"system": system, "messages": messages, "model": model,
                       "max_tokens": int(max_tokens)}, sort_keys=True,
                      ensure_ascii=False, separators=(",", ":"))


def prompt_fingerprint(system: str, messages: list[dict], model: str,
                       max_tokens: int) -> str:
    return hashlib.sha256(_payload(system, messages, model, max_tokens).encode("utf-8")).hexdigest()


def _rates(model: str, input_tokens: int) -> tuple[float, float]:
    price = MODEL_PRICES.get(model)
    if not price:
        raise ValueError(f"No price is configured for {model}.")
    if model == HAIKU and input_tokens > 100_000:
        return price["long_input"], price["long_output"]
    return price["input"], price["output"]


def quote_from_count(feature: str, model: str, input_tokens: int,
                     max_output_tokens: int, fingerprint: str,
                     *, provider: str = "anthropic",
                     counted_by_provider: bool = True) -> AIQuote:
    in_rate, out_rate = _rates(model, input_tokens)
    expected_output = min(max_output_tokens, max(64, int(input_tokens * 0.18)))
    input_cost = input_tokens * in_rate / 1_000_000
    expected_output_cost = expected_output * out_rate / 1_000_000
    max_output_cost = max_output_tokens * out_rate / 1_000_000
    maximum = input_cost + max_output_cost
    return AIQuote(
        quote_id=uuid.uuid4().hex,
        feature=feature,
        provider=provider,
        model=model,
        input_tokens=int(input_tokens),
        max_output_tokens=int(max_output_tokens),
        expected_output_tokens=int(expected_output),
        input_cost_usd=_money(input_cost),
        expected_output_cost_usd=_money(expected_output_cost),
        max_output_cost_usd=_money(max_output_cost),
        expected_total_usd=_money(input_cost + expected_output_cost),
        maximum_total_usd=_money(maximum),
        hard_ceiling_usd=_money(maximum * 1.10),
        prompt_fingerprint=fingerprint,
        created_at=datetime.now(timezone.utc).isoformat(),
        counted_by_provider=counted_by_provider,
    )


def quote_anthropic(feature: str, system: str, messages: list[dict], *,
                    model: str = HAIKU, max_tokens: int = 512,
                    client=None) -> AIQuote:
    """Use Anthropic's free token counter. No paid generation occurs here."""
    api = client or anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    counted = api.messages.count_tokens(model=model, system=system, messages=messages)
    count = int(getattr(counted, "input_tokens", 0) or 0)
    if count <= 0:
        raise RuntimeError("Anthropic did not return an input token count.")
    fingerprint = prompt_fingerprint(system, messages, model, max_tokens)
    return quote_from_count(feature, model, count, max_tokens, fingerprint)


def quote_estimate(feature: str, system: str, messages: list[dict], *, model: str,
                   max_tokens: int, provider: str) -> AIQuote:
    """Conservative local estimate for providers without a free counter."""
    count = max(1, (len(_payload(system, messages, model, max_tokens)) + 2) // 3)
    return quote_from_count(feature, model, count, max_tokens,
                            prompt_fingerprint(system, messages, model, max_tokens),
                            provider=provider, counted_by_provider=False)


def _settings() -> dict:
    try:
        data = json.loads(SETTINGS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    return {"daily_limit_usd": max(0.0, float(data.get("daily_limit_usd", DEFAULT_DAILY_LIMIT_USD)))}


def daily_limit() -> float:
    return _settings()["daily_limit_usd"]


def set_daily_limit(value: float) -> float:
    value = round(max(0.0, float(value)), 2)
    LOCAL.mkdir(parents=True, exist_ok=True)
    tmp = SETTINGS.with_suffix(".tmp")
    tmp.write_text(json.dumps({"daily_limit_usd": value}, indent=2), encoding="utf-8")
    tmp.replace(SETTINGS)
    return value


def usage_rows() -> list[dict]:
    try:
        rows = []
        for line in LEDGER.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except ValueError:
                continue
        return rows
    except OSError:
        return []


def usage_summary(now: datetime | None = None) -> dict:
    now = now or datetime.now().astimezone()
    day = now.date().isoformat()
    month = day[:7]
    rows = usage_rows()
    today_rows = [r for r in rows if str(r.get("timestamp", ""))[:10] == day and r.get("status") == "success"]
    month_rows = [r for r in rows if str(r.get("timestamp", ""))[:7] == month and r.get("status") == "success"]
    today = sum(float(r.get("cost_usd", 0)) for r in today_rows)
    month_total = sum(float(r.get("cost_usd", 0)) for r in month_rows)
    by_feature = {}
    for row in month_rows:
        key = row.get("feature", "unknown")
        by_feature[key] = by_feature.get(key, 0.0) + float(row.get("cost_usd", 0))
    limit = daily_limit()
    return {"today_usd": today, "month_usd": month_total, "daily_limit_usd": limit,
            "remaining_usd": max(0.0, limit - today), "by_feature": by_feature,
            "recent": list(reversed(rows[-10:]))}


def _validate_quote(quote: AIQuote | dict, feature: str, system: str,
                    messages: list[dict], model: str, max_tokens: int) -> AIQuote:
    q = AIQuote.from_value(quote)
    expected = prompt_fingerprint(system, messages, model, max_tokens)
    if q.feature != feature or q.model != model or q.max_output_tokens != max_tokens:
        raise AIApprovalError("This price approval belongs to a different AI action. Check the price again.")
    if q.prompt_fingerprint != expected:
        raise AIApprovalError("The prompt or context changed after pricing. Check the price again.")
    created = datetime.fromisoformat(q.created_at)
    age = (datetime.now(timezone.utc) - created.astimezone(timezone.utc)).total_seconds()
    if age > QUOTE_TTL_SECONDS:
        raise AIApprovalError("This price quote expired. Check the price again.")
    summary = usage_summary()
    if q.hard_ceiling_usd > summary["remaining_usd"] + 1e-9:
        raise AIBudgetError(
            f"Blocked by the daily AI limit. This action can cost up to ${q.hard_ceiling_usd:.4f}, "
            f"but ${summary['remaining_usd']:.4f} remains today."
        )
    return q


def record_usage(quote: AIQuote | dict, *, input_tokens: int = 0,
                 output_tokens: int = 0, status: str = "success",
                 error: str = "") -> float:
    q = AIQuote.from_value(quote)
    in_rate, out_rate = _rates(q.model, input_tokens)
    cost = (input_tokens * in_rate + output_tokens * out_rate) / 1_000_000 if status == "success" else 0.0
    row = {"timestamp": datetime.now().astimezone().isoformat(timespec="seconds"),
           "quote_id": q.quote_id, "feature": q.feature, "provider": q.provider,
           "model": q.model, "input_tokens": int(input_tokens),
           "output_tokens": int(output_tokens), "cost_usd": _money(cost),
           "status": status, "error": (error or "")[:240]}
    with _lock:
        LOCAL.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    return cost


def usage_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Actual dollars for a finished call, using the same table as quotes."""
    in_rate, out_rate = _rates(model, input_tokens)
    return _money((input_tokens * in_rate + output_tokens * out_rate) / 1_000_000)


def response_cost(response, model: str) -> float:
    usage = getattr(response, "usage", None)
    return usage_cost(model, int(getattr(usage, "input_tokens", 0) or 0),
                      int(getattr(usage, "output_tokens", 0) or 0))


def response_text(response) -> str:
    """First text block. Thinking blocks can come before it on newer models."""
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", "text") == "text":
            return (getattr(block, "text", "") or "").strip()
    return ""


def quote_request(request: dict, client=None) -> AIQuote:
    """Price a prepared request dict (feature/system/messages/model/max_tokens)."""
    return quote_anthropic(request["feature"], request["system"], request["messages"],
                           model=request["model"], max_tokens=request["max_tokens"],
                           client=client)


def run_request(quote: AIQuote | dict, request: dict, client=None):
    """Run a prepared request with the quote that priced it."""
    return run_anthropic(quote, request["feature"], request["system"], request["messages"],
                         model=request["model"], max_tokens=request["max_tokens"],
                         client=client)


def run_anthropic(quote: AIQuote | dict, feature: str, system: str,
                  messages: list[dict], *, model: str = HAIKU,
                  max_tokens: int = 512, client=None, **kwargs):
    """Run exactly one paid call after validating its reviewed quote and cap."""
    if quote is None:
        raise AIApprovalError(f"No price approval for {feature}. Check the price first.")
    if model == HAIKU:
        # Haiku 5.5 thinks by default; thinking tokens would eat the small
        # max_tokens budgets these features use and push past the quoted output.
        kwargs.setdefault("thinking", {"type": "disabled"})
    with _lock:
        q = _validate_quote(quote, feature, system, messages, model, max_tokens)
    api = client or anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))
    try:
        response = api.messages.create(model=model, max_tokens=max_tokens,
                                       system=system, messages=messages, **kwargs)
    except Exception as exc:
        record_usage(q, status="failed", error=str(exc))
        raise
    usage = getattr(response, "usage", None)
    record_usage(q, input_tokens=int(getattr(usage, "input_tokens", 0) or 0),
                 output_tokens=int(getattr(usage, "output_tokens", 0) or 0))
    return response


def quote_lines(quote: AIQuote | dict) -> list[str]:
    q = AIQuote.from_value(quote)
    source = "provider count" if q.counted_by_provider else "conservative local estimate"
    return [
        f"Model: {q.model}",
        f"Input: {q.input_tokens:,} tokens ({source}) = ${q.input_cost_usd:.6f}",
        f"Expected output: {q.expected_output_tokens:,} tokens = ${q.expected_output_cost_usd:.6f}",
        f"Maximum output: {q.max_output_tokens:,} tokens = ${q.max_output_cost_usd:.6f}",
        f"Expected total: ${q.expected_total_usd:.6f}",
        f"Hard ceiling with 10% buffer: ${q.hard_ceiling_usd:.6f}",
    ]

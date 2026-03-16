"""
shared-guardrails — Three-layer guardrail system for the stock agent.

Teaching note:
  Guardrails answer the question: "What can go wrong with an LLM agent?"

  Layer 1 — INPUT:  Is the user's request valid and safe?
  Layer 2 — TOOL:   Are the tool arguments valid and within policy?
  Layer 3 — OUTPUT: Is the final answer safe to show the user?

  Every guardrail decision is logged and stored in the JobRecord so
  students can see exactly what was blocked and why.
  This transparency is crucial for building trust in AI systems.
"""
from __future__ import annotations

import re
import time
import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class GuardrailResult:
    """
    Result of a guardrail check.

    allowed: bool          — True = proceed, False = block
    modified_input: str    — Sanitized version (if MODIFY decision)
    reason: str            — Why it was blocked/modified (shown to students)
    check_name: str        — Which specific check triggered this
    """

    def __init__(
        self,
        allowed: bool,
        reason: str = "",
        check_name: str = "",
        modified_input: Optional[str] = None,
    ):
        self.allowed = allowed
        self.reason = reason
        self.check_name = check_name
        self.modified_input = modified_input
        self.decision = "ALLOW" if allowed else "BLOCK"
        if modified_input is not None and allowed:
            self.decision = "MODIFY"

    def __bool__(self):
        return self.allowed

    def __repr__(self):
        return f"GuardrailResult({self.decision}, check={self.check_name}, reason={self.reason!r})"


_INJECTION_PATTERNS = [
    r"ignore\s+(previous|all|prior)\s+(instructions|rules|prompts)",
    r"ignore\s+all\s+(previous|prior|above)\s+instructions",
    r"disregard\s+(your|all|previous)\s+",
    r"you\s+are\s+now\s+(a|an)\s+",
    r"new\s+instructions?\s*:",
    r"system\s*:\s*",
    r"forget\s+(everything|all)",
    r"override\s+(your|the)\s+(rules?|instructions?)",
    r"act\s+as\s+(if\s+you\s+are|a)\s+",
    r"pretend\s+(you\s+are|to\s+be)\s+",
    r"jailbreak",
    r"DAN\s+mode",
    r"developer\s+mode",
    r"<\s*script",
    r"eval\s*\(",
    r"exec\s*\(",
]
_INJECTION_REGEX = re.compile(
    "|".join(_INJECTION_PATTERNS), re.IGNORECASE
)

_STOCK_KEYWORDS = re.compile(
    r"\b(stock|share|ticker|symbol|price|market|portfolio|watchlist|"
    r"invest|financial|equity|sector|index|etf|fund|dividend|earnings|"
    r"revenue|pe\s*ratio|valuation|analysis|trend|momentum|technical|"
    r"fundamental|nasdaq|nyse|s&p|dow|bull|bear|volatility)\b",
    re.IGNORECASE
)

_TICKER_PATTERN = re.compile(r"\b[A-Z]{1,5}\b")


class InputGuardrails:
    """
    Layer 1: Validates and sanitizes user input BEFORE the crew runs.

    Teaching note:
      This is the cheapest layer — checks happen in milliseconds
      with no LLM call. Block bad input early, before spending tokens.
    """

    def __init__(
        self,
        max_length: int = 2000,
        injection_detection: bool = True,
        require_stock_intent: bool = False,
    ):
        self.max_length = max_length
        self.injection_detection = injection_detection
        self.require_stock_intent = require_stock_intent

    def check(self, query: str) -> List[GuardrailResult]:
        """
        Run all input checks. Returns a list of results — one per check.
        If ANY result has allowed=False, the request should be blocked.
        """
        results = []

        results.append(self._check_length(query))

        results.append(self._check_not_empty(query))

        if self.injection_detection:
            results.append(self._check_injection(query))

        if self.require_stock_intent:
            results.append(self._check_stock_intent(query))

        return results

    def is_allowed(self, query: str) -> Tuple[bool, List[GuardrailResult]]:
        """Convenience method: returns (allowed, results)."""
        results = self.check(query)
        return all(r.allowed for r in results), results

    def _check_length(self, query: str) -> GuardrailResult:
        if len(query) > self.max_length:
            return GuardrailResult(
                allowed=False,
                check_name="max_length",
                reason=f"Input too long: {len(query)} chars (max {self.max_length})"
            )
        return GuardrailResult(allowed=True, check_name="max_length")

    def _check_not_empty(self, query: str) -> GuardrailResult:
        if not query or len(query.strip()) < 3:
            return GuardrailResult(
                allowed=False,
                check_name="min_length",
                reason="Input is empty or too short (minimum 3 characters)"
            )
        return GuardrailResult(allowed=True, check_name="min_length")

    def _check_injection(self, query: str) -> GuardrailResult:
        match = _INJECTION_REGEX.search(query)
        if match:
            return GuardrailResult(
                allowed=False,
                check_name="prompt_injection",
                reason=f"Prompt injection pattern detected: '{match.group()}'",
            )
        return GuardrailResult(allowed=True, check_name="prompt_injection")

    def _check_stock_intent(self, query: str) -> GuardrailResult:
        has_stock_keyword = bool(_STOCK_KEYWORDS.search(query))
        has_ticker = bool(_TICKER_PATTERN.search(query))
        if not (has_stock_keyword or has_ticker):
            return GuardrailResult(
                allowed=False,
                check_name="stock_intent",
                reason=(
                    "Query doesn't appear to be about stocks or financial markets. "
                    "This assistant is specialised for stock analysis."
                )
            )
        return GuardrailResult(allowed=True, check_name="stock_intent")


ALLOWED_TOOLS = {
    "search_symbols",
    "latest_quote",
    "price_series",
    "indicators",
    "detect_events",
    "explain",
}

DEFAULT_MAX_TOOL_CALLS = 30

TOOLS_REQUIRING_SYMBOL = {
    "latest_quote", "price_series", "indicators", "detect_events", "explain"
}


class ToolGuardrails:
    """
    Layer 2: Validates tool calls BEFORE they are executed.

    Teaching note:
      This layer runs inside the agent-runtime, wrapping every MCP tool call.
      It answers: "Should this agent be allowed to call this tool with these args?"

      Common problems it prevents:
      - Agent hallucinating a non-existent tool name
      - Agent passing empty or malformed arguments
      - Agent calling too many tools (cost runaway)
      - Agent trying to call tools outside its permitted set
    """

    def __init__(
        self,
        allowed_tools: Optional[set] = None,
        max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
    ):
        self.allowed_tools = allowed_tools or ALLOWED_TOOLS
        self.max_tool_calls = max_tool_calls
        self._call_count = 0

    def check_tool_call(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
    ) -> GuardrailResult:
        """Check a tool call before execution."""

        self._call_count += 1
        if self._call_count > self.max_tool_calls:
            return GuardrailResult(
                allowed=False,
                check_name="max_tool_calls",
                reason=(
                    f"Too many tool calls ({self._call_count}). "
                    f"Max allowed: {self.max_tool_calls}. "
                    "This prevents cost runaway from looping agents."
                )
            )

        if tool_name not in self.allowed_tools:
            return GuardrailResult(
                allowed=False,
                check_name="tool_allowlist",
                reason=f"Tool '{tool_name}' is not in the allowed tool set"
            )

        if tool_name in TOOLS_REQUIRING_SYMBOL:
            symbol = arguments.get("symbol", "").strip()
            result = self._validate_symbol(symbol, tool_name)
            if not result.allowed:
                return result

        for key, value in arguments.items():
            if isinstance(value, str) and _INJECTION_REGEX.search(value):
                return GuardrailResult(
                    allowed=False,
                    check_name="tool_argument_injection",
                    reason=f"Injection pattern detected in argument '{key}'"
                )

        return GuardrailResult(allowed=True, check_name="tool_call_validation")

    def _validate_symbol(self, symbol: str, tool_name: str) -> GuardrailResult:
        if not symbol:
            return GuardrailResult(
                allowed=False,
                check_name="symbol_required",
                reason=f"Tool '{tool_name}' requires a non-empty 'symbol' argument"
            )
        if not re.match(r"^[A-Z0-9\.\-\^]{1,10}$", symbol.upper()):
            return GuardrailResult(
                allowed=False,
                check_name="symbol_format",
                reason=(
                    f"Invalid symbol format: '{symbol}'. "
                    "Expected 1-10 alphanumeric characters (e.g., AAPL, NVDA)"
                )
            )
        return GuardrailResult(allowed=True, check_name="symbol_format")

    def reset_counter(self):
        """Reset tool call counter (call at start of each job)."""
        self._call_count = 0

    @property
    def call_count(self) -> int:
        return self._call_count


_PRICE_TARGET_PATTERNS = [
    r"(will|going to)\s+(reach|hit|go to|be at|rise to)\s+\$\d",
    r"price target\s+of\s+\$\d",
    r"(expect|predict|forecast)\s+(the\s+)?price\s+to",
    r"guaranteed\s+(return|profit|gain)",
    r"(will|must)\s+(go\s+up|increase|rise|surge|soar)",
    r"100%\s+(sure|certain|guaranteed)",
]
_PREDICTION_REGEX = re.compile(
    "|".join(_PRICE_TARGET_PATTERNS), re.IGNORECASE
)

FINANCIAL_DISCLAIMER = (
    "\n\n---\n"
    "⚠️ **Disclaimer**: This analysis is for educational and informational "
    "purposes only. It does not constitute financial advice, investment "
    "recommendations, or solicitation to buy or sell any securities. "
    "Always consult a qualified financial advisor before making investment decisions."
)

_LEAK_PATTERNS = re.compile(
    r"(api_key|secret|password|token|bearer)\s*[=:]\s*\S+",
    re.IGNORECASE
)


class OutputGuardrails:
    """
    Layer 3: Validates and cleans the final output BEFORE sending to the user.

    Teaching note:
      Output guardrails are the last safety net.
      By the time we get here, the crew has finished — we can't stop
      the LLM from generating bad content, but we CAN intercept it.

      Common interventions:
      - Redact accidentally leaked secrets from logs
      - Flag hallucinated price predictions (LLMs often fabricate these)
      - Inject required financial disclaimers
      - Flag low-confidence outputs for human review
    """

    def __init__(
        self,
        add_disclaimer: bool = True,
        flag_predictions: bool = True,
        redact_secrets: bool = True,
    ):
        self.add_disclaimer = add_disclaimer
        self.flag_predictions = flag_predictions
        self.redact_secrets = redact_secrets

    def check(self, output: str) -> Tuple[str, List[GuardrailResult]]:
        """
        Check and potentially modify the output.
        Returns (modified_output, list of guardrail results).
        """
        results = []
        text = output

        if self.redact_secrets:
            cleaned, redact_result = self._redact_secrets(text)
            text = cleaned
            results.append(redact_result)

        if self.flag_predictions:
            prediction_result = self._check_predictions(text)
            results.append(prediction_result)

        if self.add_disclaimer:
            text = self._add_disclaimer(text)
            results.append(
                GuardrailResult(
                    allowed=True,
                    check_name="disclaimer_added",
                    reason="Standard financial disclaimer appended",
                    modified_input=text,
                )
            )

        return text, results

    def _redact_secrets(self, text: str) -> Tuple[str, GuardrailResult]:
        cleaned = _LEAK_PATTERNS.sub(r"\1=[REDACTED]", text)
        if cleaned != text:
            return cleaned, GuardrailResult(
                allowed=True,
                check_name="secret_redaction",
                reason="Potential secret/API key pattern redacted from output",
                modified_input="[redacted output]",
            )
        return text, GuardrailResult(allowed=True, check_name="secret_redaction")

    def _check_predictions(self, text: str) -> GuardrailResult:
        match = _PREDICTION_REGEX.search(text)
        if match:
            return GuardrailResult(
                allowed=True,
                check_name="prediction_flag",
                reason=(
                    f"Output contains potential price prediction: '{match.group()[:80]}'. "
                    "Disclaimer has been added."
                ),
            )
        return GuardrailResult(allowed=True, check_name="prediction_flag")

    def _add_disclaimer(self, text: str) -> str:
        if FINANCIAL_DISCLAIMER.strip() not in text:
            return text + FINANCIAL_DISCLAIMER
        return text


class GuardrailPipeline:
    """
    Runs all three layers in sequence.

    Usage in agent-runtime:
        pipeline = GuardrailPipeline(settings)

        # Before crew runs:
        allowed, events = pipeline.check_input(request.query)
        if not allowed:
            return blocked_response(events)

        # After crew runs:
        safe_output, events = pipeline.check_output(crew_result)
        return safe_output
    """

    def __init__(
        self,
        max_input_length: int = 2000,
        max_tool_calls: int = 30,
        injection_detection: bool = True,
    ):
        self.input_layer = InputGuardrails(
            max_length=max_input_length,
            injection_detection=injection_detection,
        )
        self.tool_layer = ToolGuardrails(max_tool_calls=max_tool_calls)
        self.output_layer = OutputGuardrails()

    def check_input(self, query: str) -> Tuple[bool, List[GuardrailResult]]:
        return self.input_layer.is_allowed(query)

    def check_tool_call(
        self, tool_name: str, arguments: Dict[str, Any]
    ) -> GuardrailResult:
        return self.tool_layer.check_tool_call(tool_name, arguments)

    def check_output(self, output: str) -> Tuple[str, List[GuardrailResult]]:
        return self.output_layer.check(output)

    def reset(self):
        """Reset for a new job (clears tool call counter)."""
        self.tool_layer.reset_counter()

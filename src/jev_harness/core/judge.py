"""Judge adapters: a model answers a question and reports how sure it is.

Rules the rest of the package relies on:

* **Fail safe.** A timeout, network error, HTTP error, malformed body, or an
  answer outside the options the caller supplied all produce a *Decision* that
  is marked ``escalated``. ``decide`` does not raise for a remote failure and
  never invents an answer, so callers can treat "the judge is unavailable" as
  "escalate" without special-casing.
* **Confidence must exist.** A decision without a usable confidence is
  unusable, so it escalates. The Jev adapter asks a ``choice`` question
  precisely because that is the only question type Jev returns a confidence
  for (docs.typesafe.ai/api).
* **Secrets stay out of output.** The API key is used in a header and never
  echoed into ``Decision.raw``, messages, or reports.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable, Mapping
from typing import Any, Protocol

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr

from jev_harness.core.config import JudgeBackend, Settings
from jev_harness.core.errors import ConfigError, HarnessError

#: Statuses worth retrying. 529 comes from TypeSafe ("overloaded"); the same set
#: (minus 529, which is theirs) also appears in the author's jev-build-sentinel.
RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})

DEFAULT_JEV_ENDPOINT_SUFFIX = "systemone"
DEFAULT_ANTHROPIC_MODEL = "claude-sonnet-5"
DEFAULT_OPENAI_MODEL = "gpt-5"
BACKOFF_BASE_SECONDS = 0.25
BACKOFF_CAP_SECONDS = 4.0


class JudgeError(HarnessError):
    """Base class for judge problems."""


class JudgeUnavailableError(JudgeError):
    """The selected judge needs an extra or a credential that is not present."""


class JudgeResponseError(JudgeError):
    """The judge answered, but not in a shape we can trust."""


class Decision(BaseModel):
    """One judge answer with its stated confidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    judge: str
    answer: str = ""
    confidence: float = Field(ge=0.0, le=1.0)
    #: The probability the judge gave its chosen option, when it reports a distribution (Jev
    #: does). ``confidence`` is a *different* statistic: for Jev, how far the top probability sits
    #: above an even split, so a two-way answer of 0.9 / 0.1 has probability 0.9 and confidence 0.8.
    probability: float | None = Field(default=None, ge=0.0, le=1.0)
    latency_ms: int = Field(ge=0)
    escalated: bool = False
    reason: str | None = None
    raw: dict[str, Any] = Field(default_factory=dict)

    def answers(self, *options: str) -> bool:
        """Whether the judge chose one of these options."""
        return self.answer in options


class JudgeQuestion(BaseModel):
    """A choice question: the judge must pick one of ``options``."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    question: str = Field(min_length=1)
    #: option -> rubric description. Order is preserved in the prompt.
    options: dict[str, str] = Field(min_length=2)
    #: Optional structured state (Jev's ``state``); defaults to the question text.
    state: Any = None

    def option_names(self) -> list[str]:
        return list(self.options)


class Judge(Protocol):
    """What every judge provides."""

    name: str

    async def decide(self, question: JudgeQuestion) -> Decision:
        """Answer a question, or report why it could not."""


def escalated_decision(judge: str, reason: str, *, latency_ms: int = 0) -> Decision:
    """The only failure response this module produces."""
    return Decision(
        judge=judge,
        answer="",
        confidence=0.0,
        latency_ms=latency_ms,
        escalated=True,
        reason=reason,
    )


class NullJudge:
    """Used when no judge is configured: it never answers, so it always escalates."""

    name = "none"

    async def decide(self, question: JudgeQuestion) -> Decision:
        return escalated_decision(
            self.name,
            "no judge is configured, so model-assisted findings are unavailable "
            "(pass --judge jev|anthropic|openai to enable one)",
        )


def _validate_choice(question: JudgeQuestion, answer: str) -> str:
    if answer not in question.options:
        raise JudgeResponseError(
            f"judge answered {answer!r}, which is not one of the options it was given: "
            f"{sorted(question.options)}"
        )
    return answer


def _validate_confidence(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise JudgeResponseError(f"judge confidence must be a number, got {value!r}")
    confidence = float(value)
    if not 0.0 <= confidence <= 1.0:
        raise JudgeResponseError(f"judge confidence must be within [0, 1], got {confidence}")
    return confidence


def build_choice_prompt(question: JudgeQuestion) -> str:
    """A prompt that forces one of the caller's options plus a confidence."""
    options = "\n".join(
        f"- {name}: {description}" for name, description in question.options.items()
    )
    return (
        f"{question.question}\n\n"
        f"Options:\n{options}\n\n"
        "Answer with strict JSON only, no prose:\n"
        '{"answer": "<one of the option names>", "confidence": <number between 0 and 1>}\n'
        "The confidence is how sure you are of that answer; use a low value rather than "
        "guessing between options."
    )


def parse_model_json(text: str, question: JudgeQuestion, *, judge: str) -> tuple[str, float]:
    """Extract and validate ``{"answer": ..., "confidence": ...}`` from model text."""
    candidate = (text or "").strip()
    if candidate.startswith("```"):
        candidate = candidate.strip("`")
        if candidate.lower().startswith("json"):
            candidate = candidate[4:]
        candidate = candidate.strip()
    start = candidate.find("{")
    end = candidate.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise JudgeResponseError(f"judge did not return a JSON object: {_clip(text)}")

    try:
        payload = json.loads(candidate[start : end + 1])
    except json.JSONDecodeError as exc:
        raise JudgeResponseError(f"judge returned invalid JSON: {_clip(text)}") from exc
    if not isinstance(payload, dict):
        raise JudgeResponseError(f"judge returned {type(payload).__name__}, expected an object")

    answer = payload.get("answer")
    if not isinstance(answer, str):
        raise JudgeResponseError("judge response has no string `answer`")
    return _validate_choice(question, answer), _validate_confidence(payload.get("confidence"))


def _clip(text: str, limit: int = 200) -> str:
    flattened = " ".join((text or "").split())
    return flattened[:limit] + ("..." if len(flattened) > limit else "")


# --------------------------------------------------------------------- Jev


class JevJudge:
    """TypeSafe's System One endpoint.

    Its ``confidence`` is derived from the answer's probabilities as
    ``(p_max - 1/n) / (1 - 1/n)``; it is 0 for an even split and 1 when all the probability is on
    one option. TypeSafe does not claim it is calibrated against accuracy and advises choosing
    thresholds from your own data, which is what ``calibrate`` is for.
    """

    name = "jev"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        base_url: str,
        model: str = "jev-latest",
        timeout_seconds: float = 10.0,
        retries: int = 2,
        client: httpx.AsyncClient | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        self.api_key = api_key
        self.model = model
        base = base_url.rstrip("/")
        self.url = (
            base
            if base.endswith(f"/{DEFAULT_JEV_ENDPOINT_SUFFIX}")
            else f"{base}/{DEFAULT_JEV_ENDPOINT_SUFFIX}"
        )
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self._client = client
        self._sleep = sleep if sleep is not None else asyncio.sleep

    def build_payload(self, question: JudgeQuestion) -> dict[str, Any]:
        """The request body; asserted by tests because the API validates it."""
        return {
            "state": question.state if question.state is not None else question.question,
            "model": self.model,
            "questions": {
                "decision": {
                    "type": "choice",
                    "instructions": question.question,
                    "criteria": dict(question.options),
                }
            },
        }

    async def decide(self, question: JudgeQuestion) -> Decision:
        started = time.monotonic()
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(timeout=self.timeout_seconds)
        try:
            raw = await self._request(client, self.build_payload(question))
            answer, confidence = _parse_jev_choice(raw)
            return Decision(
                judge=self.name,
                answer=_validate_choice(question, answer),
                confidence=_validate_confidence(confidence),
                probability=_chosen_probability(raw, answer),
                latency_ms=_elapsed_ms(started),
                raw=_redact(raw),
            )
        except (httpx.HTTPError, JudgeError) as exc:
            return escalated_decision(
                self.name, f"{type(exc).__name__}: {exc}", latency_ms=_elapsed_ms(started)
            )
        finally:
            if owns_client:
                await client.aclose()

    async def _request(self, client: httpx.AsyncClient, payload: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "authorization": f"Bearer {self.api_key.get_secret_value()}",
            "content-type": "application/json",
            "accept": "application/json",
        }
        last_error: Exception | None = None
        for attempt in range(self.retries + 1):
            if attempt > 0:
                delay = min(BACKOFF_BASE_SECONDS * 2 ** (attempt - 1), BACKOFF_CAP_SECONDS)
                await self._sleep(delay)
            try:
                response = await client.post(self.url, json=payload, headers=headers)
            except httpx.HTTPError as exc:
                last_error = exc
                continue
            if response.status_code in RETRYABLE_STATUSES:
                last_error = JudgeResponseError(
                    f"Jev returned HTTP {response.status_code} for {self.url}"
                )
                continue
            if response.status_code >= 400:
                # 401 and 422 are not worth retrying: the request or key is wrong.
                raise JudgeResponseError(
                    f"Jev returned HTTP {response.status_code} for {self.url}: "
                    f"{_clip(response.text)}"
                )
            return _parse_jev_body(response)
        raise JudgeResponseError(
            f"Jev request failed after {self.retries + 1} attempt(s): {last_error}"
        )


def _parse_jev_body(response: httpx.Response) -> dict[str, Any]:
    try:
        payload = response.json()
    except ValueError as exc:
        raise JudgeResponseError(f"Jev returned a non-JSON body: {_clip(response.text)}") from exc
    if not isinstance(payload, dict):
        raise JudgeResponseError(f"Jev returned {type(payload).__name__}, expected an object")
    return payload


def _parse_jev_choice(payload: Mapping[str, Any]) -> tuple[str, float]:
    answers = payload.get("answers")
    if not isinstance(answers, dict):
        raise JudgeResponseError("Jev response has no `answers` object")
    answer = answers.get("decision")
    if not isinstance(answer, dict):
        raise JudgeResponseError("Jev response has no `answers.decision`")
    answer_type = answer.get("type")
    if answer_type not in (None, "choice"):
        raise JudgeResponseError(f"Jev answered with type {answer_type!r}, expected 'choice'")
    choice = answer.get("choice")
    if not isinstance(choice, str):
        raise JudgeResponseError("Jev choice answer has no string `choice`")
    if "confidence" not in answer:
        raise JudgeResponseError(
            "Jev choice answer has no `confidence`; without it the answer cannot be "
            "used safely, so it is treated as a failure"
        )
    return choice, _validate_confidence(answer.get("confidence"))


def _chosen_probability(payload: Mapping[str, Any], choice: str) -> float | None:
    """The probability Jev gave the option it chose, or ``None`` if it did not say."""
    answers = payload.get("answers")
    answer = answers.get("decision") if isinstance(answers, dict) else None
    probabilities = answer.get("probabilities") if isinstance(answer, dict) else None
    value = probabilities.get(choice) if isinstance(probabilities, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value) if 0.0 <= float(value) <= 1.0 else None


def _redact(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Keep the response for evidence, but never anything key-shaped."""
    sensitive = {"api_key", "apikey", "authorization", "key", "token", "secret"}
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if str(key).lower() in sensitive:
            redacted[str(key)] = "<redacted>"
        elif isinstance(value, dict):
            redacted[str(key)] = _redact(value)
        else:
            redacted[str(key)] = value
    return redacted


def _elapsed_ms(started: float) -> int:
    return max(0, int((time.monotonic() - started) * 1000))


# ------------------------------------------------------- hosted chat models


class _ChatModelJudge:
    """Shared behaviour for SDK-backed judges (Anthropic, OpenAI)."""

    name = "chat"
    extra = ""
    default_model = ""

    def __init__(
        self,
        *,
        api_key: SecretStr,
        model: str | None = None,
        timeout_seconds: float = 30.0,
        max_tokens: int = 512,
        client: Any = None,
    ) -> None:
        self.api_key = api_key
        self.model = model or self.default_model
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self._client = client

    def _build_client(self) -> Any:
        raise NotImplementedError

    async def _complete(self, client: Any, prompt: str) -> str:
        raise NotImplementedError

    async def decide(self, question: JudgeQuestion) -> Decision:
        started = time.monotonic()
        try:
            client = self._client if self._client is not None else self._build_client()
            text = await self._complete(client, build_choice_prompt(question))
            answer, confidence = parse_model_json(text, question, judge=self.name)
            return Decision(
                judge=self.name,
                answer=answer,
                confidence=confidence,
                latency_ms=_elapsed_ms(started),
            )
        except JudgeUnavailableError:
            raise
        except (JudgeError, ValueError) as exc:
            return escalated_decision(
                self.name, f"{type(exc).__name__}: {exc}", latency_ms=_elapsed_ms(started)
            )
        except Exception as exc:  # provider SDKs raise their own types
            return escalated_decision(
                self.name,
                f"judge call failed ({type(exc).__name__}): {_clip(str(exc), 120)}",
                latency_ms=_elapsed_ms(started),
            )


class AnthropicJudge(_ChatModelJudge):
    """Claude via the Anthropic SDK (extra: ``[anthropic]``)."""

    name = "anthropic"
    extra = "anthropic"
    default_model = DEFAULT_ANTHROPIC_MODEL

    def _build_client(self) -> Any:
        try:
            from anthropic import AsyncAnthropic
        except ImportError as exc:
            raise JudgeUnavailableError(
                "the Anthropic judge needs the optional extra: "
                "uv pip install 'jev-harness[anthropic]'"
            ) from exc
        return AsyncAnthropic(api_key=self.api_key.get_secret_value(), timeout=self.timeout_seconds)

    async def _complete(self, client: Any, prompt: str) -> str:
        message = await client.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            messages=[{"role": "user", "content": prompt}],
        )
        blocks = getattr(message, "content", None)
        if not isinstance(blocks, list) or not blocks:
            raise JudgeResponseError("Anthropic returned no content blocks")
        first = blocks[0]
        text = getattr(first, "text", None) if not isinstance(first, dict) else first.get("text")
        if not isinstance(text, str):
            raise JudgeResponseError("Anthropic returned a block without text")
        return text


class OpenAIJudge(_ChatModelJudge):
    """A chat model via the OpenAI SDK (extra: ``[openai]``)."""

    name = "openai"
    extra = "openai"
    default_model = DEFAULT_OPENAI_MODEL

    def _build_client(self) -> Any:
        try:
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise JudgeUnavailableError(
                "the OpenAI judge needs the optional extra: uv pip install 'jev-harness[openai]'"
            ) from exc
        return AsyncOpenAI(api_key=self.api_key.get_secret_value(), timeout=self.timeout_seconds)

    async def _complete(self, client: Any, prompt: str) -> str:
        completion = await client.chat.completions.create(
            model=self.model,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            max_tokens=self.max_tokens,
        )
        choices = getattr(completion, "choices", None)
        if not isinstance(choices, list) or not choices:
            raise JudgeResponseError("OpenAI returned no choices")
        message = getattr(choices[0], "message", None)
        text = getattr(message, "content", None)
        if not isinstance(text, str):
            raise JudgeResponseError("OpenAI returned no message content")
        return text


def get_judge(settings: Settings, *, client: Any = None, sleep: Any = None) -> Judge:
    """Build the judge named by the settings, or raise a clear configuration error."""
    match settings.judge:
        case JudgeBackend.none:
            return NullJudge()
        case JudgeBackend.jev:
            key = settings.jev_api_key
            if key is None or not key.get_secret_value():
                raise ConfigError(
                    "--judge jev needs a key: set JEV_API_KEY (or TYPESAFE_API_KEY) in the "
                    "environment, or pass --env-file PATH to a file that sets it. Never paste "
                    "keys into a report or a commit."
                )
            return JevJudge(
                api_key=key,
                base_url=settings.jev_api_url,
                model=settings.jev_model,
                timeout_seconds=settings.judge_timeout_seconds,
                retries=settings.judge_retries,
                client=client,
                sleep=sleep,
            )
        case JudgeBackend.anthropic:
            key = settings.anthropic_api_key
            if key is None or not key.get_secret_value():
                raise ConfigError("--judge anthropic needs ANTHROPIC_API_KEY in the environment")
            return AnthropicJudge(api_key=key, client=client)
        case JudgeBackend.openai:
            key = settings.openai_api_key
            if key is None or not key.get_secret_value():
                raise ConfigError("--judge openai needs OPENAI_API_KEY in the environment")
            return OpenAIJudge(api_key=key, client=client)
    raise ConfigError(f"unknown judge backend: {settings.judge}")  # pragma: no cover - exhaustive

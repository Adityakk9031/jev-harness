"""Judge adapters: every remote failure must land on ``escalated``, never a pass.

These tests are offline by construction (respx for HTTP, injected fakes for the
provider SDKs), so the whole file runs in the default non-live suite.
"""

from __future__ import annotations

import sys
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
import respx
from pydantic import SecretStr

from jev_harness.core.config import JudgeBackend, Settings
from jev_harness.core.errors import ConfigError
from jev_harness.core.judge import (
    AnthropicJudge,
    Decision,
    JevJudge,
    JudgeQuestion,
    JudgeResponseError,
    JudgeUnavailableError,
    NullJudge,
    OpenAIJudge,
    build_choice_prompt,
    escalated_decision,
    get_judge,
    parse_model_json,
)

QUESTION = JudgeQuestion(
    question="Does this payload match the deny rule?",
    options={"block": "it matches the deny rule", "allow": "it does not match"},
)

JEV_URL = "https://api.typesafe.ai/v1/systemone"


def jev_payload(choice: str, confidence: float) -> dict[str, Any]:
    return {"answers": {"decision": {"type": "choice", "choice": choice, "confidence": confidence}}}


def jev_judge(**kwargs: Any) -> JevJudge:
    delays: list[float] = []

    async def record_sleep(seconds: float) -> None:
        delays.append(seconds)

    judge = JevJudge(
        api_key=SecretStr("sk-test-not-a-real-key"),
        base_url="https://api.typesafe.ai/v1",
        sleep=record_sleep,
        **kwargs,
    )
    judge.test_delays = delays  # type: ignore[attr-defined]
    return judge


# --------------------------------------------------------------- NullJudge


async def test_null_judge_always_escalates() -> None:
    decision = await NullJudge().decide(QUESTION)
    assert decision.escalated is True
    assert decision.answer == ""
    assert decision.confidence == 0.0
    assert "no judge is configured" in (decision.reason or "")


def test_escalated_decision_shape() -> None:
    decision = escalated_decision("jev", "timeout", latency_ms=12)
    assert decision.escalated is True
    assert decision.judge == "jev"
    assert decision.reason == "timeout"
    assert Decision.model_validate_json(decision.model_dump_json()) == decision


def test_decision_answers_helpers() -> None:
    decision = escalated_decision("jev", "x")
    assert not decision.answers("block", "allow")
    assert Decision(judge="jev", answer="block", confidence=0.8, latency_ms=1).answers("block")


# ------------------------------------------------------- prompt and parsing


def test_build_choice_prompt_lists_every_option_and_forces_json() -> None:
    prompt = build_choice_prompt(QUESTION)
    assert QUESTION.question in prompt
    assert "- block: it matches the deny rule" in prompt
    assert "- allow: it does not match" in prompt
    assert "strict JSON only" in prompt
    assert "confidence" in prompt


@pytest.mark.parametrize(
    "text",
    [
        '{"answer": "block", "confidence": 0.9}',
        '```json\n{"answer": "block", "confidence": 0.9}\n```',
        'Sure, here you go: {"answer": "allow", "confidence": 0.5} — hope that helps',
    ],
)
def test_parse_model_json_accepts_real_world_shapes(text: str) -> None:
    answer, confidence = parse_model_json(text, QUESTION, judge="openai")
    assert answer in QUESTION.options
    assert 0.0 <= confidence <= 1.0


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("no json here", "JSON object"),
        ("{not json}", "invalid JSON"),
        ('{"answer": ["block"], "confidence": 0.9}', "no string `answer`"),
        ('{"confidence": 0.9}', "no string `answer`"),
        ('{"answer": "maybe", "confidence": 0.9}', "not one of the options"),
        ('{"answer": "block", "confidence": 1.5}', "confidence must be within"),
        ('{"answer": "block"}', "confidence"),
        ('{"answer": true, "confidence": 0.9}', "no string `answer`"),
    ],
)
def test_parse_model_json_rejects_everything_unusable(text: str, match: str) -> None:
    with pytest.raises(JudgeResponseError, match=match):
        parse_model_json(text, QUESTION, judge="openai")


# ------------------------------------------------------------------ JevJudge


def test_jev_judge_builds_the_documented_endpoint() -> None:
    # The documented endpoint is https://api.typesafe.ai/v1/systemone.
    assert jev_judge().url == JEV_URL
    assert JevJudge(api_key=SecretStr("k"), base_url=JEV_URL).url == JEV_URL
    assert (
        JevJudge(api_key=SecretStr("k"), base_url="https://x/v1/").url == "https://x/v1/systemone"
    )


def test_jev_payload_is_one_choice_question_with_confidence() -> None:
    payload = jev_judge().build_payload(QUESTION)
    assert payload["model"] == "jev-latest"
    assert payload["state"] == QUESTION.question
    decision = payload["questions"]["decision"]
    assert decision["type"] == "choice"
    assert decision["instructions"] == QUESTION.question
    assert decision["criteria"] == QUESTION.options


@respx.mock
async def test_jev_judge_success_returns_answer_and_confidence() -> None:
    respx.post(JEV_URL).mock(return_value=httpx.Response(200, json=jev_payload("block", 0.92)))
    decision = await jev_judge(retries=0).decide(QUESTION)

    assert decision.escalated is False
    assert decision.judge == "jev"
    assert decision.answer == "block"
    assert decision.confidence == pytest.approx(0.92)
    assert decision.latency_ms >= 0
    assert decision.raw["answers"]["decision"]["choice"] == "block"

    request = respx.calls.last.request
    assert request.headers["authorization"] == "Bearer sk-test-not-a-real-key"
    assert "sk-test-not-a-real-key" not in decision.model_dump_json()


@respx.mock
async def test_jev_judge_retries_rate_limits_with_backoff() -> None:
    respx.post(JEV_URL).mock(
        side_effect=iter([httpx.Response(429), httpx.Response(200, json=jev_payload("allow", 0.8))])
    )
    judge = jev_judge(retries=2)
    decision = await judge.decide(QUESTION)

    assert decision.escalated is False
    assert decision.answer == "allow"
    assert len(respx.calls) == 2
    assert judge.test_delays == [0.25]  # one backoff between two attempts


@respx.mock
async def test_jev_judge_gives_up_after_retries_and_escalates() -> None:
    respx.post(JEV_URL).mock(
        side_effect=iter([httpx.Response(429)] * 3),
    )
    judge = jev_judge(retries=2)
    decision = await judge.decide(QUESTION)

    assert decision.escalated is True
    assert "3 attempt(s)" in (decision.reason or "")
    assert len(respx.calls) == 3
    assert judge.test_delays == [0.25, 0.5]  # exponential backoff, no real sleeping


@respx.mock
async def test_jev_judge_does_not_retry_a_bad_request() -> None:
    respx.post(JEV_URL).mock(return_value=httpx.Response(400, text="bad criteria"))
    decision = await jev_judge(retries=2).decide(QUESTION)

    assert decision.escalated is True
    assert "400" in (decision.reason or "")
    assert len(respx.calls) == 1


@respx.mock
async def test_jev_judge_escalates_on_network_error() -> None:
    respx.post(JEV_URL).mock(side_effect=iter([httpx.ConnectError("boom")] * 2))
    decision = await jev_judge(retries=1).decide(QUESTION)

    assert decision.escalated is True
    assert "2 attempt(s)" in (decision.reason or "")
    assert "boom" in (decision.reason or "")


@respx.mock
async def test_jev_judge_escalates_on_non_json_body() -> None:
    respx.post(JEV_URL).mock(return_value=httpx.Response(200, text="<html>gateway</html>"))
    decision = await jev_judge(retries=0).decide(QUESTION)
    assert decision.escalated is True
    assert "non-JSON" in (decision.reason or "")


@respx.mock
@pytest.mark.parametrize(
    ("body", "match"),
    [
        ({"nope": True}, "no `answers` object"),
        ({"answers": {"decision": {"type": "choice", "choice": "block"}}}, "confidence"),
        (
            {"answers": {"decision": {"type": "text", "choice": "block", "confidence": 0.5}}},
            "type",
        ),
        (
            {"answers": {"decision": {"choice": "maybe", "confidence": 0.5}}},
            "not one of the options",
        ),
        (
            {"answers": {"decision": {"choice": "block", "confidence": 3.0}}},
            "confidence must be within",
        ),
    ],
)
async def test_jev_judge_escalates_on_unusable_answers(body: dict[str, Any], match: str) -> None:
    respx.post(JEV_URL).mock(return_value=httpx.Response(200, json=body))
    decision = await jev_judge(retries=0).decide(QUESTION)
    assert decision.escalated is True
    assert match in (decision.reason or "")


@respx.mock
async def test_jev_judge_redacts_key_shaped_fields_in_raw() -> None:
    body = jev_payload("block", 0.9)
    body["token"] = "sk-would-leak"
    respx.post(JEV_URL).mock(return_value=httpx.Response(200, json=body))
    decision = await jev_judge(retries=0).decide(QUESTION)

    assert decision.raw["token"] == "<redacted>"
    assert decision.raw["answers"]["decision"]["choice"] == "block"


# ------------------------------------------------------------------ get_judge


def test_get_judge_none_returns_the_null_judge() -> None:
    judge = get_judge(Settings())
    assert isinstance(judge, NullJudge)
    assert judge.name == "none"


@pytest.mark.parametrize("backend", [JudgeBackend.jev, JudgeBackend.anthropic, JudgeBackend.openai])
def test_get_judge_without_a_key_is_a_config_error(backend: JudgeBackend) -> None:
    with pytest.raises(ConfigError, match="--judge"):
        get_judge(Settings(judge=backend))


def test_get_judge_builds_the_jev_adapter_from_settings() -> None:
    settings = Settings(judge=JudgeBackend.jev, jev_api_key=SecretStr("sk-x"))
    judge = get_judge(settings)
    assert isinstance(judge, JevJudge)
    assert judge.model == settings.jev_model
    assert judge.retries == settings.judge_retries


def test_get_judge_builds_chat_adapters_without_importing_sdks() -> None:
    anthropic = get_judge(Settings(judge=JudgeBackend.anthropic, anthropic_api_key=SecretStr("a")))
    openai = get_judge(Settings(judge=JudgeBackend.openai, openai_api_key=SecretStr("o")))
    assert isinstance(anthropic, AnthropicJudge)
    assert isinstance(openai, OpenAIJudge)
    assert anthropic.extra == "anthropic"
    assert openai.extra == "openai"


# ------------------------------------------------------------ chat-model judges


def fake_anthropic(text: str) -> SimpleNamespace:
    class Messages:
        async def create(self, **kwargs: Any) -> Any:
            return SimpleNamespace(content=[SimpleNamespace(text=text)])

    return SimpleNamespace(messages=Messages())


def fake_openai(text: str) -> SimpleNamespace:
    class Completions:
        def __init__(self) -> None:
            self.kwargs: list[dict[str, Any]] = []

        async def create(self, **kwargs: Any) -> Any:
            self.kwargs.append(kwargs)
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])

    completions = Completions()
    return SimpleNamespace(chat=SimpleNamespace(completions=completions))


async def test_anthropic_judge_parses_a_text_completion() -> None:
    judge = AnthropicJudge(
        api_key=SecretStr("a"),
        client=fake_anthropic('{"answer": "block", "confidence": 0.75}'),
    )
    decision = await judge.decide(QUESTION)
    assert decision.escalated is False
    assert decision.judge == "anthropic"
    assert decision.answer == "block"
    assert decision.confidence == pytest.approx(0.75)


async def test_anthropic_judge_escalates_on_prose_without_json() -> None:
    judge = AnthropicJudge(api_key=SecretStr("a"), client=fake_anthropic("looks fine to me"))
    decision = await judge.decide(QUESTION)
    assert decision.escalated is True


async def test_anthropic_judge_escalates_when_the_provider_raises() -> None:
    class BrokenMessages:
        async def create(self, **kwargs: Any) -> Any:
            raise RuntimeError("provider exploded")

    judge = AnthropicJudge(
        api_key=SecretStr("a"), client=SimpleNamespace(messages=BrokenMessages())
    )
    decision = await judge.decide(QUESTION)
    assert decision.escalated is True
    assert "provider exploded" in (decision.reason or "")


async def test_missing_sdk_raises_rather_than_faking_a_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)  # forces ImportError on import
    judge = AnthropicJudge(api_key=SecretStr("a"))
    with pytest.raises(JudgeUnavailableError, match=r"\[anthropic\]"):
        await judge.decide(QUESTION)


async def test_openai_judge_requests_json_and_parses_the_completion() -> None:
    client = fake_openai('{"answer": "allow", "confidence": 0.6}')
    judge = OpenAIJudge(api_key=SecretStr("o"), client=client)
    decision = await judge.decide(QUESTION)

    assert decision.escalated is False
    assert decision.answer == "allow"
    sent = client.chat.completions.kwargs[0]
    assert sent["response_format"] == {"type": "json_object"}
    assert "block" in sent["messages"][0]["content"]


async def test_openai_judge_escalates_on_an_empty_choice() -> None:
    class EmptyCompletions:
        async def create(self, **kwargs: Any) -> Any:
            return SimpleNamespace(choices=[])

    judge = OpenAIJudge(
        api_key=SecretStr("o"),
        client=SimpleNamespace(chat=SimpleNamespace(completions=EmptyCompletions())),
    )
    decision = await judge.decide(QUESTION)
    assert decision.escalated is True
    assert "no choices" in (decision.reason or "")


@respx.mock
async def test_jev_probability_of_the_chosen_option_is_kept_apart_from_its_confidence() -> None:
    """Jev's confidence is (p_max - 1/n) / (1 - 1/n), not the probability of being right."""
    payload = {
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "skip",
                "confidence": 0.8,
                "probabilities": {"skip": 0.9, "full": 0.1},
            }
        }
    }
    respx.post(JEV_URL).mock(return_value=httpx.Response(200, json=payload))
    judge = jev_judge()
    decision = await judge.decide(JudgeQuestion(question="q", options={"skip": "s", "full": "f"}))
    assert (decision.answer, decision.confidence, decision.probability) == ("skip", 0.8, 0.9)


@respx.mock
async def test_a_tie_comes_back_with_zero_confidence_and_half_probability() -> None:
    """Observed live: an even split returns a choice anyway, with confidence 0.0."""
    payload = {
        "answers": {
            "decision": {
                "type": "choice",
                "choice": "full",
                "confidence": 0.0,
                "probabilities": {"skip": 0.5, "full": 0.5},
            }
        }
    }
    respx.post(JEV_URL).mock(return_value=httpx.Response(200, json=payload))
    judge = jev_judge()
    decision = await judge.decide(JudgeQuestion(question="q", options={"skip": "s", "full": "f"}))
    assert decision.probability == 0.5
    assert decision.confidence == 0.0


@respx.mock
async def test_missing_or_malformed_probabilities_leave_probability_unset() -> None:
    for probabilities in (None, {"skip": "high"}, {"other": 0.9}, {"skip": 1.5}):
        answer = {"type": "choice", "choice": "skip", "confidence": 0.9}
        if probabilities is not None:
            answer["probabilities"] = probabilities
        respx.post(JEV_URL).mock(
            return_value=httpx.Response(200, json={"answers": {"decision": answer}})
        )
        judge = jev_judge()
        decision = await judge.decide(
            JudgeQuestion(question="q", options={"skip": "s", "full": "f"})
        )
        assert decision.probability is None
        assert decision.confidence == 0.9

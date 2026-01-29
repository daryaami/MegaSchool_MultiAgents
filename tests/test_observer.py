import asyncio

from src.agents.observer import Observer
from src.llm import LLMClient
from src.policy import Policy


class FakeLLM(LLMClient):
    async def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        return (
            "{\"action\":\"same\",\"scores\":{\"correctness\":0.5,\"confidence\":0.5},"
            "\"notes\":\"ok\",\"status\":\"confirmed\",\"correct_answer\":\"\","
            "\"hallucination\":true,\"hallucination_reason\":\"тест\"}"
        )


def test_observer_flags_hallucination() -> None:
    async def _run() -> None:
        inbox: asyncio.Queue = asyncio.Queue()
        policy = Policy(
            {
                "role_reversal_reply": "ok",
                "action_reasons": {"increase": "up", "decrease": "down", "same": "same"},
            }
        )
        config = {
            "analysis_system_prompt": "system",
            "analysis_json_prompt_template": "Вопрос: {question}\nОтвет: {answer}",
            "analysis_fallback_note": "fallback {error}",
            "internal_notes": {"hallucination": "hallucination: {reason}", "off_topic": "off"},
            "internal_thoughts_prefix": "[Observer]: ",
            "default_topic": "General",
            "llm_timeout_seconds": 1,
            "llm_timeout_note": "",
            "llm_error_note": "",
            "observer_error_note": "",
        }
        observer = Observer(inbox, FakeLLM(), policy, config)
        reply_queue: asyncio.Queue = asyncio.Queue()
        await observer.handle(
            {
                "type": "analyze",
                "user_reply": "I heard Python 4 will remove for loops.",
                "last_question": "Explain Python evolution.",
                "reply_queue": reply_queue,
            }
        )
        result = await reply_queue.get()
        assert result["flags"]["hallucination_suspect"] is True

    asyncio.run(_run())

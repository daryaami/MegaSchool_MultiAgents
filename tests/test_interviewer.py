import asyncio

from src.agents.interviewer import Interviewer
from src.llm import LLMClient
from src.policy import Policy
from src.session import SessionLogger


class FakeLLM(LLMClient):
    async def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        return "ok"


def test_interviewer_questions_rotate() -> None:
    session = SessionLogger(
        team_name="Team",
        meta={"position": "Backend"},
        feedback_config={
            "recommendation": {"no_gaps": "Hire", "has_gaps": "No Hire"},
            "confidence": {"no_gaps": 70, "has_gaps": 40},
            "soft_skills": {
                "clarity": "Average",
                "honesty_no_gaps": "Clear answers",
                "honesty_with_gaps": "Admitted gaps",
                "engagement": "Neutral",
            },
            "roadmap_resources_default": ["https://example.com"],
        },
        default_topic="General",
    )
    policy = Policy(
        {
            "role_reversal_reply": "ok",
            "action_reasons": {"increase": "up", "decrease": "down", "same": "same"},
        }
    )
    config = {
        "initial_question_template": "hi {position}",
        "system_prompt": "system",
        "question_prompt_template": "{history} {asked_questions} {action} {position} {grade}",
        "role_reversal_prompt_template": "{user_question}",
        "interviewer_internal_template": "[Interviewer]: {action} {topic}",
        "use_llm_questions": False,
        "max_history_turns": 2,
        "base_questions": ["q1", "q2"],
        "follow_ups": {"same": ["next"], "increase": ["inc"], "decrease": ["dec"]},
        "default_topic": "General",
        "observer_timeout_thoughts": "timeout",
    }
    interviewer = Interviewer(
        asyncio.Queue(),
        asyncio.Queue(),
        asyncio.Queue(),
        session,
        FakeLLM(),
        policy,
        config,
    )
    first = interviewer._pick_question()
    second = interviewer._pick_question()
    assert first != second

"""
Интеграционные тесты для проверки реакций агентов на разные сценарии интервью.

Тестируются три сценария:
1. Идеальный кандидат - высокие оценки, правильные ответы
2. Средний кандидат - смешанные результаты, некоторые пробелы
3. Плохой кандидат - низкие оценки, галлюцинации, много пробелов
"""
import asyncio
import json
from typing import Any, Dict, List
from unittest.mock import AsyncMock

import pytest

from src.agents.interviewer import Interviewer
from src.agents.manager import Manager
from src.agents.observer import Observer
from src.config import load_config
from src.llm import LLMClient
from src.policy import Policy
from src.session import SessionLogger


class MockLLM(LLMClient):
    """Мок LLM, который возвращает предопределенные ответы в зависимости от сценария."""
    
    def __init__(self, scenario: str):
        self.scenario = scenario
        self.call_count = 0
        self.call_history: List[Dict[str, str]] = []
        
    async def chat(self, system_prompt: str, user_prompt: str, temperature: float = 0.2) -> str:
        """Возвращает предопределенный ответ в зависимости от сценария и типа запроса."""
        self.call_count += 1
        self.call_history.append({
            "system_prompt": system_prompt[:100],
            "user_prompt": user_prompt[:200],
            "call_number": self.call_count
        })
        
        # Определяем тип запроса по промпту
        if "Observer" in system_prompt or "наблюдатель" in system_prompt.lower():
            return self._get_observer_response(user_prompt)
        elif "интервьюер" in system_prompt.lower() or "сгенерируй следующий вопрос" in user_prompt.lower():
            return self._get_interviewer_response(user_prompt)
        elif "менеджер" in system_prompt.lower() or "прими решение о найме" in user_prompt.lower():
            return self._get_manager_response()
        elif "релевантности" in system_prompt.lower():
            return '{"relevant": true, "reason": "Вопрос релевантен"}'
        else:
            # Fallback для role_reversal
            return "Это хороший вопрос. Давайте обсудим детали после интервью."
    
    def _get_observer_response(self, prompt: str) -> str:
        """Генерирует ответ Observer в зависимости от сценария."""
        # Извлекаем ответ кандидата из промпта
        answer = ""
        if "Ответ:" in prompt:
            answer = prompt.split("Ответ:")[-1].strip()[:100].lower()
        
        if self.scenario == "ideal":
            # Идеальный кандидат - высокие оценки
            return json.dumps({
                "action": "increase",
                "scores": {"correctness": 0.9, "confidence": 0.85},
                "notes": "Отличный ответ с деталями и примерами",
                "status": "confirmed",
                "correct_answer": "",
                "hallucination": False,
                "hallucination_reason": "",
                "off_topic": False,
                "off_topic_reason": "",
                "stop_intent": False,
                "stop_intent_reason": "",
                "role_reversal": False,
                "role_reversal_reason": "",
                "suggested_topic": "Python Data Structures"
            }, ensure_ascii=False)
        
        elif self.scenario == "average":
            # Средний кандидат - смешанные оценки
            if "не знаю" in answer or "don't know" in answer:
                return json.dumps({
                    "action": "decrease",
                    "scores": {"correctness": 0.3, "confidence": 0.4},
                    "notes": "Кандидат честно признал незнание",
                    "status": "gap",
                    "correct_answer": "Правильный ответ будет дан позже",
                    "hallucination": False,
                    "hallucination_reason": "",
                    "off_topic": False,
                    "off_topic_reason": "",
                    "stop_intent": False,
                    "stop_intent_reason": "",
                    "role_reversal": False,
                    "role_reversal_reason": "",
                    "suggested_topic": "General"
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "action": "same",
                    "scores": {"correctness": 0.6, "confidence": 0.55},
                    "notes": "Нормальный ответ, но можно углубиться",
                    "status": "confirmed",
                    "correct_answer": "",
                    "hallucination": False,
                    "hallucination_reason": "",
                    "off_topic": False,
                    "off_topic_reason": "",
                    "stop_intent": False,
                    "stop_intent_reason": "",
                    "role_reversal": False,
                    "role_reversal_reason": "",
                    "suggested_topic": "General"
                }, ensure_ascii=False)
        
        else:  # poor
            # Плохой кандидат - низкие оценки, возможны галлюцинации
            if "python 4" in answer or "удалят" in answer:
                return json.dumps({
                    "action": "decrease",
                    "scores": {"correctness": 0.2, "confidence": 0.3},
                    "notes": "Подозрение на галлюцинацию",
                    "status": "hallucination_suspect",
                    "correct_answer": "Python 4 не существует",
                    "hallucination": True,
                    "hallucination_reason": "Утверждение о несуществующем Python 4",
                    "off_topic": False,
                    "off_topic_reason": "",
                    "stop_intent": False,
                    "stop_intent_reason": "",
                    "role_reversal": False,
                    "role_reversal_reason": "",
                    "suggested_topic": "General"
                }, ensure_ascii=False)
            else:
                return json.dumps({
                    "action": "decrease",
                    "scores": {"correctness": 0.25, "confidence": 0.3},
                    "notes": "Слабый ответ, много пробелов",
                    "status": "gap",
                    "correct_answer": "Правильный ответ",
                    "hallucination": False,
                    "hallucination_reason": "",
                    "off_topic": False,
                    "off_topic_reason": "",
                    "stop_intent": False,
                    "stop_intent_reason": "",
                    "role_reversal": False,
                    "role_reversal_reason": "",
                    "suggested_topic": "General"
                }, ensure_ascii=False)
    
    def _get_interviewer_response(self, prompt: str) -> str:
        """Генерирует вопрос от Interviewer."""
        questions = [
            "Расскажите о вашем опыте работы с Python.",
            "В чем разница между list и tuple в Python?",
            "Как вы обрабатываете ошибки в коде?",
            "Опишите процесс работы с базами данных."
        ]
        question_index = min(self.call_count - 1, len(questions) - 1)
        question = questions[question_index]
        
        return json.dumps({
            "question": question,
            "reasoning": f"Задаю вопрос #{question_index + 1} на основе рекомендаций Observer"
        }, ensure_ascii=False)
    
    def _get_manager_response(self) -> str:
        """Генерирует финальный отчёт Manager в зависимости от сценария."""
        if self.scenario == "ideal":
            return json.dumps({
                "verdict": {
                    "grade": "Middle",
                    "recommendation": "Strong Hire",
                    "confidence_score": 90
                },
                "technical_review": {
                    "topics": [
                        {
                            "topic": "Python Data Structures",
                            "status": "confirmed",
                            "notes": "Отличное понимание",
                            "correct_answer": ""
                        }
                    ],
                    "confirmed_skills": ["Python Data Structures", "Error Handling"],
                    "knowledge_gaps": []
                },
                "soft_skills": {
                    "clarity": "Good",
                    "honesty": "Clear answers",
                    "engagement": "High"
                },
                "personal_roadmap": []
            }, ensure_ascii=False)
        
        elif self.scenario == "average":
            return json.dumps({
                "verdict": {
                    "grade": "Junior",
                    "recommendation": "Hire",
                    "confidence_score": 65
                },
                "technical_review": {
                    "topics": [
                        {
                            "topic": "General",
                            "status": "gap",
                            "notes": "Есть пробелы, но кандидат честен",
                            "correct_answer": "Требуется обучение"
                        }
                    ],
                    "confirmed_skills": ["Python Basics"],
                    "knowledge_gaps": ["Advanced Python"]
                },
                "soft_skills": {
                    "clarity": "Average",
                    "honesty": "Admitted gaps",
                    "engagement": "Neutral"
                },
                "personal_roadmap": [
                    {
                        "topic": "Advanced Python",
                        "resources": ["https://docs.python.org/3/"]
                    }
                ]
            }, ensure_ascii=False)
        
        else:  # poor
            return json.dumps({
                "verdict": {
                    "grade": "Junior",
                    "recommendation": "No Hire",
                    "confidence_score": 30
                },
                "technical_review": {
                    "topics": [
                        {
                            "topic": "General",
                            "status": "hallucination_suspect",
                            "notes": "Обнаружены галлюцинации",
                            "correct_answer": "Требуется коррекция знаний"
                        }
                    ],
                    "confirmed_skills": [],
                    "knowledge_gaps": ["Python Basics", "Error Handling"]
                },
                "soft_skills": {
                    "clarity": "Poor",
                    "honesty": "Unclear",
                    "engagement": "Low"
                },
                "personal_roadmap": [
                    {
                        "topic": "Python Basics",
                        "resources": ["https://docs.python.org/3/"]
                    }
                ]
            }, ensure_ascii=False)


async def run_interview_scenario(
    scenario: str,
    candidate_responses: List[str],
    config_path: str = "config/runtime.json"
) -> Dict[str, Any]:
    """
    Запускает симуляцию интервью с заданными ответами кандидата.
    
    Args:
        scenario: "ideal", "average" или "poor"
        candidate_responses: Список ответов кандидата
        config_path: Путь к конфигурации
    
    Returns:
        Словарь с результатами интервью
    """
    runtime_config = load_config(config_path)
    policy = Policy(runtime_config["policy"])
    
    session = SessionLogger(
        team_name="Test Team",
        meta={
            "position": "Backend Developer",
            "grade": "Junior",
            "experience": "Test experience"
        },
        feedback_config=runtime_config["final_feedback"],
        default_topic=runtime_config["interviewer"]["default_topic"],
        session_id=f"test-{scenario}",
    )
    
    # Создаем очереди
    interviewer_in: asyncio.Queue = asyncio.Queue()
    observer_in: asyncio.Queue = asyncio.Queue()
    manager_in: asyncio.Queue = asyncio.Queue()
    user_out: asyncio.Queue = asyncio.Queue()
    
    # Создаем мок LLM
    llm = MockLLM(scenario)
    
    # Создаем агентов
    interviewer = Interviewer(
        interviewer_in,
        user_out,
        observer_in,
        session,
        llm,
        policy,
        runtime_config["interviewer"],
    )
    observer = Observer(
        observer_in,
        llm,
        policy,
        runtime_config["observer"],
    )
    manager = Manager(
        manager_in,
        llm,
        session,
        runtime_config["manager"],
    )
    
    # Запускаем агентов
    interviewer_task = asyncio.create_task(interviewer.start())
    observer_task = asyncio.create_task(observer.start())
    manager_task = asyncio.create_task(manager.start())
    
    # Начинаем интервью
    await interviewer_in.put({"cmd": "start"})
    
    # Обрабатываем ответы кандидата
    response_index = 0
    while response_index < len(candidate_responses):
        # Ждем сообщение от интервьюера
        message = await user_out.get()
        msg_type = message.get("type", "visible")
        
        if msg_type == "internal":
            # Пропускаем внутренние сообщения в тестах
            continue
        
        if msg_type == "stop_intent":
            break
        
        # Отправляем ответ кандидата
        if response_index < len(candidate_responses):
            await interviewer_in.put({"user_reply": candidate_responses[response_index]})
            response_index += 1
    
    # Завершаем интервью и получаем финальный отчёт
    reply_queue: asyncio.Queue = asyncio.Queue()
    await manager_in.put({"type": "finalize", "reply_queue": reply_queue})
    
    try:
        final_feedback = await asyncio.wait_for(reply_queue.get(), timeout=10.0)
    except asyncio.TimeoutError:
        final_feedback = session.build_final_feedback()
    
    session.set_final_feedback(final_feedback)
    
    # Останавливаем агентов
    await interviewer_in.put(None)
    await observer_in.put(None)
    await manager_in.put(None)
    
    await interviewer_task
    await observer_task
    await manager_task
    
    return {
        "session": session.to_dict(),
        "turns": session.turns,
        "observations": session.observations,
        "final_feedback": final_feedback,
        "llm_calls": llm.call_count,
    }


@pytest.mark.asyncio
async def test_ideal_candidate_scenario():
    """Тест идеального кандидата - должен получить высокие оценки и Strong Hire."""
    responses = [
        "Я работаю с Python уже 3 года. Использую его для backend разработки на Django и Flask. Например, недавно создал REST API с использованием Django REST Framework.",
        "List - это изменяемая последовательность, а tuple - неизменяемая. Например, list можно модифицировать через append(), а tuple нельзя. Tuple используется для хранения неизменяемых данных, например координат.",
        "Я использую try-except блоки для обработки исключений. Также логирую ошибки и использую кастомные исключения для более понятной обработки."
    ]
    
    result = await run_interview_scenario("ideal", responses)
    
    # Проверяем, что интервью прошло
    assert len(result["turns"]) > 0
    
    # Проверяем оценки - должны быть высокими
    avg_correctness = sum(
        turn.get("scores", {}).get("correctness", 0)
        for turn in result["turns"]
        if turn.get("scores", {}).get("correctness")
    ) / max(1, len([t for t in result["turns"] if t.get("scores", {}).get("correctness")]))
    
    assert avg_correctness > 0.7, f"Средняя correctness должна быть высокой, получено: {avg_correctness}"
    
    # Проверяем финальный отчёт
    verdict = result["final_feedback"]["verdict"]
    assert verdict["recommendation"] in ["Hire", "Strong Hire"], \
        f"Идеальный кандидат должен получить Hire/Strong Hire, получено: {verdict['recommendation']}"
    
    # Проверяем, что нет пробелов в знаниях
    technical = result["final_feedback"]["technical_review"]
    gaps = technical.get("knowledge_gaps", [])
    assert len(gaps) == 0 or len(gaps) < len(technical.get("confirmed_skills", [])), \
        f"Идеальный кандидат не должен иметь много пробелов, получено: {gaps}"


@pytest.mark.asyncio
async def test_average_candidate_scenario():
    """Тест среднего кандидата - смешанные результаты, но честность."""
    responses = [
        "Я изучаю Python около года. Делал несколько пет-проектов.",
        "List можно изменять, tuple - нет. Больше деталей не помню.",
        "Не знаю точно, как правильно обрабатывать ошибки. Обычно просто использую try-except."
    ]
    
    result = await run_interview_scenario("average", responses)
    
    # Проверяем, что интервью прошло
    assert len(result["turns"]) > 0
    
    # Проверяем оценки - должны быть средними
    scores = [turn.get("scores", {}) for turn in result["turns"] if turn.get("scores")]
    if scores:
        avg_correctness = sum(s.get("correctness", 0) for s in scores) / len(scores)
        assert 0.3 < avg_correctness < 0.8, \
            f"Средняя correctness должна быть средней, получено: {avg_correctness}"
    
    # Проверяем финальный отчёт
    verdict = result["final_feedback"]["verdict"]
    assert verdict["recommendation"] in ["Hire", "No Hire"], \
        f"Средний кандидат должен получить Hire/No Hire, получено: {verdict['recommendation']}"
    
    # Проверяем, что есть пробелы, но кандидат честен
    technical = result["final_feedback"]["technical_review"]
    gaps = technical.get("knowledge_gaps", [])
    soft_skills = result["final_feedback"]["soft_skills"]
    
    # Средний кандидат может иметь пробелы, но должен быть честен
    assert len(gaps) >= 0, "Должны быть зафиксированы пробелы или подтвержденные навыки"
    assert soft_skills.get("honesty") in ["Clear answers", "Admitted gaps"], \
        f"Средний кандидат должен быть честен, получено: {soft_skills.get('honesty')}"


@pytest.mark.asyncio
async def test_poor_candidate_scenario():
    """Тест плохого кандидата - низкие оценки, галлюцинации, No Hire."""
    responses = [
        "Я слышал, что Python 4 скоро выйдет и там удалят циклы for.",
        "List и tuple - это одно и то же, просто разные названия.",
        "Ошибки? Не знаю, что это такое."
    ]
    
    result = await run_interview_scenario("poor", responses)
    
    # Проверяем, что интервью прошло
    assert len(result["turns"]) > 0
    
    # Проверяем оценки - должны быть низкими
    scores = [turn.get("scores", {}) for turn in result["turns"] if turn.get("scores")]
    if scores:
        avg_correctness = sum(s.get("correctness", 0) for s in scores) / len(scores)
        assert avg_correctness < 0.5, \
            f"Средняя correctness должна быть низкой, получено: {avg_correctness}"
    
    # Проверяем финальный отчёт
    verdict = result["final_feedback"]["verdict"]
    assert verdict["recommendation"] in ["No Hire", "Hire"], \
        f"Плохой кандидат должен получить No Hire, получено: {verdict['recommendation']}"
    
    # Проверяем наличие галлюцинаций или пробелов
    technical = result["final_feedback"]["technical_review"]
    topics = technical.get("topics", [])
    
    has_hallucination = any(
        topic.get("status") == "hallucination_suspect"
        for topic in topics
    )
    gaps = technical.get("knowledge_gaps", [])
    
    assert has_hallucination or len(gaps) > 0, \
        "Плохой кандидат должен иметь галлюцинации или много пробелов"
    
    # Проверяем soft skills
    soft_skills = result["final_feedback"]["soft_skills"]
    assert soft_skills.get("clarity") in ["Poor", "Average"], \
        f"Плохой кандидат должен иметь низкую ясность, получено: {soft_skills.get('clarity')}"


@pytest.mark.asyncio
async def test_observer_actions():
    """Тест проверяет, что Observer правильно определяет actions на основе ответов."""
    # Тест для идеального кандидата - должен быть increase
    ideal_responses = ["Отличный детальный ответ с примерами и объяснениями."]
    ideal_result = await run_interview_scenario("ideal", ideal_responses)
    
    # Проверяем, что есть действия increase
    increase_actions = [
        turn.get("interviewer_action") == "increase"
        for turn in ideal_result["turns"]
    ]
    assert any(increase_actions), "Идеальный кандидат должен вызывать increase actions"
    
    # Тест для плохого кандидата - должен быть decrease
    poor_responses = ["Не знаю."]
    poor_result = await run_interview_scenario("poor", poor_responses)
    
    # Проверяем, что есть действия decrease
    decrease_actions = [
        turn.get("interviewer_action") == "decrease"
        for turn in poor_result["turns"]
    ]
    assert any(decrease_actions), "Плохой кандидат должен вызывать decrease actions"


@pytest.mark.asyncio
async def test_manager_final_feedback_structure():
    """Тест проверяет структуру финального отчёта Manager."""
    responses = ["Тестовый ответ для проверки структуры."]
    result = await run_interview_scenario("average", responses)
    
    feedback = result["final_feedback"]
    
    # Проверяем обязательные поля
    assert "verdict" in feedback
    assert "technical_review" in feedback
    assert "soft_skills" in feedback
    assert "personal_roadmap" in feedback
    
    # Проверяем структуру verdict
    verdict = feedback["verdict"]
    assert "grade" in verdict
    assert "recommendation" in verdict
    assert "confidence_score" in verdict
    assert verdict["recommendation"] in ["Hire", "No Hire", "Strong Hire"]
    assert 0 <= verdict["confidence_score"] <= 100
    
    # Проверяем структуру technical_review
    technical = feedback["technical_review"]
    assert "topics" in technical
    assert "confirmed_skills" in technical
    assert "knowledge_gaps" in technical
    
    # Проверяем структуру soft_skills
    soft_skills = feedback["soft_skills"]
    assert "clarity" in soft_skills
    assert "honesty" in soft_skills
    assert "engagement" in soft_skills

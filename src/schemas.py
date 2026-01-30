"""
Pydantic схемы для структурированного вывода LLM.
"""
from typing import Literal, List, Optional
from pydantic import BaseModel, Field, field_validator


class ScoresModel(BaseModel):
    """Модель для оценки ответа кандидата."""
    correctness: float = Field(..., ge=0.0, le=1.0, description="Правильность ответа (0.0-1.0)")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Уверенность кандидата (0.0-1.0)")


class ObserverAnalysis(BaseModel):
    """
    Структурированный ответ Observer для анализа ответа кандидата.
    
    Используется для валидации JSON-ответа от LLM в Observer.
    """
    action: Literal["increase", "same", "decrease"] = Field(
        ...,
        description="Действие для изменения сложности следующего вопроса"
    )
    scores: ScoresModel = Field(..., description="Оценки ответа кандидата")
    notes: str = Field(default="", description="Краткие заметки о анализе")
    status: Literal["confirmed", "gap"] = Field(
        default="confirmed",
        description="Статус ответа: confirmed (подтверждён) или gap (пробел в знаниях)"
    )
    correct_answer: str = Field(
        default="",
        description="Правильный ответ, если обнаружен пробел в знаниях"
    )
    hallucination: bool = Field(
        default=False,
        description="Подозрение на галлюцинацию (выдумывание фактов)"
    )
    hallucination_reason: str = Field(
        default="",
        description="Причина подозрения на галлюцинацию"
    )
    off_topic: bool = Field(
        default=False,
        description="Ответ не по теме вопроса"
    )
    off_topic_reason: str = Field(
        default="",
        description="Причина, почему ответ не по теме"
    )
    stop_intent: bool = Field(
        default=False,
        description="Кандидат хочет завершить интервью"
    )
    stop_intent_reason: str = Field(
        default="",
        description="Причина определения stop_intent"
    )
    role_reversal: bool = Field(
        default=False,
        description="Кандидат задаёт вопрос интервьюеру"
    )
    role_reversal_reason: str = Field(
        default="",
        description="Причина определения role_reversal"
    )
    suggested_topic: str = Field(
        default="",
        description="Предложенная тема для следующего вопроса"
    )

    @field_validator("action")
    @classmethod
    def validate_action(cls, v: str) -> str:
        """Валидация action."""
        if v not in {"increase", "same", "decrease"}:
            raise ValueError(f"action must be 'increase', 'same', or 'decrease', got '{v}'")
        return v

    @field_validator("status")
    @classmethod
    def validate_status(cls, v: str) -> str:
        """Валидация status."""
        if v not in {"confirmed", "gap"}:
            return "confirmed"  # Fallback к безопасному значению
        return v


class QuestionResponse(BaseModel):
    """
    Структурированный ответ Interviewer для генерации вопроса.
    
    Используется для валидации JSON-ответа от LLM в Interviewer.
    """
    question: str = Field(..., min_length=1, description="Сгенерированный вопрос для кандидата")
    reasoning: str = Field(
        default="",
        description="Внутренние рассуждения Interviewer о выборе вопроса"
    )
    comment: str = Field(
        default="",
        description="Необязательный комментарий перед вопросом: одобрение при хорошем ответе, подсказка при слабом, или пустая строка"
    )


class VerdictModel(BaseModel):
    """Модель для вердикта Manager."""
    grade: Literal["Junior", "Middle", "Senior"] = Field(..., description="Оценённый грейд кандидата")
    recommendation: Literal["Hire", "No Hire", "Strong Hire"] = Field(
        ...,
        description="Рекомендация по найму"
    )
    confidence_score: int = Field(..., ge=0, le=100, description="Уверенность в решении (0-100)")


class TopicReview(BaseModel):
    """Модель для обзора темы."""
    topic: str = Field(..., description="Название темы")
    status: Literal["confirmed", "gap", "hallucination_suspect"] = Field(
        ...,
        description="Статус знания темы"
    )
    notes: str = Field(default="", description="Заметки о теме")
    correct_answer: str = Field(default="", description="Правильный ответ, если есть пробел")


class TechnicalReview(BaseModel):
    """Модель для технического обзора."""
    topics: List[TopicReview] = Field(default_factory=list, description="Обзоры по темам")
    confirmed_skills: List[str] = Field(default_factory=list, description="Подтверждённые навыки")
    knowledge_gaps: List[str] = Field(default_factory=list, description="Пробелы в знаниях")


class SoftSkills(BaseModel):
    """Модель для оценки soft skills."""
    clarity: Literal["Good", "Average", "Poor"] = Field(..., description="Ясность ответов")
    honesty: Literal["Clear answers", "Admitted gaps", "Unclear"] = Field(
        ...,
        description="Честность кандидата"
    )
    engagement: Literal["High", "Neutral", "Low"] = Field(..., description="Вовлечённость")


class RoadmapItem(BaseModel):
    """Модель для элемента roadmap."""
    topic: str = Field(..., description="Тема для изучения")
    resources: List[str] = Field(default_factory=list, description="Ресурсы для изучения")


class FinalReport(BaseModel):
    """
    Структурированный ответ Manager для финального отчёта.
    
    Используется для валидации JSON-ответа от LLM в Manager.
    """
    verdict: VerdictModel = Field(..., description="Вердикт и рекомендация")
    technical_review: TechnicalReview = Field(..., description="Технический обзор")
    soft_skills: SoftSkills = Field(..., description="Оценка soft skills")
    personal_roadmap: List[RoadmapItem] = Field(
        default_factory=list,
        description="Персональный roadmap для кандидата"
    )

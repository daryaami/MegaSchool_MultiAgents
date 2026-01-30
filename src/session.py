import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def format_feedback_as_markdown(feedback: Dict[str, Any], meta: Optional[Dict[str, Any]] = None) -> str:
    """
    Преобразует структурированный final_feedback в Markdown документ.
    
    Args:
        feedback: Словарь с ключами verdict, technical_review, soft_skills, personal_roadmap
        meta: Опциональные метаданные (имя кандидата, позиция, грейд и т.д.)
        
    Returns:
        Строка в формате Markdown
    """
    lines = []
    
    # Заголовок
    lines.append("# Финальный отчёт по интервью\n")
    
    # Метаданные (если предоставлены)
    if meta:
        lines.append("## Информация о кандидате\n")
        if meta.get("name"):
            lines.append(f"- **Имя:** {meta.get('name')}")
        if meta.get("position"):
            lines.append(f"- **Позиция:** {meta.get('position')}")
        if meta.get("grade"):
            lines.append(f"- **Заявленный грейд:** {meta.get('grade')}")
        if meta.get("experience"):
            lines.append(f"- **Опыт:** {meta.get('experience')}")
        lines.append("")
    
    # Вердикт
    verdict = feedback.get("verdict", {})
    lines.append("## Вердикт\n")
    grade = verdict.get("grade", "N/A")
    recommendation = verdict.get("recommendation", "N/A")
    confidence = verdict.get("confidence_score", 0)
    
    lines.append(f"- **Грейд:** {grade}")
    lines.append(f"- **Рекомендация:** {recommendation}")
    lines.append(f"- **Уверенность:** {confidence}%\n")
    
    # Технический обзор
    technical = feedback.get("technical_review", {})
    lines.append("## Технический обзор\n")
    
    confirmed = technical.get("confirmed_skills", [])
    if confirmed:
        lines.append("### Подтверждённые навыки\n")
        for skill in confirmed:
            lines.append(f"- {skill}")
        lines.append("")
    
    gaps = technical.get("knowledge_gaps", [])
    if gaps:
        lines.append("### Пробелы в знаниях\n")
        for gap in gaps:
            lines.append(f"- {gap}")
        lines.append("")
    
    topics = technical.get("topics", [])
    if topics:
        lines.append("### Детали по темам\n")
        for topic in topics:
            topic_name = topic.get("topic", "N/A")
            status = topic.get("status", "unknown")
            notes = topic.get("notes", "")
            correct_answer = topic.get("correct_answer", "")
            
            status_text = {
                "confirmed": "Подтверждено",
                "gap": "Пробел в знаниях",
                "hallucination_suspect": "Подозрение на галлюцинацию",
            }.get(status, status)
            
            lines.append(f"#### {topic_name} ({status_text})")
            
            if notes:
                lines.append(f"\n{notes}\n")
            
            if correct_answer:
                lines.append(f"**Правильный ответ:** {correct_answer}\n")
    
    # Soft skills
    soft_skills = feedback.get("soft_skills", {})
    lines.append("## Soft Skills\n")
    lines.append(f"- **Ясность:** {soft_skills.get('clarity', 'N/A')}")
    lines.append(f"- **Честность:** {soft_skills.get('honesty', 'N/A')}")
    lines.append(f"- **Вовлечённость:** {soft_skills.get('engagement', 'N/A')}\n")
    
    # Персональный roadmap
    roadmap = feedback.get("personal_roadmap", [])
    if roadmap:
        lines.append("## Персональный Roadmap\n")
        for item in roadmap:
            topic = item.get("topic", "N/A")
            resources = item.get("resources", [])
            lines.append(f"### {topic}\n")
            if resources:
                for resource in resources:
                    lines.append(f"- {resource}")
            lines.append("")
    
    return "\n".join(lines)


@dataclass
class SessionLogger:
    team_name: str
    meta: Dict[str, Any]
    feedback_config: Dict[str, Any]
    default_topic: str
    session_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    turns: List[Dict[str, Any]] = field(default_factory=list)
    observations: List[Dict[str, Any]] = field(default_factory=list)
    history: List[Dict[str, Any]] = field(default_factory=list)
    final_feedback_override: Optional[Dict[str, Any]] = None

    def log_turn(
        self,
        agent_visible_message: str,
        user_message: str,
        internal_thoughts: str,
        interviewer_action: str,
        scores: Optional[Dict[str, Any]] = None,
    ) -> None:
        turn = {
            "turn_id": len(self.turns) + 1,
            "agent_visible_message": agent_visible_message,
            "user_message": user_message,
            "internal_thoughts": internal_thoughts,
        }
        self.turns.append(turn)

    def add_history(self, question: str, answer: str, facts: Optional[Dict[str, Any]] = None) -> None:
        self.history.append(
            {
                "timestamp": _now_iso(),
                "question": question,
                "answer": answer,
                "facts": facts or {},
            }
        )

    def add_observation(self, observation: Dict[str, Any]) -> None:
        self.observations.append(observation)

    def set_final_feedback(self, feedback: Dict[str, Any]) -> None:
        self.final_feedback_override = feedback

    def build_final_feedback(self) -> Dict[str, Any]:
        topics = []
        confirmed = []
        gaps = []
        for obs in self.observations:
            topic = obs.get("topic", self.default_topic)
            status = obs.get("status", "unknown")
            notes = obs.get("notes", "")
            correct_answer = obs.get("correct_answer", "")
            topic_entry = {"topic": topic, "status": status, "notes": notes}
            if correct_answer:
                topic_entry["correct_answer"] = correct_answer
            topics.append(topic_entry)
            if status == "confirmed":
                confirmed.append(topic)
            if status in ("gap", "hallucination_suspect"):
                gaps.append(topic)

        recommendation = (
            self.feedback_config["recommendation"]["no_gaps"]
            if not gaps
            else self.feedback_config["recommendation"]["has_gaps"]
        )
        confidence = (
            self.feedback_config["confidence"]["no_gaps"]
            if not gaps
            else self.feedback_config["confidence"]["has_gaps"]
        )
        soft_skills = self.feedback_config["soft_skills"]
        honesty = soft_skills["honesty_no_gaps"] if not gaps else soft_skills["honesty_with_gaps"]
        roadmap_default = self.feedback_config["roadmap_resources_default"]

        return {
            "verdict": {
                "grade": self.meta.get("grade", "Junior"),
                "recommendation": recommendation,
                "confidence_score": confidence,
            },
            "technical_review": {
                "topics": topics,
                "confirmed_skills": sorted(set(confirmed)),
                "knowledge_gaps": sorted(set(gaps)),
            },
            "soft_skills": {
                "clarity": soft_skills["clarity"],
                "honesty": honesty,
                "engagement": soft_skills["engagement"],
            },
            "personal_roadmap": [
                {"topic": gap, "resources": roadmap_default} for gap in gaps
            ],
        }

    def to_dict(self) -> Dict[str, Any]:
        final_feedback = self.final_feedback_override or self.build_final_feedback()
        # Преобразуем final_feedback в Markdown документ
        final_feedback_str = format_feedback_as_markdown(final_feedback, meta=self.meta)
        return {
            "participant_name": self.meta.get("name", ""),  # Имя кандидата из требований
            "session_id": self.session_id,
            "turns": self.turns,
            "final_feedback": final_feedback_str,
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

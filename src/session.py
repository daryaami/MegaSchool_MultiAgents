import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
            "timestamp": _now_iso(),
            "agent_visible_message": agent_visible_message,
            "user_message": user_message,
            "internal_thoughts": internal_thoughts,
            "interviewer_action": interviewer_action,
            "scores": scores or {},
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
        return {
            "team_name": self.team_name,
            "session_id": self.session_id,
            "meta": self.meta,
            "turns": self.turns,
            "final_feedback": final_feedback,
        }

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(self.to_dict(), handle, ensure_ascii=False, indent=2)

import asyncio
import json
from typing import Any, Dict

from src.llm import LLMClient
from src.session import SessionLogger

from .base import Agent


class Manager(Agent):
    def __init__(
        self,
        inbox: asyncio.Queue,
        llm: LLMClient,
        session: SessionLogger,
        config: Dict[str, object],
    ) -> None:
        super().__init__("Manager", inbox)
        self.llm = llm
        self.session = session
        self.config = config

    async def handle(self, msg: Dict[str, Any]) -> None:
        if msg.get("type") != "finalize":
            return
        reply_queue: asyncio.Queue = msg["reply_queue"]
        feedback = await self._generate_feedback()
        await reply_queue.put(feedback)

    async def _generate_feedback(self) -> Dict[str, Any]:
        turns_text = self._format_turns()
        observations_text = self._format_observations()
        stats_text = self._calculate_stats()
        prompt = self.config["report_prompt_template"].format(
            position=self.session.meta.get("position", "роль"),
            grade=self.session.meta.get("grade", "уровень"),
            experience=self.session.meta.get("experience", "N/A"),
            turns=turns_text,
            observations=observations_text,
            stats=stats_text,
        )
        timeout = float(self.config.get("llm_timeout_seconds", 25))
        try:
            response = await asyncio.wait_for(
                self.llm.chat(self.config["system_prompt"], prompt),
                timeout=timeout,
            )
        except (asyncio.TimeoutError, Exception) as exc:
            print(f"Manager LLM error: {exc}. Using fallback.")
            return self.session.build_final_feedback()

        try:
            parsed = self._parse_json_response(response)
            return parsed
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            print(f"Manager JSON parse error: {exc}. Using fallback.")
            return self.session.build_final_feedback()

    def _format_turns(self) -> str:
        max_turns = int(self.config.get("max_turns", 12))
        turns = self.session.turns[-max_turns:]
        lines = []
        for item in turns:
            lines.append(f"Q: {item.get('agent_visible_message', '')}")
            lines.append(f"A: {item.get('user_message', '')}")
        return "\n".join(lines) if lines else "Нет данных."

    def _format_observations(self) -> str:
        lines = []
        for item in self.session.observations:
            topic = item.get("topic", "General")
            status = item.get("status", "unknown")
            notes = item.get("notes", "")
            correct = item.get("correct_answer", "")
            scores = item.get("scores", {})
            correctness = scores.get("correctness", 0.0) if isinstance(scores, dict) else 0.0
            confidence = scores.get("confidence", 0.0) if isinstance(scores, dict) else 0.0
            line = f"- {topic} | {status} | correctness={correctness:.2f}, confidence={confidence:.2f} | {notes}"
            if correct:
                line += f" | Правильный ответ: {correct}"
            lines.append(line)
        return "\n".join(lines) if lines else "Нет данных."

    def _calculate_stats(self) -> str:
        confirmed = sum(1 for obs in self.session.observations if obs.get("status") == "confirmed")
        gaps = sum(1 for obs in self.session.observations if obs.get("status") == "gap")
        hallucinations = sum(1 for obs in self.session.observations if obs.get("status") == "hallucination_suspect")
        total = len(self.session.observations)
        avg_correctness = 0.0
        avg_confidence = 0.0
        if total > 0:
            scores_list = [
                obs.get("scores", {})
                for obs in self.session.observations
                if isinstance(obs.get("scores"), dict)
            ]
            if scores_list:
                avg_correctness = sum(s.get("correctness", 0.0) for s in scores_list) / len(scores_list)
                avg_confidence = sum(s.get("confidence", 0.0) for s in scores_list) / len(scores_list)
        return f"Статистика: Всего тем={total}, Подтверждено={confirmed}, Пробелы={gaps}, Галлюцинации={hallucinations}, Средняя correctness={avg_correctness:.2f}, Средняя confidence={avg_confidence:.2f}"

    @staticmethod
    def _parse_json_response(raw: str) -> Dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
            if text.startswith("json"):
                text = text[4:].strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise json.JSONDecodeError("No JSON object found", text, 0)
        candidate = text[start : end + 1]
        return json.loads(candidate)

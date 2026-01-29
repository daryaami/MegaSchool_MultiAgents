import asyncio
import json
from typing import Any, Dict, List

from src.llm import LLMClient
from src.policy import Policy
from src.session import SessionLogger

from .base import Agent


class Interviewer(Agent):
    def __init__(
        self,
        inbox: asyncio.Queue,
        out_user_queue: asyncio.Queue,
        observer_queue: asyncio.Queue,
        session: SessionLogger,
        llm: LLMClient,
        policy: Policy,
        config: Dict[str, object],
    ) -> None:
        super().__init__("Interviewer", inbox)
        self.out_user_queue = out_user_queue
        self.observer_queue = observer_queue
        self.session = session
        self.llm = llm
        self.policy = policy
        self.config = config
        self._question_index = 0

    async def handle(self, msg: Dict[str, Any]) -> None:
        if msg.get("cmd") == "start":
            question = self._initial_question()
            await self._emit_visible(question)
            return

        if msg.get("user_reply"):
            await self._handle_reply(msg["user_reply"])

    def _initial_question(self) -> str:
        position = self.session.meta.get("position", "роль")
        template = self.config["initial_question_template"]
        return template.format(position=position)

    async def _handle_reply(self, user_reply: str) -> None:
        last_question = self.session.history[-1]["question"] if self.session.history else ""

        if self.policy.detect_role_reversal(user_reply):
            reply = await self._answer_role_reversal(user_reply)
            await self._emit_visible(reply, record_history=False)

        reply_queue: asyncio.Queue = asyncio.Queue()
        await self.observer_queue.put(
            {
                "type": "analyze",
                "user_reply": user_reply,
                "last_question": last_question,
                "reply_queue": reply_queue,
                "topic": self._topic_from_question(last_question),
            }
        )
        pending_message = self.config.get("observer_pending_message")
        if pending_message:
            await self._emit_internal(f"[Observer -> Interviewer] {pending_message}")
        timeout_seconds = float(self.config.get("observer_timeout_seconds", 30))
        try:
            obs_result = await asyncio.wait_for(reply_queue.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            obs_result = {
                "internal_thoughts": self.config["observer_timeout_thoughts"],
                "action": "same",
                "scores": {},
                "flags": {},
                "topic": self.config["default_topic"],
            }

        stop_intent = obs_result.get("flags", {}).get("stop_intent", False)
        if stop_intent:
            # Не логируем и не оцениваем ответ, если это намерение завершить интервью
            await self._emit_internal(obs_result['internal_thoughts'])
            await self.out_user_queue.put({"type": "stop_intent"})
            return

        await self._emit_internal(
            f"[Observer -> Interviewer] {obs_result['internal_thoughts']} "
            f"(action={obs_result['action']}, scores={obs_result.get('scores', {})})"
        )

        # Генерируем вопрос и внутренние рассуждения вместе
        suggested_topic = obs_result.get("suggested_topic", obs_result.get("topic", self.config["default_topic"]))
        question_result = await self._next_question(obs_result["action"], suggested_topic)
        if isinstance(question_result, dict):
            next_question = question_result.get("question", "")
            interviewer_thoughts = f"[Interviewer]: {question_result.get('reasoning', '')}"
            # Выводим внутренние рассуждения Interviewer (не показываются пользователю)
            if question_result.get("reasoning"):
                await self._emit_internal(f"[Interviewer -> Internal] {question_result['reasoning']}")
        else:
            # Fallback: если вернулся просто текст (старый формат)
            next_question = question_result
            interviewer_thoughts = self.config["interviewer_internal_template"].format(
                action=obs_result["action"],
                topic=obs_result.get("topic", self.config["default_topic"]),
            )
        
        internal_combined = f"{obs_result['internal_thoughts']} {interviewer_thoughts}"

        self.session.log_turn(
            agent_visible_message=last_question,
            user_message=user_reply,
            internal_thoughts=internal_combined,
            interviewer_action=obs_result["action"],
            scores=obs_result.get("scores", {}),
        )
        self.session.add_history(last_question, user_reply)
        self._record_observation(obs_result)

        await self._emit_visible(next_question)

    def _topic_from_question(self, question: str) -> str:
        lower = question.lower()
        for item in self.config["topic_map"]:
            name = item["name"]
            keywords = item["keywords"]
            if any(token in lower for token in keywords):
                return name
        return self.config["default_topic"]

    def _record_observation(self, obs_result: Dict[str, Any]) -> None:
        status = obs_result.get("status", "confirmed")
        if obs_result["flags"].get("hallucination_suspect"):
            status = "hallucination_suspect"
        elif obs_result["scores"].get("correctness", 1.0) < 0.4 and status != "gap":
            status = "gap"
        self.session.add_observation(
            {
                "topic": obs_result.get("topic", self.config["default_topic"]),
                "status": status,
                "notes": obs_result.get("internal_thoughts", ""),
                "correct_answer": obs_result.get("correct_answer", ""),
                "scores": obs_result.get("scores", {}),
            }
        )

    async def _next_question(self, action: str, suggested_topic: str = "") -> str | Dict[str, str]:
        """Возвращает либо строку (вопрос), либо словарь с 'question' и 'reasoning'."""
        result = await self._generate_question(action, suggested_topic)
        return result

    def _pick_question(self) -> str:
        base_questions = self.config["base_questions"]
        question = base_questions[self._question_index % len(base_questions)]
        self._question_index += 1
        return question

    def _build_history(self) -> str:
        max_turns = int(self.config.get("max_history_turns", 4))
        items = self.session.history[-max_turns:]
        lines = []
        for item in items:
            question = item.get("question", "")
            answer = item.get("answer", "")
            if question:
                lines.append(f"Q: {question}")
            if answer:
                lines.append(f"A: {answer}")
        return "\n".join(lines) if lines else "Нет данных."

    def _build_asked_questions(self) -> str:
        questions = [item.get("question", "") for item in self.session.history if item.get("question")]
        if not questions:
            return "Нет."
        return "\n".join(f"- {q}" for q in questions)

    def _is_repeat(self, question: str) -> bool:
        normalized = question.strip().lower()
        for item in self.session.history:
            asked = item.get("question", "").strip().lower()
            if asked and asked == normalized:
                return True
        return False

    async def _generate_question(self, action: str, suggested_topic: str = "") -> str | Dict[str, str]:
        """Генерирует вопрос и внутренние рассуждения. Возвращает dict с 'question' и 'reasoning' или строку (fallback)."""
        if not self.config.get("use_llm_questions", True):
            return self._pick_question()

        max_retries = int(self.config.get("max_question_retries", 2))
        avoid_note = self.config.get("repeat_avoidance_note", "")
        llm_timeout = float(self.config.get("llm_timeout_seconds", 20))
        use_reasoning = self.config.get("use_internal_reasoning", False)
        
        # Используем тему, предложенную Observer, или определяем по последнему вопросу
        topic = suggested_topic if suggested_topic else self.config["default_topic"]
        
        for attempt in range(max_retries + 1):
            prompt = self.config["question_prompt_template"].format(
                history=self._build_history(),
                asked_questions=self._build_asked_questions(),
                action=action,
                topic=topic,
                position=self.session.meta.get("position", "роль"),
                grade=self.session.meta.get("grade", "уровень"),
            )
            if attempt > 0 and avoid_note:
                prompt = f"{prompt}\n\n{avoid_note}"
            try:
                response = await asyncio.wait_for(
                    self.llm.chat(self.config["system_prompt"], prompt),
                    timeout=llm_timeout,
                )
            except (asyncio.TimeoutError, Exception):
                return self._pick_question()
            
            candidate = response.strip()
            if not candidate:
                continue
                
            # Пытаемся распарсить JSON, если включены рассуждения
            if use_reasoning:
                try:
                    data = self._parse_json_response(candidate)
                    question = data.get("question", "").strip()
                    reasoning = data.get("reasoning", "").strip()
                    if question and not self._is_repeat(question):
                        return {"question": question, "reasoning": reasoning}
                except (ValueError, TypeError, json.JSONDecodeError, KeyError):
                    # Если не JSON или некорректный, пробуем как обычный текст
                    pass
            
            # Fallback: обычный текст (старый формат или без рассуждений)
            if not self._is_repeat(candidate):
                return candidate
                
        return self._pick_question()
    
    @staticmethod
    def _parse_json_response(raw: str) -> Dict[str, Any]:
        """Парсит JSON из ответа LLM, убирая markdown если есть."""
        import json
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

    async def _answer_role_reversal(self, user_reply: str) -> str:
        prompt = self.config["role_reversal_prompt_template"].format(user_question=user_reply)
        llm_timeout = float(self.config.get("llm_timeout_seconds", 20))
        try:
            response = await asyncio.wait_for(
                self.llm.chat(self.config["system_prompt"], prompt),
                timeout=llm_timeout,
            )
        except (asyncio.TimeoutError, Exception):
            return self.policy.role_reversal_reply()
        return response.strip() or self.policy.role_reversal_reply()

    async def _emit_visible(self, text: str, record_history: bool = True) -> None:
        await self.out_user_queue.put({"type": "visible", "text": text})
        if record_history:
            self.session.add_history(text, "")

    async def _emit_internal(self, text: str) -> None:
        await self.out_user_queue.put({"type": "internal", "text": text})

import asyncio
import json
from typing import Any, Dict, Tuple, List, Union

from src.llm import LLMClient
from src.policy import Policy
from src.session import SessionLogger
from src.schemas import QuestionResponse

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
        self._current_topic = config.get("default_topic", "General")
        self._specific_topic_count = 0
        self._max_specific_topics = config.get("max_specific_topics", 3)

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

        reply_queue: asyncio.Queue = asyncio.Queue()
        await self.observer_queue.put(
            {
                "type": "analyze",
                "user_reply": user_reply,
                "last_question": last_question,
                "reply_queue": reply_queue,
                "topic": self._current_topic,
            }
        )
        pending_message = self.config.get("observer_pending_message")
        if pending_message:
            await self._emit_internal(f"Observer: {pending_message}")
        timeout_seconds = float(self.config.get("observer_timeout_seconds", 30))
        try:
            obs_result = await asyncio.wait_for(reply_queue.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            obs_result = {
                "internal_thoughts": self.config["observer_timeout_thoughts"],
                "action": "same",
                "scores": {},
                "flags": {"role_reversal": False, "stop_intent": False},
                "topic": self._current_topic,
            }

        stop_intent = obs_result.get("flags", {}).get("stop_intent", False)
        if stop_intent:
            await self._emit_internal(obs_result['internal_thoughts'])
            await self.out_user_queue.put({"type": "stop_intent"})
            return

        role_reversal = obs_result.get("flags", {}).get("role_reversal", False)
        role_reversal_question = obs_result.get("role_reversal_question", "")
        has_normal_analysis = bool(obs_result.get("scores", {})) and obs_result.get("action") is not None
        
        if role_reversal and has_normal_analysis:
            pass
        elif role_reversal:
            obs_thoughts = obs_result.get("internal_thoughts", "Observer: Кандидат задал вопрос.")
            if obs_thoughts.startswith("[Observer]: "):
                obs_thoughts = obs_thoughts.replace("[Observer]: ", "Observer: ", 1)
            await self._emit_internal(obs_thoughts)
            
            reply = await self._answer_role_reversal(role_reversal_question)
            await self._emit_visible(reply, record_history=False)
            
            if last_question:
                transition = self.config.get("role_reversal_transition", "Теперь вернёмся к интервью.")
                await self._emit_visible(transition, record_history=False)
                
                current_topic = self._current_topic if self._current_topic else self.config["default_topic"]
                question_result = await self._next_question("same", current_topic, {}, previous_question=last_question)
                
                interviewer_thoughts_for_log = ""
                if isinstance(question_result, dict):
                    next_question = question_result.get("question", "")
                    comment = question_result.get("comment", "")
                    interviewer_thoughts_for_log = question_result.get("reasoning", "")
                    if interviewer_thoughts_for_log:
                        await self._emit_internal(f"[Interviewer -> Internal] {interviewer_thoughts_for_log}")
                else:
                    next_question = question_result
                    comment = ""
                
                if comment:
                    combined_message = f"{comment}\n{next_question}"
                    await self._emit_visible(combined_message)
                else:
                    await self._emit_visible(next_question)
                
                internal_lines = []
                if obs_thoughts:
                    internal_lines.append(f"[Observer]: {obs_thoughts}")
                if interviewer_thoughts_for_log:
                    internal_lines.append(f"[Interviewer]: {interviewer_thoughts_for_log}")
                internal_lines.append("{}")
                internal_combined = "\n".join(internal_lines) + "\n"
                
                self.session.log_turn(
                    agent_visible_message=last_question,
                    user_message=role_reversal_question,
                    internal_thoughts=internal_combined,
                    interviewer_action="same",
                    scores={},
                )
                self.session.add_history(last_question, role_reversal_question)
                self.session.add_history(next_question, "")
                
                return
            else:
                suggested_topic = obs_result.get("suggested_topic", "")
                default_topic = self.config["default_topic"]
                if suggested_topic and suggested_topic != default_topic:
                    self._specific_topic_count += 1
                    if self._specific_topic_count >= self._max_specific_topics:
                        suggested_topic = ""
                        self._specific_topic_count = 0
                    else:
                        self._current_topic = suggested_topic
                else:
                    if not suggested_topic:
                        suggested_topic = default_topic
                    self._current_topic = suggested_topic
                    self._specific_topic_count = 0
                question_result = await self._next_question(obs_result.get("action", "same"), suggested_topic, obs_result.get("scores", {}))
                
                interviewer_thoughts_for_log = ""
                if isinstance(question_result, dict):
                    next_question = question_result.get("question", "")
                    comment = question_result.get("comment", "")
                    interviewer_thoughts_for_log = question_result.get("reasoning", "")
                    if interviewer_thoughts_for_log:
                        await self._emit_internal(f"[Interviewer -> Internal] {interviewer_thoughts_for_log}")
                else:
                    next_question = question_result
                    comment = ""
                
                if comment:
                    combined_message = f"{comment}\n{next_question}"
                    await self._emit_visible(combined_message)
                else:
                    await self._emit_visible(next_question)
                
                internal_lines = []
                if obs_thoughts:
                    internal_lines.append(f"[Observer]: {obs_thoughts}")
                if interviewer_thoughts_for_log:
                    internal_lines.append(f"[Interviewer]: {interviewer_thoughts_for_log}")
                internal_lines.append("{}")
                internal_combined = "\n".join(internal_lines) + "\n"
                
                self.session.log_turn(
                    agent_visible_message="",
                    user_message=role_reversal_question,
                    internal_thoughts=internal_combined,
                    interviewer_action="same",
                    scores={},
                )
                self.session.add_history(next_question, "")
            return

        obs_thoughts = obs_result.get('internal_thoughts', '')
        if obs_thoughts.startswith("[Observer]: "):
            obs_thoughts = obs_thoughts.replace("[Observer]: ", "", 1).strip()
        action = obs_result.get('action', 'same')
        scores = obs_result.get('scores', {})
        correctness = scores.get('correctness', 0.0) if scores else 0.0
        confidence = scores.get('confidence', 0.0) if scores else 0.0
        verbosity = scores.get('verbosity', 0.0) if scores else 0.0
        await self._emit_internal(f"Observer: {obs_thoughts} (action: {action}, correctness: {correctness:.2f}, confidence: {confidence:.2f}, verbosity: {verbosity:.2f})")

        suggested_topic = obs_result.get("suggested_topic", "")
        default_topic = self.config["default_topic"]
        
        if suggested_topic and suggested_topic != default_topic:
            self._specific_topic_count += 1
            if self._specific_topic_count >= self._max_specific_topics:
                suggested_topic = ""
                self._specific_topic_count = 0
                await self._emit_internal(f"Interviewer: Достигнут лимит углубления в детали ({self._max_specific_topics} вопросов). Переключаюсь на общую тему.")
            else:
                self._current_topic = suggested_topic
        else:
            if not suggested_topic:
                suggested_topic = default_topic
            self._current_topic = suggested_topic
            self._specific_topic_count = 0
        
        question_result = await self._next_question(obs_result["action"], suggested_topic, obs_result.get("scores", {}))
        interviewer_thoughts = ""
        comment = ""
        if isinstance(question_result, dict):
            next_question = question_result.get("question", "")
            interviewer_thoughts = question_result.get('reasoning', '')
            comment = question_result.get('comment', '')
            if interviewer_thoughts:
                reasoning_short = interviewer_thoughts.split('.')[0] + '.' if '.' in interviewer_thoughts else interviewer_thoughts[:100]
                await self._emit_internal(f"Interviewer: {reasoning_short}")
        else:
            next_question = question_result
            interviewer_thoughts = self.config["interviewer_internal_template"].format(
                action=obs_result["action"],
                topic=suggested_topic,
            )
        
        obs_thoughts = obs_result.get('internal_thoughts', '')
        if obs_thoughts.startswith("[Observer]: "):
            obs_thoughts = obs_thoughts.replace("[Observer]: ", "", 1).strip()
        
        internal_lines = []
        if obs_thoughts:
            internal_lines.append(f"[Observer]: {obs_thoughts}")
        if interviewer_thoughts:
            internal_lines.append(f"[Interviewer]: {interviewer_thoughts}")
        
        scores = obs_result.get('scores', {})
        scores_json = json.dumps(scores, ensure_ascii=False)
        internal_lines.append(scores_json)
        
        internal_combined = "\n".join(internal_lines) + "\n"

        user_message_to_log = obs_result.get("answer_part", user_reply)
        if role_reversal and not obs_result.get("answer_part"):
            if role_reversal_question:
                for marker in ["Слушайте,", "Слушайте", " а ", " А ", " но ", " Но ", " и ", " И "]:
                    if marker in user_reply:
                        parts = user_reply.split(marker, 1)
                        if len(parts) > 1 and len(parts[0].strip()) > 10:
                            user_message_to_log = parts[0].strip()
                            break
                if user_message_to_log == user_reply:
                    question_keywords = ["сколько", "какие", "какой", "как", "что", "можете", "расскажите"]
                    for keyword in question_keywords:
                        pos = user_reply.lower().find(f" {keyword} ")
                        if pos > 20:
                            user_message_to_log = user_reply[:pos].strip()
                            break
        
        self.session.log_turn(
            agent_visible_message=last_question,
            user_message=user_message_to_log,
            internal_thoughts=internal_combined,
            interviewer_action=obs_result["action"],
            scores=obs_result.get("scores", {}),
        )
        self.session.add_history(last_question, user_message_to_log)
        self._record_observation(obs_result)

        if role_reversal and role_reversal_question and has_normal_analysis:
            reply = await self._answer_role_reversal(role_reversal_question)
            await self._emit_visible(reply, record_history=False)
            transition = self.config.get("role_reversal_transition", "Теперь вернемся к интервью.")
            await self._emit_visible(transition, record_history=False)

        if comment:
            combined_message = f"{comment}\n{next_question}"
            await self._emit_visible(combined_message)
        else:
            await self._emit_visible(next_question)

    def _record_observation(self, obs_result: Dict[str, Any]) -> None:
        status = obs_result.get("status", "confirmed")
        if obs_result["flags"].get("hallucination_suspect"):
            status = "hallucination_suspect"
        elif obs_result["scores"].get("correctness", 1.0) < 0.4 and status != "gap":
            status = "gap"
        topic = obs_result.get("suggested_topic", "")
        if not topic:
            topic = obs_result.get("topic", self._current_topic)
        self.session.add_observation(
            {
                "topic": topic,
                "status": status,
                "notes": obs_result.get("internal_thoughts", ""),
                "correct_answer": obs_result.get("correct_answer", ""),
                "scores": obs_result.get("scores", {}),
            }
        )

    async def _next_question(self, action: str, suggested_topic: str = "", scores: Dict[str, Any] = None, previous_question: str = "") -> Union[str, Dict[str, str]]:
        """Возвращает либо строку (вопрос), либо словарь с 'question', 'reasoning' и 'comment'.
        
        Args:
            action: Действие (increase/same/decrease)
            suggested_topic: Предложенная тема
            scores: Оценки ответа кандидата
            previous_question: Предыдущий вопрос (для случая role_reversal, чтобы вернуться к той же теме)
        """
        result = await self._generate_question(action, suggested_topic, scores or {}, previous_question)
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

    async def _generate_question(self, action: str, suggested_topic: str = "", scores: Dict[str, Any] = None, previous_question: str = "") -> Union[str, Dict[str, str]]:
        """Генерирует вопрос, комментарий и внутренние рассуждения. Возвращает dict с 'question', 'reasoning' и 'comment' или строку (fallback).
        
        Args:
            action: Действие (increase/same/decrease)
            suggested_topic: Предложенная тема
            scores: Оценки ответа кандидата
            previous_question: Предыдущий вопрос (для случая role_reversal, чтобы вернуться к той же теме)
        """
        if not self.config.get("use_llm_questions", True):
            return self._pick_question()

        max_retries = int(self.config.get("max_question_retries", 2))
        avoid_note = self.config.get("repeat_avoidance_note", "")
        llm_timeout = float(self.config.get("llm_timeout_seconds", 20))
        use_reasoning = self.config.get("use_internal_reasoning", False)
        
        topic = suggested_topic if suggested_topic else self.config["default_topic"]
        
        scores_info = ""
        if scores:
            correctness = scores.get("correctness", 0.0)
            confidence = scores.get("confidence", 0.0)
            scores_info = f"\n\nОценка последнего ответа кандидата:\n- Правильность: {correctness:.2f} (0.0-1.0)\n- Уверенность: {confidence:.2f} (0.0-1.0)\n- Действие: {action}"
        
        previous_question_info = ""
        if previous_question:
            previous_question_info = f"\n\nВАЖНО: Кандидат задал вопрос интервьюеру, и мы ответили на него. Теперь нужно вернуться к исходному вопросу, который был задан ранее:\n\"{previous_question}\"\n\nСгенерируй новый вопрос, который будет на ту же тему и с тем же смыслом, что и исходный вопрос выше, но сформулируй его по-другому (не повторяй дословно)."
        
        for attempt in range(max_retries + 1):
            prompt = self.config["question_prompt_template"].format(
                history=self._build_history(),
                asked_questions=self._build_asked_questions(),
                action=action,
                topic=topic,
                position=self.session.meta.get("position", "роль"),
                grade=self.session.meta.get("grade", "уровень"),
                experience=self.session.meta.get("experience", "N/A"),
                scores_info=scores_info,
            )
            if previous_question_info:
                prompt = f"{prompt}{previous_question_info}"
            if attempt > 0 and avoid_note:
                prompt = f"{prompt}\n\n{avoid_note}"
            try:
                system_prompt = self.config["system_prompt"].format(
                    candidate_name=self.session.meta.get("name", "кандидат"),
                    position=self.session.meta.get("position", "роль"),
                    grade=self.session.meta.get("grade", "уровень"),
                    experience=self.session.meta.get("experience", "N/A"),
                )
                response = await asyncio.wait_for(
                    self.llm.chat(system_prompt, prompt),
                    timeout=llm_timeout,
                )
            except (asyncio.TimeoutError, Exception):
                return self._pick_question()
            
            candidate = response.strip()
            if not candidate:
                continue
                
            if use_reasoning:
                try:
                    raw_data = self._parse_json_response(candidate)
                    try:
                        response = QuestionResponse(**raw_data)
                        if response.question and not self._is_repeat(response.question):
                            return {
                                "question": response.question,
                                "reasoning": response.reasoning,
                                "comment": response.comment or ""
                            }
                    except Exception as validation_error:
                        pass
                except (ValueError, TypeError, json.JSONDecodeError, KeyError):
                    pass
            
            if not self._is_repeat(candidate):
                return candidate
                
        return self._pick_question()
    
    @staticmethod
    def _parse_json_response(raw: str) -> Dict[str, Any]:
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

    async def _check_question_relevance(self, user_question: str) -> Tuple[bool, str]:
        relevance_prompt = self.config.get("role_reversal_relevance_check_prompt", "")
        if not relevance_prompt:
            return True, ""
        
        prompt = relevance_prompt.format(user_question=user_question)
        llm_timeout = float(self.config.get("llm_timeout_seconds", 20))
        
        try:
            response = await asyncio.wait_for(
                self.llm.chat(
                    "Ты помощник для определения релевантности вопросов на собеседовании. Отвечай только JSON.",
                    prompt
                ),
                timeout=llm_timeout,
            )
            
            import json
            text = response.strip()
            if text.startswith("```"):
                text = text.strip("`").strip()
                if text.startswith("json"):
                    text = text[4:].strip()
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                data = json.loads(text[start : end + 1])
                is_relevant = data.get("relevant", True)
                reason = data.get("reason", "")
                return bool(is_relevant), reason
        except (asyncio.TimeoutError, json.JSONDecodeError, KeyError, Exception):
            pass
        
        return True, ""

    async def _answer_role_reversal(self, user_reply: str) -> str:
        is_relevant, reason = await self._check_question_relevance(user_reply)
        
        if not is_relevant:
            irrelevant_reply = self.config.get(
                "role_reversal_irrelevant_reply",
                "Извините, этот вопрос не относится к теме интервью. Давайте сосредоточимся на технических вопросах."
            )
            return irrelevant_reply
        
        prompt = self.config["role_reversal_prompt_template"].format(user_question=user_reply)
        llm_timeout = float(self.config.get("llm_timeout_seconds", 20))
        try:
            system_prompt = self.config["system_prompt"].format(
                candidate_name=self.session.meta.get("name", "кандидат"),
                position=self.session.meta.get("position", "роль"),
                grade=self.session.meta.get("grade", "уровень"),
                experience=self.session.meta.get("experience", "N/A"),
            )
            response = await asyncio.wait_for(
                self.llm.chat(system_prompt, prompt),
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

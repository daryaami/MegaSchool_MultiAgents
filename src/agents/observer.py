import asyncio
import json
import time
from typing import Any, Dict

from src.llm import LLMClient
from src.policy import Policy
from src.score import Score, score_answer

from .base import Agent


class Observer(Agent):
    def __init__(self, inbox: asyncio.Queue, llm: LLMClient, policy: Policy, config: Dict[str, object]) -> None:
        super().__init__("Observer", inbox)
        self.llm = llm
        self.policy = policy
        self.config = config
        self._llm_cooldown_until = 0.0

    async def handle(self, msg: Dict[str, Any]) -> None:
        if msg.get("type") != "analyze":
            return
        reply_queue: asyncio.Queue = msg["reply_queue"]
        try:
            user_reply = msg["user_reply"]
            last_question = msg.get("last_question", "")
            scores = score_answer(user_reply, last_question)

            internal_notes = []
            llm_analysis, analysis_error = await self._get_llm_analysis(last_question, user_reply)
            stop_intent = bool(llm_analysis.get("stop_intent", False))
            
            # Если пользователь хочет закончить интервью, не анализируем технически
            if stop_intent:
                result = {
                    "internal_thoughts": "[Observer]: Кандидат хочет завершить интервью.",
                    "action": "stop",
                    "scores": {},
                    "flags": {
                        "hallucination_suspect": False,
                        "off_topic": False,
                        "stop_intent": True,
                    },
                    "topic": msg.get("topic", self.config["default_topic"]),
                    "status": "confirmed",
                    "correct_answer": "",
                }
                await reply_queue.put(result)
                return
            
            hallucination = bool(llm_analysis.get("hallucination", False))
            off_topic = bool(llm_analysis.get("off_topic", False))

            if hallucination:
                reason = llm_analysis.get("hallucination_reason", "")
                template = self.config["internal_notes"]["hallucination"]
                internal_notes.append(template.format(reason=reason))
            if off_topic:
                reason = llm_analysis.get("off_topic_reason", "")
                template = self.config["internal_notes"].get("off_topic", "Ответ не по теме.")
                if reason:
                    internal_notes.append(f"{template} {reason}")
                else:
                    internal_notes.append(template)
            if not llm_analysis:
                fallback_note = self.config.get("analysis_fallback_note", "")
                if fallback_note:
                    internal_notes.append(fallback_note.format(error=analysis_error))

            action, action_reason = self.policy.action_from_score(
                scores.correctness,
                scores.confidence_estimate,
            )
            status = "confirmed"
            correct_answer = ""
            if llm_analysis:
                action = llm_analysis["action"]
                scores = llm_analysis["scores"]
                if llm_analysis["notes"]:
                    action_reason = llm_analysis["notes"]
                status = llm_analysis["status"]
                correct_answer = llm_analysis["correct_answer"]
            if hallucination:
                action = "correct_and_continue"
                status = "hallucination_suspect"
            elif off_topic:
                action = "redirect"

            prefix = self.config["internal_thoughts_prefix"]
            internal_thoughts = prefix + " ".join(internal_notes).strip()
            if action_reason:
                internal_thoughts = f"{internal_thoughts} {action_reason}".strip()

            # Определяем тему для следующего вопроса
            suggested_topic = llm_analysis.get("suggested_topic", "") if llm_analysis else ""
            suggested_topic = suggested_topic.strip() if suggested_topic else ""
            current_topic = msg.get("topic", self.config["default_topic"])
            next_topic = suggested_topic if suggested_topic else current_topic
            
            # Добавляем предложение темы в internal_thoughts, если Observer предложил тему
            if suggested_topic and suggested_topic != current_topic:
                internal_thoughts = f"{internal_thoughts} Рекомендую спросить про: {suggested_topic}."
            
            result = {
                "internal_thoughts": internal_thoughts,
                "action": action,
                "scores": {
                    "correctness": scores.correctness,
                    "confidence": scores.confidence_estimate,
                    "verbosity": scores.verbosity,
                    "uses_examples": scores.uses_examples,
                },
                "flags": {
                    "hallucination_suspect": hallucination,
                    "off_topic": off_topic,
                    "stop_intent": stop_intent,
                },
                "topic": msg.get("topic", self.config["default_topic"]),
                "suggested_topic": next_topic,
                "status": status,
                "correct_answer": correct_answer,
            }
        except Exception as exc:
            note = self.config.get("observer_error_note", "Observer error.")
            result = {
                "internal_thoughts": f"{note} Details: {exc}",
                "action": "same",
                "scores": {},
                "flags": {"hallucination_suspect": False, "off_topic": False},
                "topic": msg.get("topic", self.config["default_topic"]),
            }
        await reply_queue.put(result)

    async def _get_llm_analysis(self, question: str, answer: str) -> tuple[Dict[str, Any], str]:
        if time.time() < self._llm_cooldown_until:
            return {}, self.config.get("llm_cooldown_note", "cooldown")
        prompt = self.config["analysis_json_prompt_template"].format(question=question, answer=answer)
        llm_timeout = float(self.config.get("llm_timeout_seconds", 20))
        max_retries = int(self.config.get("llm_max_retries", 2))
        
        last_error = ""
        for attempt in range(max_retries + 1):
            try:
                response = await asyncio.wait_for(
                    self.llm.chat(
                        self.config["analysis_system_prompt"],
                        prompt,
                    ),
                    timeout=llm_timeout,
                )
                break
            except asyncio.TimeoutError:
                last_error = "timeout"
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                self._llm_cooldown_until = time.time() + float(
                    self.config.get("llm_cooldown_seconds", 30)
                )
                return {}, "timeout"
            except ConnectionError as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                self._llm_cooldown_until = time.time() + float(
                    self.config.get("llm_cooldown_seconds", 30)
                )
                error_msg = str(exc)
                if "недоступен" in error_msg:
                    return {}, error_msg
                return {}, f"Соединение с LLM разорвано: {error_msg}"
            except Exception as exc:
                last_error = str(exc)
                if attempt < max_retries:
                    await asyncio.sleep(1.0 * (2 ** attempt))
                    continue
                self._llm_cooldown_until = time.time() + float(
                    self.config.get("llm_cooldown_seconds", 30)
                )
                error_msg = str(exc)
                if "Connection" in error_msg or "разорван" in error_msg:
                    return {}, "Соединение с LLM разорвано"
                return {}, error_msg
        else:
            self._llm_cooldown_until = time.time() + float(
                self.config.get("llm_cooldown_seconds", 30)
            )
            return {}, last_error or "max_retries_exceeded"
        try:
            data = self._parse_json_response(response)
            action = data.get("action")
            scores = data.get("scores", {})
            notes = data.get("notes", "")
            status = data.get("status", "confirmed")
            correct_answer = data.get("correct_answer", "")
            hallucination = bool(data.get("hallucination", False))
            hallucination_reason = data.get("hallucination_reason", "")
            off_topic = bool(data.get("off_topic", False))
            off_topic_reason = data.get("off_topic_reason", "")
            stop_intent = bool(data.get("stop_intent", False))
            stop_intent_reason = data.get("stop_intent_reason", "")
            suggested_topic = data.get("suggested_topic", "").strip()
            if action not in {"increase", "same", "decrease"}:
                return {}, "invalid_action"
            if status not in {"confirmed", "gap"}:
                status = "confirmed"
            correctness = float(scores.get("correctness", 0.0))
            confidence = float(scores.get("confidence", 0.0))
            score_obj = Score(
                correctness=correctness,
                confidence_estimate=confidence,
                verbosity=score_answer(answer, question).verbosity,
                uses_examples=score_answer(answer, question).uses_examples,
            )
            return {
                "action": action,
                "scores": score_obj,
                "notes": notes,
                "status": status,
                "correct_answer": correct_answer,
                "hallucination": hallucination,
                "hallucination_reason": hallucination_reason,
                "off_topic": off_topic,
                "off_topic_reason": off_topic_reason,
                "stop_intent": stop_intent,
                "stop_intent_reason": stop_intent_reason,
                "suggested_topic": suggested_topic,
            }, ""
        except (ValueError, TypeError, json.JSONDecodeError):
            return {}, "invalid_json"

    @staticmethod
    def _parse_json_response(raw: str) -> Dict[str, Any]:
        text = raw.strip()
        if text.startswith("```"):
            text = text.strip("`").strip()
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise json.JSONDecodeError("No JSON object found", text, 0)
        candidate = text[start : end + 1]
        return json.loads(candidate)

import asyncio
import json
import time
from typing import Any, Dict, Tuple, Optional

from src.llm import LLMClient
from src.policy import Policy
from src.score import Score, score_answer
from src.schemas import ObserverAnalysis

from .base import Agent


class Observer(Agent):
    def __init__(
        self,
        inbox: asyncio.Queue,
        llm: LLMClient,
        policy: Policy,
        config: Dict[str, object],
        rag: Optional[Any] = None,
    ) -> None:
        super().__init__("Observer", inbox)
        self.llm = llm
        self.policy = policy
        self.config = config
        self.rag = rag
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
            role_reversal = bool(llm_analysis.get("role_reversal", False))
            
            if stop_intent:
                result = {
                    "internal_thoughts": "Кандидат хочет завершить интервью.",
                    "action": "stop",
                    "scores": {},
                    "flags": {
                        "hallucination_suspect": False,
                        "off_topic": False,
                        "stop_intent": True,
                        "role_reversal": False,
                    },
                    "topic": msg.get("topic", self.config["default_topic"]),
                    "status": "confirmed",
                    "correct_answer": "",
                }
                await reply_queue.put(result)
                return
            
            has_meaningful_answer = False
            answer_part = user_reply
            role_reversal_question = user_reply
            
            if role_reversal:
                for marker in ["Слушайте,", "Слушайте", " а ", " А ", " но ", " Но ", " и ", " И "]:
                    if marker in user_reply:
                        parts = user_reply.split(marker, 1)
                        if len(parts) > 1:
                            answer_part = parts[0].strip()
                            role_reversal_question = parts[1].strip()
                            role_reversal_question = role_reversal_question.lstrip(" ,.!?")
                            if len(answer_part) > 10:
                                has_meaningful_answer = True
                                break
                
                if not has_meaningful_answer and len(user_reply) > 50:
                    question_keywords = ["сколько", "какие", "какой", "как", "что", "можете", "расскажите"]
                    for keyword in question_keywords:
                        pos = user_reply.lower().find(f" {keyword} ")
                        if pos > 20:
                            answer_part = user_reply[:pos].strip()
                            role_reversal_question = user_reply[pos:].strip()
                            if len(answer_part) > 10:
                                has_meaningful_answer = True
                                break
                
                if not has_meaningful_answer and len(user_reply) > 60:
                    first_sentence = user_reply.split('.')[0] if '.' in user_reply else user_reply.split('?')[0] if '?' in user_reply else user_reply[:50]
                    if len(first_sentence) > 20 and '?' not in first_sentence:
                        has_meaningful_answer = True
                        last_q_pos = user_reply.rfind('?')
                        if last_q_pos > 20:
                            answer_part = user_reply[:last_q_pos].strip()
                            role_reversal_question = user_reply[last_q_pos:].strip()
            
            if role_reversal and not has_meaningful_answer:
                scores = score_answer(user_reply, last_question)
                role_reversal_reason = llm_analysis.get("role_reversal_reason", "")
                result = {
                    "internal_thoughts": f"Кандидат задал вопрос интервьюеру. {role_reversal_reason}",
                    "action": "same",
                    "scores": {},
                    "flags": {
                        "hallucination_suspect": False,
                        "off_topic": False,
                        "stop_intent": False,
                        "role_reversal": True,
                    },
                    "topic": msg.get("topic", self.config["default_topic"]),
                    "status": "confirmed",
                    "correct_answer": "",
                    "role_reversal_question": role_reversal_question,
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
            if has_meaningful_answer:
                scores = score_answer(answer_part, last_question)
            
            if llm_analysis:
                action = llm_analysis["action"]
                action_reason = llm_analysis.get("notes", "")
                status = llm_analysis["status"]
                correct_answer = llm_analysis["correct_answer"]
                if has_meaningful_answer:
                    llm_scores = llm_analysis["scores"]
                    if isinstance(llm_scores, dict):
                        scores.correctness = llm_scores.get("correctness", scores.correctness)
                        scores.confidence_estimate = llm_scores.get("confidence", scores.confidence_estimate)
                else:
                    llm_scores = llm_analysis["scores"]
                    if isinstance(llm_scores, dict):
                        scores = Score(
                            correctness=llm_scores.get("correctness", 0.0),
                            confidence_estimate=llm_scores.get("confidence", 0.0),
                            verbosity=llm_scores.get("verbosity", 0.0),
                            uses_examples=llm_scores.get("uses_examples", False),
                        )
            else:
                action, action_reason = self.policy.action_from_score(
                    scores.correctness,
                    scores.confidence_estimate,
                )
                status = "confirmed"
                correct_answer = ""
                fallback_note = self.config.get("analysis_fallback_note", "")
                if fallback_note:
                    internal_notes.append(fallback_note.format(error=analysis_error))
            
            if hallucination:
                action = "correct_and_continue"
                status = "hallucination_suspect"
            elif off_topic:
                action = "redirect"

            internal_notes_text = ". ".join(internal_notes).strip() if internal_notes else ""
            
            if action_reason:
                if internal_notes_text:
                    internal_notes_text = f"{internal_notes_text}. {action_reason}"
                else:
                    internal_notes_text = action_reason

            suggested_topic = llm_analysis.get("suggested_topic", "") if llm_analysis else ""
            suggested_topic = suggested_topic.strip() if suggested_topic else ""
            current_topic = msg.get("topic", self.config["default_topic"])
            next_topic = suggested_topic if suggested_topic else current_topic
            
            if suggested_topic and suggested_topic != current_topic:
                if internal_notes_text:
                    internal_notes_text = f"{internal_notes_text}. Тема: {suggested_topic}"
                else:
                    internal_notes_text = f"Тема: {suggested_topic}"
            
            internal_thoughts = internal_notes_text.strip()
            
            if role_reversal and has_meaningful_answer:
                role_reversal_reason = llm_analysis.get("role_reversal_reason", "")
                if role_reversal_reason:
                    internal_thoughts = f"{internal_thoughts}. Кандидат также задал вопрос интервьюеру: {role_reversal_reason}".strip()
                else:
                    internal_thoughts = f"{internal_thoughts}. Кандидат также задал вопрос интервьюеру.".strip()
            
            if has_meaningful_answer:
                scores = score_answer(answer_part, last_question)
            
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
                    "role_reversal": role_reversal,
                },
                "topic": msg.get("topic", self.config["default_topic"]),
                "suggested_topic": next_topic,
                "status": status,
                "correct_answer": correct_answer,
            }
            
            if role_reversal:
                result["role_reversal_question"] = role_reversal_question
            
            if has_meaningful_answer:
                result["answer_part"] = answer_part
            
        except Exception as exc:
            note = self.config.get("observer_error_note", "Ошибка")
            result = {
                "internal_thoughts": f"{note}: {str(exc)[:50]}",
                "action": "same",
                "scores": {},
                "flags": {"hallucination_suspect": False, "off_topic": False, "stop_intent": False, "role_reversal": False},
                "topic": msg.get("topic", self.config["default_topic"]),
            }
        await reply_queue.put(result)

    async def _get_llm_analysis(self, question: str, answer: str) -> Tuple[Dict[str, Any], str]:
        if time.time() < self._llm_cooldown_until:
            return {}, self.config.get("llm_cooldown_note", "cooldown")
        
        reference_materials = ""
        if self.rag:
            if self.rag.is_available():
                rag_config = self.config.get("rag", {})
                top_k = rag_config.get("top_k", 5)
                min_relevance = rag_config.get("min_relevance", 0.6)
                
                query = f"{question} {answer}"
                results = self.rag.search(query, top_k=top_k, min_relevance=min_relevance)
                
                if results:
                    reference_materials = self.rag.format_reference_materials(results)
        
        base_prompt = self.config["analysis_json_prompt_template"].format(question=question, answer=answer)
        if reference_materials:
            prompt = f"{reference_materials}\n\n{base_prompt}"
        else:
            prompt = base_prompt
        
        print("\n" + "="*80)
        print("[Observer] ПРОМПТ ДЛЯ LLM:")
        print("="*80)
        print(f"System Prompt: {self.config['analysis_system_prompt']}")
        print("\n" + "-"*80)
        print("User Prompt:")
        print("-"*80)
        print(prompt)
        print("="*80 + "\n")
        
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
            raw_data = self._parse_json_response(response)
            
            try:
                analysis = ObserverAnalysis(**raw_data)
            except Exception as validation_error:
                return {}, f"validation_error: {str(validation_error)}"
            
            score_obj = Score(
                correctness=analysis.scores.correctness,
                confidence_estimate=analysis.scores.confidence,
                verbosity=score_answer(answer, question).verbosity,
                uses_examples=score_answer(answer, question).uses_examples,
            )
            
            return {
                "action": analysis.action,
                "scores": score_obj,
                "notes": analysis.notes,
                "status": analysis.status,
                "correct_answer": analysis.correct_answer,
                "hallucination": analysis.hallucination,
                "hallucination_reason": analysis.hallucination_reason,
                "off_topic": analysis.off_topic,
                "off_topic_reason": analysis.off_topic_reason,
                "stop_intent": analysis.stop_intent,
                "stop_intent_reason": analysis.stop_intent_reason,
                "role_reversal": analysis.role_reversal,
                "role_reversal_reason": analysis.role_reversal_reason,
                "suggested_topic": analysis.suggested_topic.strip(),
            }, ""
        except (ValueError, TypeError, json.JSONDecodeError) as e:
            return {}, f"invalid_json: {str(e)}"

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

# Документация: Запросы к LLM в Observer и Interviewer

Этот документ описывает, как происходят запросы к LLM в агентах Observer и Interviewer, в каком формате отправляются промпты и что возвращается.

## Structured Output через Pydantic

Система использует **Pydantic схемы** для валидации всех ответов LLM:

- **Observer** → `ObserverAnalysis` (`src/schemas.py`)
- **Interviewer** → `QuestionResponse` (`src/schemas.py`)
- **Manager** → `FinalReport` (`src/schemas.py`)

**Преимущества:**
- Автоматическая валидация типов и диапазонов значений
- Гарантированная структура данных
- Типобезопасность на уровне кода
- Понятные сообщения об ошибках валидации
- Меньше ошибок парсинга в production

Все схемы определены в `src/schemas.py` и используются для валидации JSON-ответов от LLM перед их использованием в коде.

---

## Observer: Анализ ответов кандидата

### Процесс запроса к Observer

#### 1. Инициация запроса (из Interviewer)

Когда кандидат отвечает, Interviewer отправляет сообщение в очередь Observer:

```python
# В interviewer.py, метод _handle_reply()
await self.observer_queue.put({
    "type": "analyze",
    "user_reply": user_reply,           # Ответ кандидата
    "last_question": last_question,      # Последний вопрос
    "reply_queue": reply_queue,          # Очередь для ответа
    "topic": self._topic_from_question(last_question)  # Текущая тема
})
```

#### 2. Обработка запроса (Observer.handle)

Observer получает сообщение и вызывает `_get_llm_analysis()`:

```python
# В observer.py, метод handle()
llm_analysis, analysis_error = await self._get_llm_analysis(
    last_question,  # Вопрос интервьюера
    user_reply      # Ответ или вопрос кандидата
)
```

**Специальные случаи обрабатываются сразу после получения анализа:**

1. **stop_intent** - если кандидат хочет завершить интервью, возвращается специальный результат
2. **role_reversal** - если кандидат задаёт вопрос интервьюеру, возвращается специальный результат с флагом `role_reversal: true` и сохраняется вопрос в `role_reversal_question`
3. **hallucination** и **off_topic** - обрабатываются в основном потоке анализа

#### 3. Формирование промпта для LLM

В `_get_llm_analysis()` формируется промпт из двух частей:

**System Prompt (из конфига):**
```
"Ты внутренний наблюдатель. Анализируй ответы кандидата на технические вопросы. 
Твоя задача: 
1) Оценить ответ (correctness, confidence). 
2) Предложить тему для следующего вопроса (suggested_topic) на основе пробелов в знаниях или необходимости углубиться. 
3) Различать: признание незнания (\"не знаю\") - НЕ stop_intent, явное намерение завершить (\"стоп\") - stop_intent=true. 
4) Определять, задаёт ли кандидат вопрос интервьюеру (role_reversal) - это когда кандидат спрашивает что-то у интервьюера вместо ответа на вопрос.
Верни только JSON без Markdown и пояснений."
```

**User Prompt (шаблон с подстановкой):**
```
"Проанализируй ответ кандидата.
Верни СТРОГО JSON без каких-либо пояснений, префиксов или markdown.

ВАЖНО про stop_intent:
- stop_intent=true ТОЛЬКО если кандидат ЯВНО хочет завершить интервью (\"стоп\", \"закончим\", \"давай фидбэк\", \"хватит\", \"завершим\")
- stop_intent=false если кандидат просто признаёт незнание (\"не знаю\", \"не умею\", \"не помню\", \"не изучал\") - это НОРМАЛЬНЫЙ ответ на интервью
- stop_intent=false если кандидат даёт любой технический ответ, даже слабый

ВАЖНО про role_reversal:
- role_reversal=true если кандидат задаёт вопрос интервьюеру (например: \"Что такое X?\", \"Как работает Y?\", \"Можете объяснить Z?\", \"А что насчёт...?\")
- role_reversal=false если кандидат отвечает на вопрос интервьюера или просто признаёт незнание
- Различай: \"Не знаю что такое X\" (ответ) vs \"Что такое X?\" (вопрос к интервьюеру)
- Различай: \"Можете уточнить вопрос?\" (вопрос к интервьюеру) vs \"Я не понял вопрос\" (ответ)

ВАЖНО про suggested_topic:
- Если видишь пробелы в знаниях или нужно углубиться в тему - предложи конкретную тему (например: 'SQL Transactions', 'Django ORM', 'Python Data Structures', 'Error Handling', 'API Design')
- Если ответ хороший и нужно усложнить - предложи более продвинутую тему
- Если ответ слабый - предложи более базовую тему или ту же тему для уточнения
- Если всё хорошо - можешь оставить пустую строку

Формат:
{\"action\":\"increase|same|decrease\",\"scores\":{\"correctness\":0.0-1.0,\"confidence\":0.0-1.0},\"notes\":\"кратко\",\"status\":\"confirmed|gap\",\"correct_answer\":\"если есть пробел, дай корректный краткий ответ\",\"hallucination\":true|false,\"hallucination_reason\":\"кратко\",\"off_topic\":true|false,\"off_topic_reason\":\"кратко\",\"stop_intent\":true|false,\"stop_intent_reason\":\"кратко\",\"role_reversal\":true|false,\"role_reversal_reason\":\"кратко\",\"suggested_topic\":\"конкретная тема для следующего вопроса или пустая строка\"}

Вопрос: {question}
Ответ: {answer}"
```

В шаблон подставляются:
- `{question}` → `last_question`
- `{answer}` → `user_reply`

#### 4. Вызов LLM

```python
response = await self.llm.chat(
    system_prompt=self.config["analysis_system_prompt"],
    user_prompt=prompt,  # Сформированный промпт с вопросом и ответом
    temperature=0.2  # По умолчанию
)
```

**Особенности:**
- **Таймаут**: `llm_timeout_seconds` (по умолчанию 60)
- **Retry**: до `llm_max_retries + 1` попыток (по умолчанию 3)
- **Экспоненциальная задержка**: `1.0 * (2 ** attempt)` секунд
- **Cooldown**: при ошибках устанавливается на `llm_cooldown_seconds` (30 сек)

#### 5. Парсинг и валидация ответа LLM

LLM должен вернуть JSON. Парсинг и валидация:

```python
# Убирает markdown обёртки (```json ... ```)
raw_data = self._parse_json_response(response)

# Валидация через Pydantic схему
try:
    analysis = ObserverAnalysis(**raw_data)
except Exception as validation_error:
    return {}, f"validation_error: {str(validation_error)}"
```

**Важно:** Система использует **Pydantic схемы** для валидации всех ответов LLM:
- Автоматическая проверка типов и обязательных полей
- Гарантированная структура данных
- Понятные сообщения об ошибках валидации
- Типобезопасность на уровне кода

Схемы определены в `src/schemas.py`:
- `ObserverAnalysis` - для ответов Observer
- `QuestionResponse` - для ответов Interviewer
- `FinalReport` - для отчётов Manager

**Ожидаемая структура JSON:**

```json
{
  "action": "increase" | "same" | "decrease",
  "scores": {
    "correctness": 0.0-1.0,
    "confidence": 0.0-1.0
  },
  "notes": "краткое описание анализа",
  "status": "confirmed" | "gap",
  "correct_answer": "правильный ответ, если есть пробел",
  "hallucination": true | false,
  "hallucination_reason": "причина подозрения на галлюцинацию",
  "off_topic": true | false,
  "off_topic_reason": "почему ответ не по теме",
  "stop_intent": true | false,
  "stop_intent_reason": "почему кандидат хочет завершить",
  "role_reversal": true | false,
  "role_reversal_reason": "почему кандидат задаёт вопрос интервьюеру",
  "suggested_topic": "тема для следующего вопроса или пустая строка"
}
```

#### 6. Валидация и преобразование

После парсинга выполняется валидация через **Pydantic**:

```python
# Валидация через Pydantic схему ObserverAnalysis
analysis = ObserverAnalysis(**raw_data)

# Pydantic автоматически проверяет:
# - Типы полей (action должен быть Literal["increase", "same", "decrease"])
# - Диапазоны значений (scores.correctness и scores.confidence в [0.0, 1.0])
# - Обязательные поля
# - Статус (должен быть "confirmed" или "gap")

# Преобразование scores в объект Score
score_obj = Score(
    correctness=analysis.scores.correctness,
    confidence_estimate=analysis.scores.confidence,
    verbosity=score_answer(answer, question).verbosity,  # Из эвристики
    uses_examples=score_answer(answer, question).uses_examples  # Из эвристики
)
```

**Преимущества Pydantic валидации:**
- Автоматическая проверка всех полей
- Валидация типов и диапазонов значений
- Понятные сообщения об ошибках
- Типобезопасность на уровне кода
- Меньше ошибок парсинга в production

#### 7. Возвращаемый результат

`_get_llm_analysis()` возвращает кортеж:

```python
return {
    "action": action,                    # "increase" | "same" | "decrease"
    "scores": score_obj,                 # Объект Score
    "notes": notes,                      # Внутренние заметки
    "status": status,                    # "confirmed" | "gap"
    "correct_answer": correct_answer,     # Правильный ответ (если есть)
    "hallucination": hallucination,      # bool
    "hallucination_reason": hallucination_reason,
    "off_topic": off_topic,              # bool
    "off_topic_reason": off_topic_reason,
    "stop_intent": stop_intent,          # bool
    "stop_intent_reason": stop_intent_reason,
    "role_reversal": role_reversal,      # bool - кандидат задаёт вопрос интервьюеру
    "role_reversal_reason": role_reversal_reason,
    "suggested_topic": suggested_topic   # Тема для следующего вопроса
}, ""  # Пустая строка = успех, иначе - описание ошибки
```

#### 8. Финальный ответ Interviewer

Observer формирует финальный результат и отправляет в `reply_queue`:

```python
result = {
    "internal_thoughts": "...",           # Внутренние мысли Observer
    "action": action,                     # Используется Interviewer
    "scores": {...},                      # Метрики оценки
    "flags": {
        "hallucination_suspect": bool,
        "off_topic": bool,
        "stop_intent": bool,
        "role_reversal": bool             # Кандидат задаёт вопрос интервьюеру
    },
    "topic": "...",                       # Текущая тема
    "suggested_topic": "...",             # Предложенная тема
    "status": "...",                      # "confirmed" | "gap" | "hallucination_suspect"
    "correct_answer": "...",              # Правильный ответ
    "role_reversal_question": "..."       # Вопрос кандидата (если role_reversal=true)
}
await reply_queue.put(result)
```

### Схема потока данных Observer

```
Interviewer → observer_queue.put({
    "type": "analyze",
    "user_reply": "...",
    "last_question": "...",
    "reply_queue": queue
})
           ↓
Observer.handle() → _get_llm_analysis()
           ↓
LLM.chat(
    system_prompt: "Ты внутренний наблюдатель...",
    user_prompt: "Проанализируй ответ...\nВопрос: {question}\nОтвет: {answer}"
)
           ↓
LLM возвращает JSON
           ↓
Парсинг + валидация
           ↓
reply_queue.put(result)
           ↓
Interviewer получает результат и использует action для генерации следующего вопроса
```

### Особенности Observer

1. **Защита от ошибок:**
   - Retry с экспоненциальной задержкой
   - Cooldown при частых ошибках
   - Fallback на эвристики, если LLM недоступен

2. **Парсинг:**
   - Убирает markdown-обёртки
   - Извлекает JSON из текста
   - Валидирует обязательные поля

3. **Гибкость:**
   - `suggested_topic` позволяет Observer предлагать темы
   - `stop_intent` позволяет корректно завершать интервью
   - `role_reversal` позволяет определять, когда кандидат задаёт вопрос интервьюеру
   - Флаги `hallucination` и `off_topic` для специальной обработки

4. **Определение намерений:**
   - Observer определяет не только качество ответа, но и намерения кандидата
   - `stop_intent` - намерение завершить интервью
   - `role_reversal` - кандидат задаёт вопрос интервьюеру (определяется через LLM, а не простая проверка "?")

---

## Interviewer: Генерация вопросов

### Процесс запроса к Interviewer

#### 1. Инициация запроса

Interviewer получает команды через очередь `inbox`:

**Сценарий A: Начало интервью**
```python
# В orchestrator.py
await interviewer_in.put({"cmd": "start"})
```

**Сценарий B: Ответ кандидата**
```python
# В orchestrator.py, после ввода пользователя
await interviewer_in.put({"user_reply": user_reply})
```

#### 2. Обработка команд (Interviewer.handle)

Interviewer обрабатывает два типа сообщений:

**A. Команда "start"**
```python
if msg.get("cmd") == "start":
    question = self._initial_question()  # Простой шаблон, без LLM
    await self._emit_visible(question)
    return
```

Первый вопрос генерируется без LLM:
```python
def _initial_question(self) -> str:
    position = self.session.meta.get("position", "роль")
    template = self.config["initial_question_template"]
    # "Привет! Начнём. Расскажите о вашем опыте работы {position}."
    return template.format(position=position)
```

**B. Ответ кандидата (user_reply)**

Основной процесс:

1. **Отправка в Observer для анализа:**
```python
reply_queue: asyncio.Queue = asyncio.Queue()
await self.observer_queue.put({
    "type": "analyze",
    "user_reply": user_reply,
    "last_question": last_question,
    "reply_queue": reply_queue,
    "topic": self._topic_from_question(last_question),
})
```

2. **Ожидание результата Observer:**
```python
obs_result = await asyncio.wait_for(reply_queue.get(), timeout=timeout_seconds)
```

3. **Обработка stop_intent:**
```python
stop_intent = obs_result.get("flags", {}).get("stop_intent", False)
if stop_intent:
    await self._emit_internal(obs_result['internal_thoughts'])
    await self.out_user_queue.put({"type": "stop_intent"})
    return
```

4. **Обработка role_reversal (кандидат задаёт вопрос интервьюеру):**
```python
role_reversal = obs_result.get("flags", {}).get("role_reversal", False)
if role_reversal:
    role_reversal_question = obs_result.get("role_reversal_question", user_reply)
    await self._emit_internal(obs_result.get("internal_thoughts", "[Observer]: Кандидат задал вопрос."))
    reply = await self._answer_role_reversal(role_reversal_question)
    await self._emit_visible(reply, record_history=False)
    # После ответа на вопрос кандидата продолжаем интервью - задаём следующий вопрос
    suggested_topic = obs_result.get("suggested_topic", obs_result.get("topic", self.config["default_topic"]))
    question_result = await self._next_question(obs_result.get("action", "same"), suggested_topic)
    # ... генерация и отправка следующего вопроса ...
    return  # Завершаем обработку, не логируем вопрос кандидата как обычный ответ
```

5. **Генерация следующего вопроса:**
```python
suggested_topic = obs_result.get("suggested_topic", ...)
question_result = await self._next_question(obs_result["action"], suggested_topic)
```

#### 3. Генерация вопроса (_generate_question)

Основной метод генерации вопроса через LLM.

**Проверка режима работы:**
```python
if not self.config.get("use_llm_questions", True):
    return self._pick_question()  # Fallback на базовые вопросы из конфига
```

**Подготовка контекста:**

1. **История диалога (`_build_history()`):**
```python
# Берёт последние max_history_turns (по умолчанию 4) ходов
# Формат:
"""
Q: Как работают транзакции в SQL?
A: Транзакции обеспечивают атомарность операций...
Q: Что такое ACID?
A: ACID - это набор свойств...
"""
```

2. **Список заданных вопросов (`_build_asked_questions()`):**
```python
# Формат:
"""
- Как работают транзакции в SQL?
- Что такое ACID?
- В чём разница между списком и кортежем?
"""
```

#### 4. Формирование промпта для LLM

**System Prompt (из конфига):**
```
"Ты интервьюер. Задавай один технический вопрос за раз, следуй рекомендациям Observer и не повторяй уже заданные вопросы."
```

**User Prompt (шаблон с подстановкой):**
```
"Контекст (последние ответы кандидата):
{history}

Уже заданные вопросы:
{asked_questions}

Рекомендация Observer:
- Сложность: {action} (increase/same/decrease)
- Тема для вопроса: {topic}

ВАЖНО: Observer рекомендует задать вопрос по теме '{topic}'. Используй эту тему при генерации вопроса.

Сгенерируй следующий вопрос интервью для позиции {position} уровня {grade}.

Верни СТРОГО JSON без markdown и пояснений:
{\"reasoning\":\"твои внутренние рассуждения о том, какой вопрос ты хочешь задать и почему (1-2 предложения) - НЕ показываются кандидату\",\"question\":\"вопрос для кандидата (1-2 предложения)\"}"
```

**Подстановки в шаблон:**
- `{history}` → последние 4 вопроса/ответа
- `{asked_questions}` → список всех заданных вопросов
- `{action}` → "increase" | "same" | "decrease" (от Observer)
- `{topic}` → тема, предложенная Observer (например, "SQL Transactions")
- `{position}` → позиция кандидата (например, "Backend Developer")
- `{grade}` → грейд кандидата (например, "Junior")

#### 5. Вызов LLM

```python
response = await asyncio.wait_for(
    self.llm.chat(
        system_prompt=self.config["system_prompt"],
        user_prompt=prompt,  # Сформированный промпт
        temperature=0.2  # По умолчанию
    ),
    timeout=llm_timeout,  # По умолчанию 60 секунд
)
```

**Особенности:**
- **Retry**: до `max_question_retries + 1` попыток (по умолчанию 3)
- При повторной попытке добавляется: `"Не повторяй ранее заданные вопросы."`
- При ошибке/таймауте → fallback на `_pick_question()`

#### 6. Парсинг ответа LLM

LLM должен вернуть JSON (если `use_internal_reasoning=True`):

**Ожидаемый формат:**
```json
{
  "reasoning": "Внутренние рассуждения интервьюера (не показываются кандидату)",
  "question": "Вопрос для кандидата (1-2 предложения)"
}
```

**Парсинг и валидация:**
```python
if use_reasoning:
    try:
        raw_data = self._parse_json_response(candidate)  # Убирает markdown обёртки
        # Валидация через Pydantic схему QuestionResponse
        try:
            response = QuestionResponse(**raw_data)
            if response.question and not self._is_repeat(response.question):
                return {"question": response.question, "reasoning": response.reasoning}
        except Exception as validation_error:
            # Если валидация не прошла, пробуем как обычный текст
            pass
    except (ValueError, TypeError, json.JSONDecodeError, KeyError):
        # Если не JSON - пробуем как обычный текст
        pass
```

**Важно:** Interviewer использует **Pydantic схему `QuestionResponse`** для валидации структурированных ответов с рассуждениями.

# Fallback: обычный текст (старый формат или без рассуждений)
if not self._is_repeat(candidate):
    return candidate  # Просто строка с вопросом
```

**Защита от повторов:**
```python
def _is_repeat(self, question: str) -> bool:
    normalized = question.strip().lower()
    for item in self.session.history:
        asked = item.get("question", "").strip().lower()
        if asked and asked == normalized:
            return True
    return False
```

Если вопрос повторяется → следующая попытка или fallback.

#### 7. Возвращаемый результат

`_generate_question()` возвращает:

**Вариант A: JSON с рассуждениями (если `use_internal_reasoning=True`):**
```python
{
    "question": "Объясните, как работает механизм изоляции транзакций в PostgreSQL?",
    "reasoning": "Кандидат хорошо ответил на базовые вопросы про транзакции, Observer рекомендует увеличить сложность. Задам вопрос про изоляцию, чтобы проверить более глубокие знания."
}
```

**Вариант B: Просто строка (если рассуждения отключены или LLM вернул текст):**
```python
"Объясните, как работает механизм изоляции транзакций в PostgreSQL?"
```

**Вариант C: Fallback (если LLM недоступен или все попытки неудачны):**
```python
# Из base_questions по кругу
"Расскажите о вашем опыте."
```

#### 8. Обработка результата

В `_handle_reply()`:

```python
question_result = await self._next_question(obs_result["action"], suggested_topic)

if isinstance(question_result, dict):
    # JSON формат с рассуждениями
    next_question = question_result.get("question", "")
    interviewer_thoughts = f"[Interviewer]: {question_result.get('reasoning', '')}"
    # Логируем внутренние рассуждения (не показываются кандидату)
    if question_result.get("reasoning"):
        await self._emit_internal(f"[Interviewer -> Internal] {question_result['reasoning']}")
else:
    # Просто строка
    next_question = question_result
    interviewer_thoughts = self.config["interviewer_internal_template"].format(
        action=obs_result["action"],
        topic=obs_result.get("topic", self.config["default_topic"]),
    )

# Комбинируем мысли Observer и Interviewer
internal_combined = f"{obs_result['internal_thoughts']} {interviewer_thoughts}"

# Логируем ход
self.session.log_turn(
    agent_visible_message=last_question,
    user_message=user_reply,
    internal_thoughts=internal_combined,
    interviewer_action=obs_result["action"],
    scores=obs_result.get("scores", {}),
)

# Отправляем вопрос кандидату
await self._emit_visible(next_question)
```

#### 9. Специальный случай: Role Reversal

**Определение role reversal теперь делает Observer через LLM**, а не простая проверка "?" в Policy.

**Процесс:**

1. **Observer определяет role_reversal** через LLM-анализ ответа кандидата
2. **Observer возвращает флаг** `role_reversal: true` в результате
3. **Interviewer обрабатывает role_reversal** из результата Observer:

```python
# В Interviewer, после получения результата Observer
role_reversal = obs_result.get("flags", {}).get("role_reversal", False)
if role_reversal:
    role_reversal_question = obs_result.get("role_reversal_question", user_reply)
    await self._emit_internal(obs_result.get("internal_thoughts", "[Observer]: Кандидат задал вопрос."))
    reply = await self._answer_role_reversal(role_reversal_question)
    await self._emit_visible(reply, record_history=False)
    # Продолжаем интервью - задаём следующий вопрос
```

**Генерация ответа на вопрос кандидата:**

```python
async def _answer_role_reversal(self, user_reply: str) -> str:
    prompt = self.config["role_reversal_prompt_template"].format(
        user_question=user_reply
    )
    # "Кандидат задал вопрос: \"{user_question}\". 
    #  Дай краткий ответ (1-2 предложения) и мягко вернись к интервью."
    
    response = await asyncio.wait_for(
        self.llm.chat(self.config["system_prompt"], prompt),
        timeout=llm_timeout,
    )
    return response.strip() or self.policy.role_reversal_reply()  # Fallback из Policy
```

**Преимущества нового подхода:**
- Более точное определение через LLM (различает "не знаю что такое X" vs "что такое X?")
- Контекстный анализ с учётом смысла, а не просто наличие "?"
- Единая точка анализа в Observer

### Схема потока данных Interviewer

```
Orchestrator → interviewer_in.put({"cmd": "start"})
           ↓
Interviewer.handle() → _initial_question()
           ↓
Отправка первого вопроса кандидату
           ↓
Кандидат отвечает
           ↓
Orchestrator → interviewer_in.put({"user_reply": "..."})
           ↓
Interviewer.handle() → _handle_reply()
           ↓
Отправка в Observer для анализа
           ↓
Получение obs_result от Observer
           ↓
Проверка stop_intent → если true, завершение интервью
           ↓
Проверка role_reversal → если true:
           ├─ Генерация ответа через _answer_role_reversal()
           ├─ Отправка ответа кандидату
           └─ Продолжение интервью (генерация следующего вопроса)
           ↓
_next_question(action, suggested_topic)
           ↓
_generate_question()
           ↓
LLM.chat(
    system_prompt: "Ты интервьюер...",
    user_prompt: "Контекст: {history}\nРекомендация: {action}, {topic}..."
)
           ↓
LLM возвращает JSON: {"reasoning": "...", "question": "..."}
           ↓
Парсинг + проверка на повтор
           ↓
Логирование + отправка вопроса кандидату
```

### Особенности Interviewer

1. **Два режима генерации:**
   - С рассуждениями (`use_internal_reasoning=True`) → JSON с `reasoning` и `question`
   - Без рассуждений → просто текст вопроса

2. **Защита от повторов:**
   - Проверка `_is_repeat()` перед отправкой
   - При повторе → retry или fallback

3. **Fallback-механизмы:**
   - При ошибке LLM → базовые вопросы из конфига
   - При таймауте → базовые вопросы
   - При пустом ответе → следующая попытка

4. **Использование рекомендаций Observer:**
   - `action` (increase/same/decrease) влияет на сложность вопроса
   - `suggested_topic` используется как тема для следующего вопроса
   - Observer может направлять интервью через предложения тем

5. **Логирование внутренних мыслей:**
   - Рассуждения Interviewer логируются, но не показываются кандидату
   - Комбинируются с мыслями Observer в `internal_thoughts`
   - Вопрос кандидата при role_reversal не логируется как обычный ответ

6. **Контекст для LLM:**
   - История последних 4 ходов
   - Список всех заданных вопросов
   - Метаданные кандидата (позиция, грейд)
   - Рекомендации Observer (action, suggested_topic)

7. **Structured Output:**
   - Ответы с рассуждениями валидируются через Pydantic схему `QuestionResponse`
   - Гарантированная структура данных
   - Типобезопасность на уровне кода

---

## Сравнительная таблица

| Аспект | Observer | Interviewer |
|--------|----------|-------------|
| **Когда вызывается** | После каждого ответа кандидата | После получения анализа от Observer |
| **System Prompt** | "Ты внутренний наблюдатель..." | "Ты интервьюер..." |
| **Основной вход** | Вопрос + Ответ кандидата | История + Рекомендации Observer |
| **Формат ответа** | JSON с action, scores, flags | JSON с question + reasoning (или просто текст) |
| **Retry логика** | Да, с экспоненциальной задержкой | Да, с проверкой на повторы |
| **Cooldown** | Да (30 сек при ошибках) | Нет |
| **Fallback** | Эвристики из Policy | Базовые вопросы из конфига |
| **Таймаут** | 60 секунд | 60 секунд |
| **Макс. попыток** | 3 (llm_max_retries + 1) | 3 (max_question_retries + 1) |

---

## Manager: Генерация финального отчёта

### Процесс запроса к Manager

#### 1. Инициация запроса (из Orchestrator)

Когда интервью завершается (кандидат хочет закончить или достигнут лимит), Orchestrator отправляет команду финализации:

```python
# В orchestrator.py, после завершения основного цикла интервью
reply_queue: asyncio.Queue = asyncio.Queue()
await manager_in.put({
    "type": "finalize",
    "reply_queue": reply_queue
})
```

#### 2. Обработка запроса (Manager.handle)

Manager получает сообщение и генерирует финальный отчёт:

```python
# В manager.py, метод handle()
if msg.get("type") != "finalize":
    return
reply_queue: asyncio.Queue = msg["reply_queue"]
feedback = await self._generate_feedback()
await reply_queue.put(feedback)
```

#### 3. Подготовка данных для анализа

Перед генерацией отчёта Manager собирает и форматирует данные из сессии:

**A. История диалога (`_format_turns()`):**
```python
# Берёт последние max_turns (по умолчанию 12) ходов
# Формат:
"""
Q: Как работают транзакции в SQL?
A: Транзакции обеспечивают атомарность операций...
Q: Что такое ACID?
A: ACID - это набор свойств...
"""
```

**B. Наблюдения Observer (`_format_observations()`):**
```python
# Формат:
"""
- SQL Transactions | confirmed | correctness=0.85, confidence=0.90 | Кандидат хорошо объяснил...
- Python Data Structures | gap | correctness=0.30, confidence=0.40 | Не знает разницу... | Правильный ответ: Список изменяемый, кортеж нет
- Error Handling | hallucination_suspect | correctness=0.20, confidence=0.25 | Подозрение на галлюцинацию...
"""
```

**C. Статистика (`_calculate_stats()`):**
```python
# Формат:
"""
Статистика: Всего тем=5, Подтверждено=2, Пробелы=2, Галлюцинации=1, 
Средняя correctness=0.55, Средняя confidence=0.60
"""
```

#### 4. Формирование промпта для LLM

**System Prompt (из конфига):**
```
"Ты менеджер по найму. Твоя задача — проанализировать все данные интервью и принять решение о найме. 
Ты должен оценить:
1. Технические навыки (confirmed skills vs knowledge gaps)
2. Наличие галлюцинаций (hallucination_suspect)
3. Соответствие заявленному грейду
4. Soft skills (честность, ясность ответов, вовлечённость)

Правила принятия решения:
- Strong Hire: много confirmed skills, нет/мало gaps, нет галлюцинаций, соответствует или превышает грейд
- Hire: достаточно confirmed skills, есть небольшие gaps, но кандидат честен, соответствует грейду
- No Hire: много gaps, есть галлюцинации, не соответствует грейду, или недостаточно навыков

Верни строгий JSON отчёт без markdown и пояснений."
```

**User Prompt (шаблон с подстановкой):**
```
"Проанализируй интервью и прими решение о найме.

Вводные:
- Позиция: {position}
- Заявленный грейд: {grade}
- Опыт: {experience}

Статистика:
{stats}

Наблюдения Observer по темам:
{observations}

История диалога (вопросы и ответы):
{turns}

Твоя задача:
1. Проанализируй статистику (confirmed vs gaps vs hallucinations)
2. Оцени средние correctness и confidence
3. Проверь наличие галлюцинаций (hallucination_suspect)
4. Оцени соответствие грейду на основе ответов
5. Прими решение: Hire / No Hire / Strong Hire
6. Оцени confidence_score (0-100) на основе количества данных и их качества

Верни СТРОГО JSON без markdown, префиксов и пояснений:
{\"verdict\":{\"grade\":\"Junior|Middle|Senior\",\"recommendation\":\"Hire|No Hire|Strong Hire\",\"confidence_score\":0-100},\"technical_review\":{\"topics\":[{\"topic\":\"...\",\"status\":\"confirmed|gap|hallucination_suspect\",\"notes\":\"...\",\"correct_answer\":\"...\"}],\"confirmed_skills\":[\"...\"],\"knowledge_gaps\":[\"...\"]},\"soft_skills\":{\"clarity\":\"Good|Average|Poor\",\"honesty\":\"Clear answers|Admitted gaps|Unclear\",\"engagement\":\"High|Neutral|Low\"},\"personal_roadmap\":[{\"topic\":\"...\",\"resources\":[\"...\"]}]}"
```

**Подстановки в шаблон:**
- `{position}` → позиция кандидата (например, "Backend Developer")
- `{grade}` → заявленный грейд (например, "Junior")
- `{experience}` → опыт кандидата
- `{stats}` → статистика (подтверждено/пробелы/галлюцинации/средние значения)
- `{observations}` → все наблюдения Observer по темам
- `{turns}` → история диалога (последние 12 вопросов/ответов)

#### 5. Вызов LLM

```python
response = await asyncio.wait_for(
    self.llm.chat(
        system_prompt=self.config["system_prompt"],
        user_prompt=prompt,  # Сформированный промпт с данными интервью
        temperature=0.2  # По умолчанию
    ),
    timeout=timeout,  # По умолчанию 60 секунд
)
```

**Особенности:**
- **Таймаут**: `llm_timeout_seconds` (по умолчанию 60)
- **Нет retry**: только одна попытка (Manager вызывается один раз в конце)
- **Fallback**: при ошибке/таймауте используется `session.build_final_feedback()`

#### 6. Парсинг ответа LLM

LLM должен вернуть JSON. Парсинг:

```python
parsed = self._parse_json_response(response)  # Убирает markdown обёртки
```

**Ожидаемая структура JSON:**

```json
{
  "verdict": {
    "grade": "Junior" | "Middle" | "Senior",
    "recommendation": "Hire" | "No Hire" | "Strong Hire",
    "confidence_score": 0-100
  },
  "technical_review": {
    "topics": [
      {
        "topic": "SQL Transactions",
        "status": "confirmed" | "gap" | "hallucination_suspect",
        "notes": "Детальное описание...",
        "correct_answer": "Правильный ответ (если есть пробел)"
      }
    ],
    "confirmed_skills": ["SQL Transactions", "Python Basics"],
    "knowledge_gaps": ["Django ORM", "Error Handling"]
  },
  "soft_skills": {
    "clarity": "Good" | "Average" | "Poor",
    "honesty": "Clear answers" | "Admitted gaps" | "Unclear",
    "engagement": "High" | "Neutral" | "Low"
  },
  "personal_roadmap": [
    {
      "topic": "Django ORM",
      "resources": ["https://docs.djangoproject.com/", "Django ORM tutorial"]
    }
  ]
}
```

#### 7. Валидация и обработка ошибок

```python
try:
    raw_data = self._parse_json_response(response)
    # Валидация через Pydantic схему FinalReport
    try:
        report = FinalReport(**raw_data)
        return report.model_dump()
    except Exception as validation_error:
        print(f"Manager validation error: {validation_error}. Using fallback.")
        return self.session.build_final_feedback()
except (ValueError, TypeError, json.JSONDecodeError) as exc:
    print(f"Manager JSON parse error: {exc}. Using fallback.")
    return self.session.build_final_feedback()  # Fallback на эвристический отчёт
```

**Важно:** Manager использует **Pydantic схему `FinalReport`** для валидации финального отчёта. Это гарантирует корректную структуру данных и типобезопасность.

**Fallback механизм:**
- При ошибке парсинга → `session.build_final_feedback()`
- При ошибке валидации → `session.build_final_feedback()`
- При таймауте → `session.build_final_feedback()`
- При любой другой ошибке → `session.build_final_feedback()`

Fallback создаёт базовый отчёт на основе наблюдений Observer без LLM-анализа.

#### 8. Возвращаемый результат

`_generate_feedback()` возвращает словарь:

```python
{
    "verdict": {
        "grade": "Junior" | "Middle" | "Senior",
        "recommendation": "Hire" | "No Hire" | "Strong Hire",
        "confidence_score": 0-100
    },
    "technical_review": {
        "topics": [...],  # Детальный разбор по темам
        "confirmed_skills": [...],  # Список подтверждённых навыков
        "knowledge_gaps": [...]  # Список пробелов в знаниях
    },
    "soft_skills": {
        "clarity": "Good" | "Average" | "Poor",
        "honesty": "Clear answers" | "Admitted gaps" | "Unclear",
        "engagement": "High" | "Neutral" | "Low"
    },
    "personal_roadmap": [
        {
            "topic": "...",
            "resources": ["..."]
        }
    ]
}
```

#### 9. Обработка результата в Orchestrator

```python
# В orchestrator.py
reply_queue: asyncio.Queue = asyncio.Queue()
await manager_in.put({"type": "finalize", "reply_queue": reply_queue})

manager_timeout = float(runtime_config.get("manager", {}).get("llm_timeout_seconds", 25))
try:
    final_feedback = await asyncio.wait_for(reply_queue.get(), timeout=manager_timeout + 5)
except asyncio.TimeoutError:
    final_feedback = session.build_final_feedback()  # Fallback при таймауте

session.set_final_feedback(final_feedback)
_print_final_report(final_feedback, colors)  # Вывод в консоль
session.save(str(log_path))  # Сохранение в JSON
```

### Схема потока данных Manager

```
Интервью завершено
           ↓
Orchestrator → manager_in.put({
    "type": "finalize",
    "reply_queue": queue
})
           ↓
Manager.handle() → _generate_feedback()
           ↓
Сбор данных из SessionLogger:
- _format_turns() (последние 12 ходов)
- _format_observations() (все наблюдения)
- _calculate_stats() (статистика)
           ↓
LLM.chat(
    system_prompt: "Ты менеджер по найму...",
    user_prompt: "Проанализируй интервью...\nСтатистика: {stats}\n..."
)
           ↓
LLM возвращает JSON с финальным отчётом
           ↓
Парсинг + валидация
           ↓
reply_queue.put(feedback)
           ↓
Orchestrator получает результат
           ↓
Вывод отчёта в консоль + сохранение в logs/interview_log.json
```

### Особенности Manager

1. **Однократный вызов:**
   - Manager вызывается только один раз в конце интервью
   - Нет retry-логики (только одна попытка)
   - Нет cooldown (не нужен)

2. **Агрегация данных:**
   - Собирает данные из всей сессии интервью
   - Форматирует наблюдения Observer
   - Вычисляет статистику

3. **Fallback механизм:**
   - При любой ошибке используется `session.build_final_feedback()`
   - Гарантирует наличие отчёта даже при сбоях LLM
   - Fallback создаёт базовый отчёт на основе наблюдений

4. **Структурированный отчёт:**
   - Вердикт (грейд, рекомендация, уверенность)
   - Технический обзор (навыки, пробелы, детали по темам)
   - **Валидация через Pydantic схему `FinalReport`**
   - Автоматическая проверка типов и структуры данных

5. **Structured Output:**
   - Все отчёты валидируются через Pydantic схему `FinalReport`
   - Гарантированная структура данных (verdict, technical_review, soft_skills, personal_roadmap)
   - Типобезопасность на уровне кода
   - Понятные сообщения об ошибках валидации
   - Soft skills (ясность, честность, вовлечённость)
   - Персональный roadmap (рекомендации для развития)

5. **Контекст для LLM:**
   - Вся история интервью (последние 12 ходов)
   - Все наблюдения Observer
   - Статистика по темам
   - Метаданные кандидата

6. **Принятие решения:**
   - LLM анализирует все данные и принимает решение о найме
   - Учитывает технические навыки, галлюцинации, соответствие грейду
   - Оценивает soft skills на основе поведения кандидата

---

## Сравнительная таблица

| Аспект | Observer | Interviewer | Manager |
|--------|----------|-------------|---------|
| **Когда вызывается** | После каждого ответа кандидата | После получения анализа от Observer | Один раз в конце интервью |
| **System Prompt** | "Ты внутренний наблюдатель..." | "Ты интервьюер..." | "Ты менеджер по найму..." |
| **Основной вход** | Вопрос + Ответ кандидата | История + Рекомендации Observer | Вся сессия (turns + observations + stats) |
| **Формат ответа** | JSON с action, scores, flags | JSON с question + reasoning (или просто текст) | JSON с финальным отчётом (verdict, technical_review, soft_skills, roadmap) |
| **Retry логика** | Да, с экспоненциальной задержкой | Да, с проверкой на повторы | Нет (одна попытка) |
| **Cooldown** | Да (30 сек при ошибках) | Нет | Нет |
| **Fallback** | Эвристики из Policy | Базовые вопросы из конфига | session.build_final_feedback() |
| **Таймаут** | 60 секунд | 60 секунд | 60 секунд |
| **Макс. попыток** | 3 (llm_max_retries + 1) | 3 (max_question_retries + 1) | 1 |
| **Частота вызовов** | Множественная (каждый ответ) | Множественная (каждый ход) | Однократная (финализация) |

---

## Конфигурация

Все промпты и настройки находятся в `config/runtime.json`:

- **Observer**: секция `"observer"`
  - `analysis_system_prompt`
  - `analysis_json_prompt_template`
  - `llm_timeout_seconds`
  - `llm_max_retries`
  - `llm_cooldown_seconds`

- **Interviewer**: секция `"interviewer"`
  - `system_prompt`
  - `question_prompt_template`
  - `role_reversal_prompt_template`
  - `use_internal_reasoning`
  - `use_llm_questions`
  - `max_question_retries`
  - `llm_timeout_seconds`
  - `base_questions` (fallback)

- **Manager**: секция `"manager"`
  - `system_prompt`
  - `report_prompt_template`
  - `max_turns` (сколько последних ходов включать)
  - `llm_timeout_seconds`

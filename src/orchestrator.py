import argparse
import asyncio
import json
from pathlib import Path
from typing import Any, Dict

from src.agents.interviewer import Interviewer
from src.agents.manager import Manager
from src.agents.observer import Observer
from src.config import load_config
from src.llm import get_llm_client
from src.policy import Policy
from src.rag import RAGRetriever
from src.session import SessionLogger
from src.session_id_manager import get_session_id_string


def _print_final_report(feedback: Dict[str, Any], colors: Dict[str, str]) -> None:
    """Выводит финальный отчёт Manager в консоль."""
    verdict = feedback.get("verdict", {})
    technical = feedback.get("technical_review", {})
    soft_skills = feedback.get("soft_skills", {})
    roadmap = feedback.get("personal_roadmap", [])
    
    print(f"\n{colors['internal']}{'='*60}{colors['reset']}")
    print(f"{colors['interviewer']}{'ФИНАЛЬНЫЙ ОТЧЁТ МЕНЕДЖЕРА':^60}{colors['reset']}")
    print(f"{colors['internal']}{'='*60}{colors['reset']}\n")
    
    # Вердикт
    grade = verdict.get("grade", "N/A")
    recommendation = verdict.get("recommendation", "N/A")
    confidence = verdict.get("confidence_score", 0)
    
    rec_color = colors["interviewer"]
    if recommendation == "Strong Hire":
        rec_color = "\x1b[92m"  # Green
    elif recommendation == "Hire":
        rec_color = "\x1b[93m"  # Yellow
    else:
        rec_color = "\x1b[91m"  # Red
    
    print(f"{colors['interviewer']}ВЕРДИКТ:{colors['reset']}")
    print(f"  Грейд: {grade}")
    print(f"  Рекомендация: {rec_color}{recommendation}{colors['reset']}")
    print(f"  Уверенность: {confidence}%\n")
    
    # Технический обзор
    print(f"{colors['interviewer']}ТЕХНИЧЕСКИЙ ОБЗОР:{colors['reset']}")
    confirmed = technical.get("confirmed_skills", [])
    gaps = technical.get("knowledge_gaps", [])
    topics = technical.get("topics", [])
    
    if confirmed:
        print(f"  {colors['interviewer']}Подтверждённые навыки:{colors['reset']}")
        for skill in confirmed:
            print(f"    • {skill}")
    
    if gaps:
        print(f"  {colors['interviewer']}Пробелы в знаниях:{colors['reset']}")
        for gap in gaps:
            print(f"    • {gap}")
    
    if topics:
        print(f"\n  {colors['interviewer']}Детали по темам:{colors['reset']}")
        for topic in topics:
            topic_name = topic.get("topic", "N/A")
            status = topic.get("status", "unknown")
            notes = topic.get("notes", "")
            correct = topic.get("correct_answer", "")
            
            status_icon = "✅" if status == "confirmed" else "❌" if status == "gap" else "⚠️"
            print(f"    {status_icon} {topic_name} ({status})")
            if notes:
                print(f"      {notes[:100]}{'...' if len(notes) > 100 else ''}")
            if correct:
                print(f"      {colors['internal']}Правильный ответ: {correct[:80]}{'...' if len(correct) > 80 else ''}{colors['reset']}")
    
    print()
    
    # Soft skills
    print(f"{colors['interviewer']}SOFT SKILLS:{colors['reset']}")
    print(f"  Ясность: {soft_skills.get('clarity', 'N/A')}")
    print(f"  Честность: {soft_skills.get('honesty', 'N/A')}")
    print(f"  Вовлечённость: {soft_skills.get('engagement', 'N/A')}\n")
    
    # Roadmap
    if roadmap:
        print(f"{colors['interviewer']}ПЕРСОНАЛЬНЫЙ ROADMAP:{colors['reset']}")
        for item in roadmap:
            topic = item.get("topic", "N/A")
            resources = item.get("resources", [])
            print(f"  • {topic}")
            for resource in resources:
                print(f"    - {resource}")
    
    print(f"\n{colors['internal']}{'='*60}{colors['reset']}\n")


async def run_interview(meta: Dict[str, str], team_name: str, config_path: str) -> None:
    runtime_config = load_config(config_path)
    policy = Policy(runtime_config["policy"])
    
    # Получаем инкрементируемый session_id
    logs_dir = Path("logs")
    session_id = get_session_id_string(logs_dir=logs_dir)
    
    session = SessionLogger(
        team_name=team_name,
        meta=meta,
        feedback_config=runtime_config["final_feedback"],
        default_topic=runtime_config["interviewer"]["default_topic"],
        session_id=session_id,
    )
    interviewer_in: asyncio.Queue = asyncio.Queue()
    observer_in: asyncio.Queue = asyncio.Queue()
    manager_in: asyncio.Queue = asyncio.Queue()
    user_out: asyncio.Queue = asyncio.Queue()

    llm = get_llm_client()
    
    # Инициализируем RAG, если включен в конфигурации
    rag = None
    observer_config = runtime_config.get("observer", {})
    rag_config = observer_config.get("rag", {})
    if rag_config.get("enabled", False):
        try:
            rag = RAGRetriever(
                model_name=rag_config.get("model_name", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"),
                index_path=rag_config.get("index_path"),
                data_path=rag_config.get("data_path"),
                base_dir=rag_config.get("base_dir"),
            )
        except Exception as e:
            print(f"Предупреждение: Не удалось инициализировать RAG: {e}")
            print("Продолжаем работу без RAG.")
            rag = None
    
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
        rag=rag,
    )
    manager = Manager(
        manager_in,
        llm,
        session,
        runtime_config["manager"],
    )

    interviewer_task = asyncio.create_task(interviewer.start())
    observer_task = asyncio.create_task(observer.start())
    manager_task = asyncio.create_task(manager.start())

    await interviewer_in.put({"cmd": "start"})

    colors = {
        "reset": "\x1b[0m",
        "interviewer": "\x1b[96m",
        "internal": "\x1b[90m",
        "user": "\x1b[92m",
    }
    labels = {
        "interviewer": "Interviewer",
        "internal": "Internal",
        "user": "USER",
    }

    while True:
        message = await user_out.get()
        msg_type = message.get("type", "visible")
        text = message.get("text", "")
        
        if msg_type == "internal":
            print(f"{colors['internal']}{labels['internal']}: {text}{colors['reset']}")
            continue
        
        if msg_type == "stop_intent":
            break

        print(f"{colors['interviewer']}{labels['interviewer']}: {text}{colors['reset']}")
        user_reply = input(f"{colors['user']}{labels['user']}> {colors['reset']}").strip()
        await interviewer_in.put({"user_reply": user_reply})

    print(f"\n{colors['internal']}{'='*60}{colors['reset']}")
    print(f"{colors['internal']}Генерация финального отчёта...{colors['reset']}\n")
    
    reply_queue: asyncio.Queue = asyncio.Queue()
    await manager_in.put({"type": "finalize", "reply_queue": reply_queue})
    manager_timeout = float(runtime_config.get("manager", {}).get("llm_timeout_seconds", 25))
    try:
        final_feedback = await asyncio.wait_for(reply_queue.get(), timeout=manager_timeout + 5)
    except asyncio.TimeoutError:
        final_feedback = session.build_final_feedback()
    session.set_final_feedback(final_feedback)
    
    _print_final_report(final_feedback, colors)
    
    # Создаём папку logs, если её нет
    logs_dir = Path("logs")
    logs_dir.mkdir(exist_ok=True)
    
    # Сохраняем лог в папку logs с session_id в имени файла
    log_path = logs_dir / f"interview_log_{session_id}.json"
    session.save(str(log_path))
    print(f"Лог сохранен: {log_path}")
    await interviewer_in.put(None)
    await observer_in.put(None)
    await manager_in.put(None)
    await interviewer_task
    await observer_task
    await manager_task


def load_input_data(input_path: str) -> Dict[str, Any]:
    """Загружает входные данные из JSON файла."""
    path = Path(input_path)
    if not path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")
    
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    
    candidate = data.get("candidate", {})
    meta = {
        "name": candidate.get("name", ""),
        "position": candidate.get("position", "Backend Developer"),
        "grade": candidate.get("grade", "Junior"),
        "experience": candidate.get("experience", "N/A"),
    }
    
    return {
        "team_name": data.get("team_name", "Team Alpha"),
        "meta": meta,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Multi-Agent Interview Coach")
    parser.add_argument(
        "--input",
        default="input.json",
        help="Path to JSON file with candidate data (default: input.json)",
    )
    parser.add_argument(
        "--config",
        default="config/runtime.json",
        help="Path to runtime config (default: config/runtime.json)",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    try:
        input_data = load_input_data(args.input)
    except FileNotFoundError as exc:
        print(f"Error: {exc}")
        print("\nExample input.json:")
        print(json.dumps({
            "team_name": "Team Alpha",
            "candidate": {
                "name": "Алекс",
                "position": "Backend Developer",
                "grade": "Junior",
                "experience": "Пет-проекты на Django, немного SQL"
            }
        }, ensure_ascii=False, indent=2))
        return
    except json.JSONDecodeError as exc:
        print(f"Error: Invalid JSON in input file: {exc}")
        return
    
    asyncio.run(run_interview(input_data["meta"], input_data["team_name"], args.config))


if __name__ == "__main__":
    main()

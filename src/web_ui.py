import asyncio
import json
import threading
from pathlib import Path
from typing import Any, Dict, Optional

from flask import Flask, render_template, request, jsonify, session as flask_session
from flask_session import Session

from src.agents.interviewer import Interviewer
from src.agents.manager import Manager
from src.agents.observer import Observer
from src.config import load_config
from src.llm import get_llm_client
from src.policy import Policy
from src.session import SessionLogger
from src.session_id_manager import get_session_id_string

import os
BASE_DIR = Path(__file__).parent.parent
TEMPLATE_DIR = BASE_DIR / "templates"
STATIC_DIR = BASE_DIR / "static"

app = Flask(
    __name__,
    template_folder=str(TEMPLATE_DIR),
    static_folder=str(STATIC_DIR)
)
app.secret_key = "megaschool-secret-key-change-in-production"
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
Session(app)

active_interviews: Dict[str, Dict[str, Any]] = {}
web_queues: Dict[str, Dict[str, asyncio.Queue]] = {}


async def run_interview_async(
    session_id: str,
    meta: Dict[str, str],
    team_name: str,
    config_path: str,
    message_queue: asyncio.Queue,
    response_queue: asyncio.Queue,
) -> None:
    """Асинхронная функция для запуска интервью."""
    try:
        runtime_config = load_config(config_path)
        policy = Policy(runtime_config["policy"])
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

        active_interviews[session_id] = {
            "interviewer_in": interviewer_in,
            "user_out": user_out,
            "manager_in": manager_in,
            "session": session,
            "interviewer_task": interviewer_task,
            "observer_task": observer_task,
            "manager_task": manager_task,
            "runtime_config": runtime_config,
            "status": "running",
        }

        web_queues[session_id] = {
            "message_queue": message_queue,
            "response_queue": response_queue,
        }

        while True:
            try:
                try:
                    user_message = message_queue.get_nowait()
                    if user_message is None:
                        break
                    await interviewer_in.put({"user_reply": user_message})
                except asyncio.QueueEmpty:
                    pass

                try:
                    message = await asyncio.wait_for(user_out.get(), timeout=0.1)
                    msg_type = message.get("type", "visible")
                    text = message.get("text", "")

                    if msg_type == "stop_intent":
                        break

                    await response_queue.put(
                        {
                            "type": msg_type,
                            "text": text,
                        }
                    )
                except asyncio.TimeoutError:
                    pass

                await asyncio.sleep(0.1)

            except Exception as e:
                await response_queue.put({"type": "error", "text": str(e)})
                break

        active_interviews[session_id]["status"] = "finalizing"
        await response_queue.put({"type": "status", "text": "Генерация финального отчёта..."})

        reply_queue: asyncio.Queue = asyncio.Queue()
        await manager_in.put({"type": "finalize", "reply_queue": reply_queue})
        
        web_timeout = 25.0
        
        try:
            final_feedback = await asyncio.wait_for(reply_queue.get(), timeout=web_timeout)
            if not final_feedback:
                raise ValueError("Manager вернул пустой ответ")
        except asyncio.TimeoutError:
            await response_queue.put({"type": "status", "text": "Используется упрощённая версия отчёта..."})
            await asyncio.sleep(0.3)
            final_feedback = session.build_final_feedback()
        except Exception as e:
            await response_queue.put({"type": "status", "text": "Используется упрощённая версия отчёта..."})
            await asyncio.sleep(0.3)
            final_feedback = session.build_final_feedback()
        
        session.set_final_feedback(final_feedback)
        await response_queue.put({"type": "final_report", "data": final_feedback})

        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        log_path = logs_dir / f"interview_log_{session_id}.json"
        session.save(str(log_path))

        await interviewer_in.put(None)
        await observer_in.put(None)
        await manager_in.put(None)
        await interviewer_task
        await observer_task
        await manager_task

        active_interviews[session_id]["status"] = "completed"
        await response_queue.put({"type": "completed"})

    except Exception as e:
        if session_id in active_interviews:
            active_interviews[session_id]["status"] = "error"
        if session_id in web_queues:
            await web_queues[session_id]["response_queue"].put({"type": "error", "text": str(e)})


def run_interview_thread(
    session_id: str,
    meta: Dict[str, str],
    team_name: str,
    config_path: str,
    message_queue: asyncio.Queue,
    response_queue: asyncio.Queue,
) -> None:
    """Запускает интервью в отдельном потоке."""
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(
        run_interview_async(session_id, meta, team_name, config_path, message_queue, response_queue)
    )
    loop.close()


@app.route("/")
def index():
    """Главная страница с формой ввода данных кандидата."""
    return render_template("index.html")


@app.route("/api/start", methods=["POST"])
def start_interview():
    """Запускает новое интервью."""
    data = request.json
    candidate = data.get("candidate", {})
    team_name = data.get("team_name", "Team Alpha")
    config_path = data.get("config", "config/runtime.json")

    meta = {
        "name": candidate.get("name", ""),
        "position": candidate.get("position", "Backend Developer"),
        "grade": candidate.get("grade", "Junior"),
        "experience": candidate.get("experience", "N/A"),
    }

    logs_dir = Path("logs")
    session_id = get_session_id_string(logs_dir=logs_dir)
    flask_session["session_id"] = session_id

    message_queue: asyncio.Queue = asyncio.Queue()
    response_queue: asyncio.Queue = asyncio.Queue()

    thread = threading.Thread(
        target=run_interview_thread,
        args=(session_id, meta, team_name, config_path, message_queue, response_queue),
        daemon=True,
    )
    thread.start()

    return jsonify({"session_id": session_id, "status": "started"})


@app.route("/api/message", methods=["POST"])
def send_message():
    """Отправляет сообщение пользователя в интервью."""
    data = request.json
    message = data.get("message", "")
    session_id = flask_session.get("session_id")

    if not session_id or session_id not in web_queues:
        return jsonify({"error": "No active interview"}), 400

    try:
        message_queue = web_queues[session_id]["message_queue"]
        message_queue.put_nowait(message)
        return jsonify({"status": "sent"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/poll", methods=["GET"])
def poll_messages():
    """Получает новые сообщения от интервьюера."""
    session_id = flask_session.get("session_id")
    
    if not session_id or session_id not in web_queues:
        return jsonify({"messages": []})

    response_queue = web_queues[session_id]["response_queue"]
    messages = []
    try:
        while True:
            msg = response_queue.get_nowait()
            messages.append(
                {
                    "type": msg.get("type", "visible"),
                    "text": msg.get("text", ""),
                    "data": msg.get("data"),
                }
            )
    except asyncio.QueueEmpty:
        pass

    return jsonify({"messages": messages})


@app.route("/api/stop", methods=["POST"])
def stop_interview():
    """Останавливает интервью."""
    session_id = flask_session.get("session_id")
    
    if session_id and session_id in web_queues:
        try:
            message_queue = web_queues[session_id]["message_queue"]
            message_queue.put_nowait(None)
        except Exception:
            pass

    return jsonify({"status": "stopped"})


if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)

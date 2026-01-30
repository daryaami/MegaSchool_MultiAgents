"""Утилита для управления инкрементируемым session_id."""
import json
import re
from pathlib import Path
from typing import Optional


def _find_max_existing_session_id(logs_dir: Path) -> int:
    if not logs_dir.exists():
        return 0
    
    max_id = 0
    pattern = re.compile(r"interview_log_0*(\d+)\.json")
    
    for file_path in logs_dir.glob("interview_log_*.json"):
        match = pattern.match(file_path.name)
        if match:
            try:
                file_id = int(match.group(1))
                max_id = max(max_id, file_id)
            except ValueError:
                continue
    
    return max_id


def get_next_session_id(logs_dir: Path = Path("logs")) -> int:
    logs_dir.mkdir(exist_ok=True)
    max_id = _find_max_existing_session_id(logs_dir)
    next_id = max_id + 1
    return next_id


def get_session_id_string(session_id: Optional[int] = None, logs_dir: Path = Path("logs")) -> str:
    if session_id is None:
        session_id = get_next_session_id(logs_dir)
    return str(session_id)

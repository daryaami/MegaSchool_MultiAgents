"""
Модуль RAG (Retrieval-Augmented Generation) для поиска релевантных вопросов и ответов
из базы данных интервью.
"""
import os
from pathlib import Path
from typing import List, Optional, Dict, Any
import pandas as pd
import faiss
import numpy as np
from sentence_transformers import SentenceTransformer


class RAGRetriever:
    """
    Класс для поиска релевантных вопросов и ответов из базы данных интервью.
    Использует FAISS для быстрого поиска по эмбеддингам.
    """
    
    def __init__(
        self,
        model_name: str = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
        index_path: Optional[str] = None,
        data_path: Optional[str] = None,
        base_dir: Optional[str] = None,
    ):
        """
        Инициализирует RAG retriever.
        
        Args:
            model_name: Название модели sentence-transformers для эмбеддингов
            index_path: Путь к FAISS индексу (по умолчанию: base_dir/data/rag/faiss_index.bin)
            data_path: Путь к данным в формате pickle (по умолчанию: base_dir/data/rag/data.pkl)
            base_dir: Базовая директория для поиска файлов (по умолчанию: корень проекта)
        """
        if base_dir is None:
            base_dir = Path(__file__).parent.parent
        
        base_dir = Path(base_dir)
        default_rag_dir = base_dir / "data" / "rag"
        
        if index_path is None:
            index_path = default_rag_dir / "faiss_index.bin"
        else:
            index_path = Path(index_path)
        
        if data_path is None:
            data_path = default_rag_dir / "data.pkl"
        else:
            data_path = Path(data_path)
        
        self.index_path = index_path
        self.data_path = data_path
        
        self.model = None
        try:
            print(f"Загрузка модели {model_name}...")
            print("Примечание: при первом запуске модель скачивается (~471MB), это может занять время.")
            print("При последующих запусках модель загружается из кэша быстро.")
            self.model = SentenceTransformer(model_name)
            print(f"Модель {model_name} успешно загружена.")
        except Exception as e:
            print(f"Ошибка при загрузке модели {model_name}: {e}")
            print("RAG будет отключен. Приложение продолжит работу без RAG.")
            self.model = None
        
        self.index = None
        self.data = None
        self._load_index_and_data()
    
    def _load_index_and_data(self) -> None:
        if not self.index_path.exists():
            print(f"Предупреждение: FAISS индекс не найден по пути {self.index_path}")
            print(f"RAG будет отключен. Поместите файл faiss_index.bin в директорию {self.index_path.parent}")
            print(f"Или укажите правильный путь в конфигурации (config/runtime.json -> observer.rag.index_path)")
            return
        
        if not self.data_path.exists():
            print(f"Предупреждение: Данные не найдены по пути {self.data_path}")
            print(f"RAG будет отключен. Поместите файл data.pkl в директорию {self.data_path.parent}")
            print(f"Или укажите правильный путь в конфигурации (config/runtime.json -> observer.rag.data_path)")
            return
        
        try:
            self.index = faiss.read_index(str(self.index_path))
            print(f"Загружен FAISS индекс из {self.index_path}")
            
            self.data = pd.read_pickle(self.data_path)
            print(f"Загружены данные из {self.data_path} ({len(self.data)} записей)")
        except Exception as e:
            print(f"Ошибка при загрузке RAG данных: {e}")
            print("RAG будет отключен.")
            self.index = None
            self.data = None
    
    def search(
        self,
        query: str,
        top_k: int = 5,
        min_relevance: float = 0.6,
    ) -> List[Dict[str, Any]]:
        """
        Ищет релевантные вопросы и ответы для заданного запроса.
        
        Args:
            query: Текст запроса (вопрос или ответ кандидата)
            top_k: Количество результатов для возврата
            min_relevance: Минимальный порог релевантности (0.0-1.0)
        
        Returns:
            Список словарей с релевантными вопросами и ответами, каждый содержит:
            - Category: категория вопроса
            - Skill: навык
            - Level: уровень
            - Question: вопрос
            - Answer: ответ
            - relevance: релевантность (0.0-1.0)
        """
        if self.model is None or self.index is None or self.data is None:
            return []
        
        try:
            query_vec = self.model.encode([query], show_progress_bar=False)
            faiss.normalize_L2(query_vec)
            distances, indices = self.index.search(query_vec, top_k)
            
            results = []
            for i, (distance, idx) in enumerate(zip(distances[0], indices[0])):
                distance_float = float(distance)
                if distance_float >= min_relevance:
                    row = self.data.iloc[idx]
                    results.append({
                        "Category": str(row.get("Category", "")),
                        "Skill": str(row.get("Skill", "")),
                        "Level": str(row.get("Level", "")),
                        "Question": str(row.get("Question", "")),
                        "Answer": str(row.get("Answer", "")),
                        "relevance": distance_float,
                    })
                else:
                    pass
            
            return results
        except Exception as e:
            return []
    
    def format_reference_materials(self, results: List[Dict[str, Any]]) -> str:
        """
        Форматирует результаты поиска в текстовый формат для использования в промпте.
        
        Args:
            results: Список результатов поиска
        
        Returns:
            Отформатированная строка с опорными материалами
        """
        if not results:
            return ""
        
        lines = ["ОПОРНЫЕ МАТЕРИАЛЫ ИЗ БАЗЫ ДАННЫХ ИНТЕРВЬЮ:"]
        lines.append("")
        lines.append("ВАЖНО: Используй эти материалы ТОЛЬКО если они релевантны теме ответа кандидата (кандидат упоминал соответствующие технологии/концепции). Если материалы не соответствуют тому, о чем говорит кандидат - ИГНОРИРУЙ их при оценке!")
        lines.append("Эти материалы могут помочь оценить, насколько правильный и полный ответ кандидата. НО: не предлагай темы на основе этих материалов, если кандидат их не упоминал! Предлагай темы только на основе того, что кандидат РЕАЛЬНО сказал в своем ответе.")
        lines.append("")
        
        for i, result in enumerate(results, 1):
            lines.append(f"{i}. Категория: {result['Category']} | Навык: {result['Skill']} | Уровень: {result['Level']}")
            lines.append(f"   Вопрос: {result['Question']}")
            lines.append(f"   Ожидаемый ответ: {result['Answer']}")
            lines.append(f"   Релевантность: {result['relevance']:.2f}")
            lines.append("")
        
        return "\n".join(lines)
    
    def is_available(self) -> bool:
        """Проверяет, доступен ли RAG (загружены ли модель, индекс и данные)."""
        return self.model is not None and self.index is not None and self.data is not None

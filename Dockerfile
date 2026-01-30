FROM python:3.12-slim

WORKDIR /app

# Установка системных зависимостей
RUN apt-get update && apt-get install -y \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Копирование файлов зависимостей
COPY requirements.txt .

# Установка Python зависимостей
RUN pip install --no-cache-dir -r requirements.txt

# Копирование всего проекта
COPY . .

# Создание необходимых директорий
RUN mkdir -p logs flask_session

# Открытие порта
EXPOSE 5000

# Переменные окружения по умолчанию
ENV FLASK_APP=run_web.py
ENV FLASK_ENV=production
ENV PYTHONUNBUFFERED=1

# Команда запуска
CMD ["python", "run_web.py"]

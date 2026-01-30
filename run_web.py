#!/usr/bin/env python3
"""Запуск веб-интерфейса для MegaSchool Interview Coach."""

from src.web_ui import app

if __name__ == "__main__":
    print("=" * 60)
    print("MegaSchool Interview Coach - Web UI")
    print("=" * 60)
    print("\nОткройте в браузере: http://localhost:5000")
    print("\nДля остановки нажмите Ctrl+C\n")
    app.run(debug=True, host="0.0.0.0", port=5000)

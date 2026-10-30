@echo off
chcp 65001 >nul
cd /d %~dp0
if not exist venv (
  echo Первый запуск: устанавливаю нужное, подожди пару минут...
  python -m venv venv
  call venv\Scripts\activate
  pip install -r requirements.txt
) else (
  call venv\Scripts\activate
)
python bot.py
pause

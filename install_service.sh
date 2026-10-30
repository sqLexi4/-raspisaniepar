#!/bin/bash
# Запускать НА СЕРВЕРЕ из папки проекта:  bash install_service.sh
set -e
cd "$(dirname "$0")"
DIR=$(pwd); USR=$(whoami)
grep -q "^BOT_TOKEN=[0-9]" settings.txt || { echo "❌ Вставь токен в settings.txt (nano settings.txt)"; exit 1; }
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
sudo tee /etc/systemd/system/schedule-bot.service >/dev/null <<UNIT
[Unit]
Description=Telegram Schedule Bot
After=network-online.target
Wants=network-online.target

[Service]
User=$USR
WorkingDirectory=$DIR
ExecStart=$DIR/venv/bin/python bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now schedule-bot
sleep 2
sudo systemctl status schedule-bot --no-pager
echo "✅ Готово. Бот работает и будет сам запускаться после перезагрузки."

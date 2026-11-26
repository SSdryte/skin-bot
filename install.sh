#!/bin/bash
# Ставит бота на Ubuntu/Debian как службу: запускается сам после перезагрузки и падений.
# Вызывается из deploy.ps1; повторный запуск = обновление (настройки и state.json сохраняются).
set -e
cd /opt/skinbot
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq && apt-get install -y -qq python3-venv >/dev/null
[ -d venv ] || python3 -m venv venv
venv/bin/pip install -q -r requirements.txt
id skinbot >/dev/null 2>&1 || useradd --system --home /opt/skinbot --shell /usr/sbin/nologin skinbot
chown -R skinbot: /opt/skinbot
chmod 600 .env  # токен видит только служба
cat > /etc/systemd/system/skinbot.service <<'UNIT'
[Unit]
Description=Skin arbitrage Telegram bot
After=network-online.target
Wants=network-online.target

[Service]
User=skinbot
WorkingDirectory=/opt/skinbot
ExecStart=/opt/skinbot/venv/bin/python -u bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
systemctl daemon-reload
systemctl enable -q skinbot
systemctl restart skinbot
sleep 5
systemctl is-active skinbot && echo "OK: бот работает. Логи: journalctl -u skinbot -f"
journalctl -u skinbot -n 5 --no-pager

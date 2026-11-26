@echo off
chcp 65001 >nul
cd /d %~dp0
title Skin Deals bot
:loop
python bot.py
rem Упал (нет интернета/VPN при включении ПК и т.п.) — поднимаемся снова. Выключить бота: закрыть это окно.
echo Бот остановился, перезапуск через 30 секунд. Чтобы выключить бота, закройте это окно.
timeout /t 30 >nul
goto loop

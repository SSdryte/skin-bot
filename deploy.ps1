# Переносит бота на сервер (Ubuntu/Debian) и запускает его там как службу.
# Запуск:  .\deploy.ps1 root@IP_СЕРВЕРА      (пароль сервера спросит 2 раза)
# Повторный запуск — обновление кода на сервере.
param([Parameter(Mandatory = $true)][string]$Server)
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Select-String -Path .env -Pattern "^TELEGRAM_TOKEN=.+" -Quiet -ErrorAction SilentlyContinue)) {
    throw "Нет файла .env с TELEGRAM_TOKEN=... (образец в .env.example)"
}
$tar = Join-Path $env:TEMP "skinbot.tar"
tar -cf $tar bot.py skins.py requirements.txt install.sh .env
if ($LASTEXITCODE) { throw "tar не сработал" }
scp $tar "${Server}:/tmp/skinbot.tar"
if ($LASTEXITCODE) { throw "Не удалось скопировать файлы на сервер" }
ssh $Server "mkdir -p /opt/skinbot && tar -xf /tmp/skinbot.tar -C /opt/skinbot && rm /tmp/skinbot.tar && sed -i 's/\r$//' /opt/skinbot/.env /opt/skinbot/install.sh && bash /opt/skinbot/install.sh"
Remove-Item $tar

@echo off
chcp 65001 >nul
cd /d "%~dp0"
title PhonePicker — каталог v2, настоящие данные
rem эта версия работает на порту 8081, чтобы не мешать основной (8080)
set PORT=8081

where cargo >nul 2>nul
if errorlevel 1 (
  echo Rust не найден.
  echo Установите его командой:  winget install Rustlang.Rustup
  echo или скачайте установщик с https://rustup.rs
  echo После установки закройте это окно и запустите run.bat ещё раз.
  pause
  exit /b 1
)

rem запущенный сервер этой версии держит exe-файл, и сборка не сможет его заменить — сначала останавливаем его
rem (только процесс на порту 8081; основная версия на 8080 не трогается)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":8081 " ^| findstr LISTENING') do taskkill /PID %%p /F >nul 2>nul
timeout /t 1 /nobreak >nul

rem если есть Python — пересобрать каталог из data\catalog (иначе используется готовый catalog.db)
where python >nul 2>nul
if not errorlevel 1 (
  pushd ..
  python tools\build_catalog.py
  popd
)

echo Сборка (первый раз 1-3 минуты, потом пара секунд)...
cargo build --release
if errorlevel 1 (
  echo Сборка не удалась, текст ошибки выше.
  pause
  exit /b 1
)

start "PhonePicker random server" target\release\phonepicker-server.exe
timeout /t 2 /nobreak >nul
start "" http://localhost:8081
echo Сервер запущен в отдельном окне: http://localhost:8081
echo Чтобы остановить, закройте окно "PhonePicker random server".
timeout /t 5 >nul

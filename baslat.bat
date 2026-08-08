@echo off
chcp 65001 >nul
title Muzik AI Baslatici
echo =======================================================
echo   MUZIK AI - Baslatiliyor
echo =======================================================

echo [1/3] ACE-Step sicak servis baslatiliyor (:8001)...
cd /d C:\Users\FiratBakir\muzik-ai\ACE-Step-1.5
set ACESTEP_INIT_LLM=false
set ACESTEP_OFFLOAD_TO_CPU=true
set ACESTEP_API_PORT=8001
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
start "ACE-Step Sicak Servis" .venv\Scripts\python.exe -m acestep.api_server --port 8001

echo     Servis yukleniyor, 20 sn bekleniyor...
timeout /t 20 /nobreak >nul

echo [2/3] Web uygulamasi baslatiliyor (:5000)...
cd /d C:\Users\FiratBakir\muzik-ai\web
start "Muzik AI Web" "C:\Program Files\Python311\python.exe" app.py

echo [3/3] Tarayici aciliyor...
timeout /t 3 /nobreak >nul
start http://127.0.0.1:5000

echo.
echo =======================================================
echo   HAZIR!  http://127.0.0.1:5000
echo   (Iki siyah pencereyi KAPATMA - servisler orada calisiyor)
echo =======================================================
echo Bu pencereyi kapatabilirsin.
timeout /t 8 /nobreak >nul

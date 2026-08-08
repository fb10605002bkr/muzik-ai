@echo off
chcp 65001 >nul
title Muzik AI - Muzik Uretim Servisi ve Tunel
echo =======================================================
echo   MUZIK AI - Müzik Üretim Servisi Başlatılıyor
echo =======================================================

echo [1/2] ACE-Step Sıcak Servis Başlatılıyor (:8001)...
cd /d C:\Users\FiratBakir\muzik-ai\ACE-Step-1.5
set ACESTEP_INIT_LLM=false
set ACESTEP_OFFLOAD_TO_CPU=true
set ACESTEP_API_PORT=8001
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
start "ACE-Step Sicak Servis" .venv\Scripts\python.exe -m acestep.api_server --port 8001

echo [2/2] Cloudflare Tünel Başlatılıyor (Lütfen bekleyin)...
timeout /t 10 /nobreak >nul
cd /d C:\Users\FiratBakir\muzik-ai
.\cloudflared.exe tunnel --http-host-header "localhost" --url http://127.0.0.1:8001

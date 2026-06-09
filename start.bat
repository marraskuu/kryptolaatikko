@echo off
cd /d "%~dp0"
echo Kaynnistetaan Krypto Simulaattori...
powershell -ExecutionPolicy Bypass -File "%~dp0start.ps1"
pause

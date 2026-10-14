@echo off
rem Микромонитор запуска фабрики Lab-100 — пуск для двойного клика.
rem Запускает scripts\micromonitor.ps1 (окно с ошибками запуска фабрики).
chcp 65001 >nul
powershell -NoProfile -STA -ExecutionPolicy Bypass -File "%~dp0micromonitor.ps1" %*
if errorlevel 1 pause

@echo off
chcp 65001 > nul
cd /d "%~dp0"
py -3 bot_new.py
pause

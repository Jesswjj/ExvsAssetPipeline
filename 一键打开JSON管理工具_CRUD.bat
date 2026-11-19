@echo off
chcp 65001 >nul
cd /d "%~dp0"
python -X utf8 exvs_db_manager.py interactive
pause

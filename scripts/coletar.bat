@echo off
rem Coleta diaria de precos (chamado pelo Agendador de Tarefas do Windows).
cd /d "%~dp0.."
echo === %date% %time% >> data\coletor.log
".venv\Scripts\python.exe" -u app\coletor.py >> data\coletor.log 2>&1

@echo off
REM Inicia o FEFO localmente no Windows. Coloque um atalho deste arquivo na pasta
REM "Inicializar" do Windows (shell:startup) para abrir sozinho junto com o PC.
cd /d "%~dp0.."
cd backend
start "" http://127.0.0.1:8000
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000

@echo off
REM Start Astra local dev server on Windows
.\.venv\Scripts\uvicorn.exe astra.api.main:app --host 0.0.0.0 --port 8080 --reload

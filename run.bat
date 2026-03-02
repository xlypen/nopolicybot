@echo off
chcp 65001 >nul
cd /d "%~dp0"

REM Сначала пробуем "py" (лаунчер Python в Windows), потом "python"
set PYEXE=
where py >nul 2>&1 && set PYEXE=py
if not defined PYEXE where python >nul 2>&1 && set PYEXE=python
if not defined PYEXE (
    echo.
    echo [ОШИБКА] Python не найден.
    echo.
    echo Установите Python с https://www.python.org/downloads/
    echo При установке обязательно отметьте "Add Python to PATH".
    echo После установки перезапустите run.bat.
    echo.
    pause
    exit /b 1
)

if not exist "venv" (
    echo Создаю виртуальное окружение и ставлю зависимости...
    %PYEXE% -m venv venv
    call venv\Scripts\activate
    pip install -r requirements.txt
) else (
    call venv\Scripts\activate
)

%PYEXE% bot.py
pause

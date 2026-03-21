@echo off
REM ============================================
REM MedInventory - Setup Iniziale
REM by Studio Bergamaschi
REM ============================================

setlocal
set APP_DIR=%~dp0

echo.
echo ============================================
echo  MedInventory - Setup Iniziale
echo  by Studio Bergamaschi
echo ============================================
echo.

REM Check Python
python --version >nul 2>&1
if %errorlevel% neq 0 (
    echo ERRORE: Python non trovato!
    echo Installa Python 3.10+ da https://python.org
    echo Assicurati di selezionare "Add Python to PATH"
    pause
    exit /b 1
)

echo [1/4] Creazione virtual environment...
if not exist "%APP_DIR%venv" (
    python -m venv "%APP_DIR%venv"
    echo       Virtual environment creato.
) else (
    echo       Virtual environment gia esistente.
)

echo.
echo [2/4] Installazione dipendenze...
"%APP_DIR%venv\Scripts\pip" install --upgrade pip >nul 2>&1
"%APP_DIR%venv\Scripts\pip" install -r "%APP_DIR%requirements.txt"

echo.
echo [3/4] Inizializzazione database...
"%APP_DIR%venv\Scripts\python" "%APP_DIR%seed.py"

echo.
echo [4/4] Verifica configurazione...
if not exist "%APP_DIR%config.json" (
    echo       Il file config.json verra creato al primo avvio.
    echo       Modifica config.example.json per personalizzare le impostazioni.
) else (
    echo       config.json trovato.
)

echo.
echo ============================================
echo  Setup completato!
echo ============================================
echo.
echo Per avviare in modalita sviluppo:
echo   venv\Scripts\python app.py
echo.
echo Per avviare in produzione:
echo   venv\Scripts\python run_production.py
echo.
echo Per installare come servizio Windows:
echo   install_service.bat (come amministratore)
echo.
echo Credenziali admin predefinite:
echo   Email:    admin@medinventory.local
echo   Password: admin123
echo   (verra richiesto di cambiarla al primo accesso)
echo.
echo L'applicazione sara accessibile su:
echo   http://localhost:5000
echo   http://<ip-di-questa-macchina>:5000
echo.
pause
endlocal

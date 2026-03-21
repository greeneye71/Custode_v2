@echo off
REM ============================================
REM MedInventory - Windows Service Installer
REM Uses NSSM (Non-Sucking Service Manager)
REM by Studio Bergamaschi
REM ============================================

setlocal enabledelayedexpansion

REM Configuration
set SERVICE_NAME=MedInventory
set SERVICE_DISPLAY=MedInventory - Gestione Apparecchi Elettromedicali
set SERVICE_DESC=Applicazione web per la gestione degli apparecchi elettromedicali. Accessibile via browser su http://localhost:5000

REM Auto-detect paths
REM FIX: %~dp0 include la backslash finale - va rimossa per compatibilita' con NSSM AppDirectory
set APP_DIR=%~dp0
if "%APP_DIR:~-1%"=="\" set APP_DIR=%APP_DIR:~0,-1%
set PYTHON_EXE=%APP_DIR%\venv\Scripts\python.exe
set APP_SCRIPT=%APP_DIR%\run_production.py
set LOG_DIR=%APP_DIR%\logs

REM Check if running as admin
net session >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERRORE: Eseguire come Amministratore!
    echo Tasto destro su questo file -^> Esegui come amministratore
    echo.
    pause
    exit /b 1
)

REM Check if NSSM exists
REM FIX: logica corretta - prima cerca nel PATH, poi nella cartella app
set NSSM=
where nssm >nul 2>&1
if %errorlevel% equ 0 (
    set NSSM=nssm
) else if exist "%APP_DIR%\nssm.exe" (
    set NSSM="%APP_DIR%\nssm.exe"
) else (
    echo.
    echo ERRORE: NSSM non trovato!
    echo.
    echo Scarica NSSM da: https://nssm.cc/download
    echo Copia nssm.exe nella cartella dell'applicazione: %APP_DIR%
    echo Oppure aggiungilo al PATH di sistema.
    echo.
    pause
    exit /b 1
)

REM Check if Python venv exists
if not exist "%PYTHON_EXE%" (
    echo.
    echo ERRORE: Virtual environment non trovato!
    echo.
    echo Creare il virtual environment con:
    echo   cd "%APP_DIR%"
    echo   python -m venv venv
    echo   venv\Scripts\pip install -r requirements.txt
    echo.
    pause
    exit /b 1
)

REM FIX: verifica che waitress sia installato nel venv prima di procedere
"%PYTHON_EXE%" -c "import waitress" >nul 2>&1
if %errorlevel% neq 0 (
    echo.
    echo ERRORE: Il pacchetto 'waitress' non e' installato nel virtual environment!
    echo.
    echo Eseguire:
    echo   "%APP_DIR%\venv\Scripts\pip" install -r "%APP_DIR%\requirements.txt"
    echo.
    pause
    exit /b 1
)

echo.
echo ============================================
echo  MedInventory - Installazione Servizio
echo ============================================
echo.
echo Cartella applicazione: %APP_DIR%
echo Python:                %PYTHON_EXE%
echo.
echo Cosa vuoi fare?
echo.
echo  1. Installa il servizio
echo  2. Rimuovi il servizio
echo  3. Avvia il servizio
echo  4. Ferma il servizio
echo  5. Riavvia il servizio
echo  6. Stato del servizio
echo  7. Mostra log errori
echo  8. Esci
echo.

set /p choice="Scelta (1-8): "

if "%choice%"=="1" goto install
if "%choice%"=="2" goto remove
if "%choice%"=="3" goto start
if "%choice%"=="4" goto stop
if "%choice%"=="5" goto restart
if "%choice%"=="6" goto status
if "%choice%"=="7" goto showlog
if "%choice%"=="8" goto end

echo Scelta non valida.
goto end


:install
echo.
echo Installazione del servizio %SERVICE_NAME%...

REM FIX: controlla se il servizio e' gia' installato
%NSSM% status %SERVICE_NAME% >nul 2>&1
if %errorlevel% equ 0 (
    echo.
    echo ATTENZIONE: Il servizio %SERVICE_NAME% e' gia' installato.
    echo Per reinstallarlo rimuoverlo prima con l'opzione 2.
    echo.
    goto end
)

REM Create log directory
if not exist "%LOG_DIR%" mkdir "%LOG_DIR%"

REM Install service
%NSSM% install %SERVICE_NAME% "%PYTHON_EXE%" "%APP_SCRIPT%"
if %errorlevel% neq 0 (
    echo.
    echo ERRORE: Installazione del servizio fallita.
    goto end
)

REM Configure service
%NSSM% set %SERVICE_NAME% DisplayName "%SERVICE_DISPLAY%"
%NSSM% set %SERVICE_NAME% Description "%SERVICE_DESC%"
REM FIX: APP_DIR senza backslash finale - compatibile con NSSM AppDirectory
%NSSM% set %SERVICE_NAME% AppDirectory "%APP_DIR%"
%NSSM% set %SERVICE_NAME% AppStdout "%LOG_DIR%\service_stdout.log"
%NSSM% set %SERVICE_NAME% AppStderr "%LOG_DIR%\service_stderr.log"
%NSSM% set %SERVICE_NAME% AppStdoutCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppStderrCreationDisposition 4
%NSSM% set %SERVICE_NAME% AppRotateFiles 1
%NSSM% set %SERVICE_NAME% AppRotateBytes 5242880
%NSSM% set %SERVICE_NAME% Start SERVICE_AUTO_START

REM FIX: configurazione account di esecuzione
echo.
echo -----------------------------------------------
echo  Account di esecuzione del servizio
echo -----------------------------------------------
echo.
echo  1. LocalSystem  ^(default - NON accede a cartelle utente o unita' di rete^)
echo  2. Account utente specifico  ^(consigliato^)
echo.
set /p accountChoice="Scelta (1/2): "

if "!accountChoice!"=="2" (
    echo.
    set /p svcUser="Nome utente ^(es: .\Amministratore  o  DOMINIO\utente^): "
    set /p svcPass="Password: "
    %NSSM% set %SERVICE_NAME% ObjectName "!svcUser!" "!svcPass!"
    if !errorlevel! neq 0 (
        echo ATTENZIONE: impossibile impostare l'account - verra' usato LocalSystem.
    ) else (
        echo Account configurato: !svcUser!
    )
)

echo.
echo Servizio installato con successo.
echo Il servizio si avviera' automaticamente all'avvio di Windows.
echo.
set /p startNow="Vuoi avviare il servizio ora? (S/N): "
if /i "!startNow!"=="S" goto :do_start
goto end


:do_start
echo.
echo Avvio del servizio...
%NSSM% start %SERVICE_NAME%
if %errorlevel% neq 0 (
    echo.
    echo ERRORE: comando di avvio fallito.
    goto check_running
)
REM Attendi qualche secondo per lasciare il tempo al processo di inizializzarsi
timeout /t 4 /nobreak >nul

:check_running
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if %errorlevel% equ 0 (
    echo Servizio avviato correttamente.
) else (
    echo.
    echo ATTENZIONE: il servizio non risulta in esecuzione.
    echo.
    echo Controllare il log errori:
    echo   %LOG_DIR%\service_stderr.log
    echo.
    echo Possibili cause:
    echo   - Dipendenze mancanti nel venv
    echo   - Permessi insufficienti sull'account di servizio
    echo   - Porta 5000 gia' in uso
    echo   - config.json o config.example.json mancanti
)
goto end


:remove
echo.
echo Rimozione del servizio %SERVICE_NAME%...
%NSSM% stop %SERVICE_NAME% >nul 2>&1
%NSSM% remove %SERVICE_NAME% confirm
if %errorlevel% equ 0 (
    echo Servizio rimosso.
) else (
    echo ERRORE nella rimozione del servizio.
)
goto end


:start
echo.
echo Avvio del servizio %SERVICE_NAME%...
%NSSM% start %SERVICE_NAME%
if %errorlevel% neq 0 (
    echo.
    echo ERRORE: comando di avvio fallito.
)
timeout /t 4 /nobreak >nul
sc query %SERVICE_NAME% | findstr /i "RUNNING" >nul
if %errorlevel% equ 0 (
    echo Servizio in esecuzione.
) else (
    echo.
    echo ATTENZIONE: il servizio non risulta in esecuzione.
    echo Controllare il log errori: %LOG_DIR%\service_stderr.log
)
goto end


:stop
echo.
echo Arresto del servizio %SERVICE_NAME%...
%NSSM% stop %SERVICE_NAME%
if %errorlevel% equ 0 (
    echo Servizio arrestato.
) else (
    echo ERRORE nell'arresto del servizio.
)
goto end


:restart
echo.
echo Riavvio del servizio %SERVICE_NAME%...
%NSSM% restart %SERVICE_NAME%
if %errorlevel% neq 0 (
    echo ERRORE nel riavvio.
    echo Controllare il log errori: %LOG_DIR%\service_stderr.log
) else (
    echo Servizio riavviato.
)
goto end


:status
echo.
sc query %SERVICE_NAME%
goto end


:showlog
echo.
if exist "%LOG_DIR%\service_stderr.log" (
    echo === %LOG_DIR%\service_stderr.log ===
    echo.
    type "%LOG_DIR%\service_stderr.log"
) else (
    echo Nessun log errori trovato in: %LOG_DIR%\service_stderr.log
)
goto end


:end
echo.
pause
endlocal

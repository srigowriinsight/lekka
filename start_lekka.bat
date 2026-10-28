@echo off
REM Starts Lekka and (optionally) shares it through a Cloudflare Quick Tunnel.
REM Usage:  start_lekka.bat                     (local only, http://localhost:8000)
REM         start_lekka.bat manager@company.com (also share, only for this email)
cd /d "%~dp0"
call venv\Scripts\activate.bat
flask db upgrade
flask extend-recurring
start "Lekka server" cmd /k waitress-serve --listen=127.0.0.1:8000 --call app:create_app
timeout /t 3 > nul
start "" http://localhost:8000
if "%~1"=="" goto :eof
echo.
echo Sharing Lekka. Look for the https://....trycloudflare.com link below and send it to %1
echo The link changes every time this starts. Close this window to stop sharing.
cloudflared tunnel --url http://localhost:8000 --allowed-mail %1

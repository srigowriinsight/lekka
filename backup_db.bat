@echo off
REM Daily backup of the Lekka database. Run at the end of each day.
cd /d "%~dp0"
if not exist backups mkdir backups
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set D=%%i

REM Use mariadb-dump from PATH, or from the MariaDB install folder.
set DUMP=mariadb-dump
where mariadb-dump >nul 2>nul
if errorlevel 1 (
  for /d %%M in ("C:\Program Files\MariaDB*") do if exist "%%M\bin\mariadb-dump.exe" set DUMP="%%M\bin\mariadb-dump.exe"
)

%DUMP% -h 127.0.0.1 -P 3306 -u lekka -p --single-transaction lekka_db > backups\lekka_%D%.sql
echo Backup written to backups\lekka_%D%.sql
pause

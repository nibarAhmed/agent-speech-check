@echo off
set OUT=C:\spike\out
echo started %DATE% %TIME% > %OUT%\progress.txt
rem Silence the sandbox completely: stop Windows Audio inside it.
net stop audiosrv /y >> %OUT%\progress.txt 2>&1
sc query audiosrv >> %OUT%\progress.txt 2>&1
echo muted >> %OUT%\progress.txt
"C:\spike\in\nvda_2026.2.exe" --create-portable-silent --portable-path=C:\nvda >> %OUT%\progress.txt 2>&1
:waitnvda
if not exist C:\nvda\nvda.exe (
  timeout /t 2 /nobreak > nul
  goto waitnvda
)
timeout /t 5 /nobreak > nul
echo portable ready %TIME% >> %OUT%\progress.txt
"C:\spike\in\py\cpython-3.13.16-windows-x86_64-none\python.exe" -I C:\spike\in\spike_remote.py > %OUT%\spike_stdout.txt 2>&1
echo spike exit %ERRORLEVEL% %TIME% >> %OUT%\progress.txt
echo DONE > %OUT%\DONE

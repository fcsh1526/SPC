@echo off
"%~dp0..\python\python.exe" -m spc.signing %*
exit /b %ERRORLEVEL%

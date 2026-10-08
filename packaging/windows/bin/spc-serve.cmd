@echo off
"%~dp0..\python\python.exe" -m spc.api %*
exit /b %ERRORLEVEL%

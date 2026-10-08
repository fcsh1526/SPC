@echo off
"%~dp0..\python\python.exe" -m spc.qualification %*
exit /b %ERRORLEVEL%

@echo off
"%~dp0..\python\python.exe" -m spc.admin %*
exit /b %ERRORLEVEL%

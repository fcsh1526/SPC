@echo off
"%~dp0..\python\python.exe" -m spc.setup %*
exit /b %ERRORLEVEL%

@echo off
title F Whisper
cd /d "%~dp0"
python -m fwhisper.run %*
pause

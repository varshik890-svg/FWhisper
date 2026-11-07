@echo off
rem Stops the hidden F Whisper (started at login by start_hidden.pyw) and frees the VRAM.
powershell -NoProfile -Command "$p = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -like '*start_hidden.pyw*' -or ($_.Name -eq 'llama-server.exe' -and $_.ExecutablePath -like '*FWhisper*') }; if ($p) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }; 'F Whisper stopped.' } else { 'F Whisper was not running.' }; Start-Sleep 3"

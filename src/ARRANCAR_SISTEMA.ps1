# ============================================================
# ARRANCAR_SISTEMA.ps1 - v1.2
# Brazo Robotico Pick & Place v5.0
# USO: doble clic antes de cada sesion de trabajo/demo
# ============================================================

# 1. Matar instancias previas de mosquitto
Get-Process -Name "mosquitto" -ErrorAction SilentlyContinue | Stop-Process -Force
Start-Sleep -Seconds 1

# 2. Arrancar Mosquitto
Start-Process "C:\Program Files\mosquitto\mosquitto.exe" -ArgumentList "-c C:\mosquitto-broker\mosquitto.conf" -WindowStyle Hidden
Start-Sleep -Seconds 2

# 3. Verificar Mosquitto
$mqtt = Get-Process -Name "mosquitto" -ErrorAction SilentlyContinue
if ($mqtt) {
    Write-Host "[OK] Mosquitto corriendo (PID $($mqtt.Id))" -ForegroundColor Green
} else {
    Write-Host "[ERROR] Mosquitto no arranco" -ForegroundColor Red
    pause; exit
}

# 4. Verificar puertos
if (netstat -ano | findstr ":1883") { Write-Host "[OK] Puerto 1883 activo" -ForegroundColor Green }
if (netstat -ano | findstr ":9001") { Write-Host "[OK] Puerto 9001 activo" -ForegroundColor Green }

# 5. Arrancar Backend FastAPI
Start-Process "powershell.exe" -ArgumentList "-NoExit", "-ExecutionPolicy", "Bypass", "-Command", "cd 'C:\ruta\TP-IC2\src\backend'; .\venv\Scripts\Activate.ps1; uvicorn app.main:app --host 0.0.0.0 --port 8000"
Start-Sleep -Seconds 3
Write-Host "[OK] Backend FastAPI iniciado" -ForegroundColor Green

Write-Host ""
Write-Host "============================================" -ForegroundColor Cyan
Write-Host " Sistema listo. Abri la GUI en el navegador:" -ForegroundColor Cyan
Write-Host " http://localhost:5500/login.html" -ForegroundColor Yellow
Write-Host "============================================" -ForegroundColor Cyan
Write-Host ""
pause
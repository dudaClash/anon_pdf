:: ============================
:: build_console_onedir.bat
:: ============================
@echo off
setlocal
cd /d "%~dp0"

rem ===== Config =====
set APPNAME=AnonLGPD
set PYI_ENV=.venv
set DIST_DIR=dist
set BUILD_DIR=build
set SPEC_DIR=.

echo [1/7] Ambiente virtual...
if not exist %PYI_ENV% (
  python -m venv %PYI_ENV% || goto :err
)

echo [2/7] Dependencias...
call %PYI_ENV%\Scripts\pip install -U pip pyinstaller pymupdf || goto :err

echo [3/7] Metadados de versao (ver.txt)...
> ver.txt (
  echo VSVersionInfo(
  echo   ffi=FixedFileInfo(filevers=(1,0,1,0), prodvers=(1,0,1,0), flags=0, mask=0x3f, flagsmask=0x3f, filetype=0x1, subtype=0x0, date=(0, 0)),
  echo   kids=[
  echo     StringFileInfo([
  echo       StringTable('040904b0', [
  echo         StringStruct('CompanyName', 'Acoes-Sefaz'),
  echo         StringStruct('FileDescription', 'Anonimizador LGPD de PDFs (console visivel)'),
  echo         StringStruct('FileVersion', '1.0.1'),
  echo         StringStruct('InternalName', '%APPNAME%'),
  echo         StringStruct('OriginalFilename', '%APPNAME%.exe'),
  echo         StringStruct('ProductName', '%APPNAME%'),
  echo         StringStruct('ProductVersion', '1.0.1')
  echo       ])
  echo     ]),
  echo     VarFileInfo([VarStruct('Translation', [1033, 1200])])
  echo   ]
  echo )
)

echo [4/7] Limpando saidas anteriores...
if exist %DIST_DIR% rmdir /s /q %DIST_DIR%
if exist %BUILD_DIR% rmdir /s /q %BUILD_DIR%

echo [5/7] Build ONEDIR (console visivel, sem UPX)...
call %PYI_ENV%\Scripts\pyinstaller ^
  --name "%APPNAME%" ^
  --onedir ^
  --clean ^
  --noupx ^
  --version-file ver.txt ^
  --distpath "%DIST_DIR%" ^
  --workpath "%BUILD_DIR%" ^
  --specpath "%SPEC_DIR%" ^
  AnonLGPD.py || goto :err

echo [6/7] Verificando saida...
if not exist "%DIST_DIR%\%APPNAME%\%APPNAME%.exe" (
  echo.
  echo [ATENCAO] O executavel nao foi encontrado. Antivirus pode ter colocado em quarentena.
  echo Restaure e crie excecao para: "%cd%\%DIST_DIR%\%APPNAME%".
  pause
  exit /b 1
)

echo [7/7] OK! Executavel ONEDIR pronto.
echo Rode por: "%cd%\%DIST_DIR%\%APPNAME%\%APPNAME%.exe"
echo (Console ficara visivel ao executar.)
pause
exit /b 0

:err
echo.
echo Falha no build ONEDIR. Se um antivirus removeu o .exe, crie excecoes e rode de novo.
pause
exit /b 1


:: =================================
:: build_console_onefile.bat (opcional)
:: =================================
@echo off
setlocal
cd /d "%~dp0"

rem ===== Config =====
set APPNAME=AnonLGPD
set PYI_ENV=.venv
set DIST_DIR=dist_onefile
set BUILD_DIR=build_onefile
set SPEC_DIR=.

echo [1/7] Ambiente virtual...
if not exist %PYI_ENV% (
  python -m venv %PYI_ENV% || goto :err
)

echo [2/7] Dependencias...
call %PYI_ENV%\Scripts\pip install -U pip pyinstaller pymupdf || goto :err

echo [3/7] Metadados de versao (ver.txt)...
> ver.txt (
  echo VSVersionInfo(
  echo   ffi=FixedFileInfo(filevers=(1,0,1,0), prodvers=(1,0,1,0), flags=0, mask=0x3f, flagsmask=0x3f, filetype=0x1, subtype=0x0, date=(0, 0)),
  echo   kids=[
  echo     StringFileInfo([
  echo       StringTable('040904b0', [
  echo         StringStruct('CompanyName', 'Acoes-Sefaz'),
  echo         StringStruct('FileDescription', 'Anonimizador LGPD de PDFs (console visivel)'),
  echo         StringStruct('FileVersion', '1.0.1'),
  echo         StringStruct('InternalName', '%APPNAME%'),
  echo         StringStruct('OriginalFilename', '%APPNAME%.exe'),
  echo         StringStruct('ProductName', '%APPNAME%'),
  echo         StringStruct('ProductVersion', '1.0.1')
  echo       ])
  echo     ]),
  echo     VarFileInfo([VarStruct('Translation', [1033, 1200])])
  echo   ]
  echo )
)

echo [4/7] Limpando saidas anteriores...
if exist %DIST_DIR% rmdir /s /q %DIST_DIR%
if exist %BUILD_DIR% rmdir /s /q %BUILD_DIR%

echo [5/7] Build ONEFILE (console visivel, sem UPX)...
call %PYI_ENV%\Scripts\pyinstaller ^
  --name "%APPNAME%" ^
  --onefile ^
  --clean ^
  --noupx ^
  --version-file ver.txt ^
  --distpath "%DIST_DIR%" ^
  --workpath "%BUILD_DIR%" ^
  --specpath "%SPEC_DIR%" ^
  AnonLGPD.py || goto :err

echo [6/7] Verificando saida...
if not exist "%DIST_DIR%\%APPNAME%.exe" (
  echo.
  echo [ATENCAO] O executavel nao foi encontrado. Antivirus pode ter colocado em quarentena.
  echo Restaure e crie excecao para: "%cd%\%DIST_DIR%".
  pause
  exit /b 1
)

echo [7/7] OK! Executavel ONEFILE pronto.
echo Rode por: "%cd%\%DIST_DIR%\%APPNAME%.exe"
echo (Console ficara visivel ao executar.)
pause
exit /b 0

:err
echo.
echo Falha no build ONEFILE. Se um antivirus removeu o .exe, crie excecoes e rode de novo.
pause
exit /b 1

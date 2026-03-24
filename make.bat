@echo off
setlocal enabledelayedexpansion

set COMMAND=%1

if "%COMMAND%"=="" goto help
if "%COMMAND%"=="help" goto help
if "%COMMAND%"=="install" goto install
if "%COMMAND%"=="lint" goto lint
if "%COMMAND%"=="typecheck" goto typecheck
if "%COMMAND%"=="test" goto test
if "%COMMAND%"=="check" goto check
if "%COMMAND%"=="build" goto build
if "%COMMAND%"=="publish" goto publish
if "%COMMAND%"=="clean" goto clean

echo Unknown target: %COMMAND%
echo Run "make.bat help" for available targets.
exit /b 1

:help
echo Usage: make.bat ^<target^>
echo.
echo Targets:
echo   install    Install all dependencies (including dev group)
echo   lint       Run ruff and flake8 linters
echo   typecheck  Run ty type checker
echo   test       Run pytest with coverage
echo   check      Run lint + typecheck + test
echo   build      Build the distribution packages
echo   publish    Build and publish to PyPI via uv
echo   clean      Remove build artifacts
goto end

:install
uv sync --group dev
goto end

:lint
uv run ruff check fastapi_profiler test
if errorlevel 1 exit /b 1
uv run flake8 fastapi_profiler test
if errorlevel 1 exit /b 1
goto end

:typecheck
uv run ty check fastapi_profiler
if errorlevel 1 exit /b 1
goto end

:test
uv run pytest
if errorlevel 1 exit /b 1
goto end

:check
call "%~f0" lint
if errorlevel 1 exit /b 1
call "%~f0" typecheck
if errorlevel 1 exit /b 1
call "%~f0" test
if errorlevel 1 exit /b 1
goto end

:build
uv build
if errorlevel 1 exit /b 1
goto end

:publish
call "%~f0" build
if errorlevel 1 exit /b 1
uv publish
if errorlevel 1 exit /b 1
goto end

:clean
if exist dist rmdir /s /q dist
if exist build rmdir /s /q build
if exist .coverage del /f .coverage
if exist htmlcov rmdir /s /q htmlcov
if exist .pytest_cache rmdir /s /q .pytest_cache
for /r . %%d in (__pycache__) do if exist "%%d" rmdir /s /q "%%d"
for /r . %%f in (*.pyc) do del /f "%%f"
for /r . %%d in (*.egg-info) do if exist "%%d" rmdir /s /q "%%d"
goto end

:end
endlocal

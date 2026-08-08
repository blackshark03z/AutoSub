$ErrorActionPreference = "Stop"
$env:TOOL_AUTO_SUB_ROOT = Split-Path -Parent $MyInvocation.MyCommand.Path
python -m uvicorn app.main:app --host 127.0.0.1 --port 8173

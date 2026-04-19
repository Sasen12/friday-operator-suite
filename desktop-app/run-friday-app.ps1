param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$RemainingArgs
)

$ErrorActionPreference = "Stop"

$launcher = Resolve-Path (Join-Path $PSScriptRoot '..\run-friday-app.ps1')
& $launcher @RemainingArgs

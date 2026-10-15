# Builds jokerd.elf on Windows with the PS5 payload SDK + LLVM. No admin, no Linux, no Docker.
# Usage: powershell -File build.ps1   (from anywhere)
$ErrorActionPreference = 'Stop'
$root = Resolve-Path "$PSScriptRoot\.."
$sdk  = "$root\.toolchain\sdk\ps5-payload-sdk"
$llvm = "$root\.toolchain\llvm\bin"
$env:PATH = "$llvm;$sdk\win;$env:PATH"
$cc = "$sdk\win\prospero-clang.cmd"
$objs = @()
foreach ($f in 'main', 'util') {
  $o = "$PSScriptRoot\$f.o"
  & $cc -c -O2 -Wall -Wextra -Werror -std=gnu11 -D_BSD_SOURCE -I"$PSScriptRoot" "$PSScriptRoot\$f.c" -o $o
  if ($LASTEXITCODE -ne 0) { throw "compile $f failed ($LASTEXITCODE)" }
  $objs += $o
}
$out = "$PSScriptRoot\jokerd.elf"
# LLVM's own ld.lld replaces the SDK's prospero-lld.exe (Windows Defender flags it as a false positive).
# The flags are exactly what the SDK's prospero-lld wrapper script adds.
$lib = "$sdk\target\lib"
& "$llvm\ld.lld.exe" -m elf_x86_64 -pie "-T$sdk\ldscripts\elf_x86_64.x" --eh-frame-hdr `
  -z max-page-size=0x4000 -mllvm -emulated-tls -o $out "-L$lib" "-L$sdk\target\user\homebrew\lib" `
  -lc -lkernel_web $objs -lSceSystemService -lSceUserService -lScePad `
  "$lib\crt1.o" -lSceLibcInternal -lSceNet --hash-style=gnu
if ($LASTEXITCODE -ne 0) { throw "link failed ($LASTEXITCODE)" }
Write-Host "built $out ($((Get-Item $out).Length) bytes)"

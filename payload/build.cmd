@echo off
set R=D:\ps5\joker-ps5-control
set SDK=%R%\.toolchain\sdk\ps5-payload-sdk
set PATH=%R%\.toolchain\llvm\bin;%SDK%\win;%PATH%
cd /d %R%\payload
for %%f in (main util) do (
  call "%SDK%\win\prospero-clang.cmd" -c -O2 -Wall -Wextra -Werror -std=gnu11 -D_BSD_SOURCE -I. %%f.c -o %%f.o || exit /b 1
)
set L=%SDK%\target\lib
"%R%\.toolchain\llvm\bin\ld.lld.exe" -m elf_x86_64 -pie "-T%SDK%\ldscripts\elf_x86_64.x" --eh-frame-hdr -z max-page-size=0x4000 -mllvm -emulated-tls -o jokerd.elf "-L%L%" "-L%SDK%\target\user\homebrew\lib" -lc -lkernel_web main.o util.o -lSceSystemService -lSceUserService -lScePad "%L%\crt1.o" -lSceLibcInternal -lSceNet --hash-style=gnu || exit /b 1
echo BUILD_OK

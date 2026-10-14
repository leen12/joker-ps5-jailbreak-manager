# Joker PS5 Control — payload build
# Requires: PS5 payload SDK v0.44+, clang-18, lld-18
# Set PS5_PAYLOAD_SDK to the SDK root (default: /opt/ps5-payload-sdk)

PS5_PAYLOAD_SDK ?= /opt/ps5-payload-sdk

CC      := clang-18
LD      := ld.lld-18
OBJCOPY := llvm-objcopy-18

SYSROOT := $(PS5_PAYLOAD_SDK)/target
CFLAGS  := -target x86_64-sie-ps5 --sysroot $(SYSROOT) \
           -isystem $(SYSROOT)/include -isysroot $(SYSROOT) \
           -O2 -Wall -Wextra -Werror -fPIC -fno-exceptions \
           -D_POSIX_C_SOURCE=200809L
LDFLAGS := -m elf_x86_64 -pie --script $(SYSROOT)/linker.ld \
           --eh-frame-hdr -z max-page-size=0x4000 \
           -L$(SYSROOT)/lib
LIBS    := -lkernel -lSceUserService -lScePad -lSceSystemService \
           -lSceLncUtil -lc -lc++

SRCS := main.c util.c
OBJS := $(SRCS:.c=.o)
ELF  := jokerd.elf

.PHONY: all clean

all: $(ELF)

$(ELF): $(OBJS)
	$(LD) $(LDFLAGS) -o $@ $(OBJS) $(LIBS)

%.o: %.c util.h
	$(CC) $(CFLAGS) -c -o $@ $<

clean:
	rm -f $(OBJS) $(ELF)

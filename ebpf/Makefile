# eBPF 转发面构建
# 注意: 多架构系统需加 -I/usr/include/<arch>-linux-gnu 才能找到 asm/types.h

ARCH    ?= $(shell uname -m | sed 's/x86_64/x86/;s/aarch64/arm64/')
MULTIARCH := $(shell gcc -print-multiarch 2>/dev/null)
CLANG   ?= clang
CC      ?= gcc

BPF_CFLAGS := -O2 -g -target bpf -D__TARGET_ARCH_$(ARCH) -Wall
ifneq ($(MULTIARCH),)
BPF_CFLAGS += -I/usr/include/$(MULTIARCH)
endif

USER_CFLAGS := -O2 -Wall
USER_LIBS   := -lbpf

all: sat_fwd.bpf.o sat_fwd_loader

sat_fwd.bpf.o: sat_fwd.bpf.c
	$(CLANG) $(BPF_CFLAGS) -c $< -o $@

sat_fwd_loader: sat_fwd_loader.c
	$(CC) $(USER_CFLAGS) $< -o $@ $(USER_LIBS)

clean:
	rm -f sat_fwd.bpf.o sat_fwd_loader

.PHONY: all clean

# SPDX-License-Identifier: MPL-2.0
SDK ?= /opt/lilsmoke-sdk
CROSS ?= $(SDK)/bin/mipsel-buildroot-linux-musl-
SYSROOT ?= $(SDK)/mipsel-buildroot-linux-musl/sysroot
OUT ?= build
CC := $(CROSS)gcc
CXX := $(CROSS)g++
STRIP := $(CROSS)strip
SDL_FLAGS := -I$(SYSROOT)/usr/include/SDL2
MIPS_FLAGS := -mips32r2 -mtune=74kc -mabi=32 -mfp32 -mhard-float -mdspr2
INPUT_FLAGS := -std=c++17 -O2 -Wall -Wextra $(MIPS_FLAGS) $(SDL_FLAGS) -Isrc/input
OBJECTS := $(OUT)/lilsmoke-common-input.o $(OUT)/lilsmoke-input.o $(OUT)/lilsmoke-keymap.o $(OUT)/lilsmoke-log.o
INPUT_HEADERS := $(wildcard src/input/*.h src/adaptation/*.h)

all: $(OUT)/sm64-prepare $(OUT)/sm64-bin2c $(OUT)/sm64-build-shell
test:
	PYTHONPATH=src/prepare:tools:tests python3 -m unittest test_cook test_native_build test_resume test_build_progress test_package test_split_sources test_release_update
$(OUT):
	mkdir -p $@
$(OUT)/lilsmoke-input.o: src/adaptation/input_bridge.cpp $(INPUT_HEADERS) | $(OUT)
	$(CXX) $(INPUT_FLAGS) -Isrc/adaptation -c $< -o $@
$(OUT)/lilsmoke-common-input.o: src/input/Input.cpp $(INPUT_HEADERS) | $(OUT)
	$(CXX) $(INPUT_FLAGS) -c $< -o $@
$(OUT)/lilsmoke-keymap.o: src/input/Keymap.cpp $(INPUT_HEADERS) | $(OUT)
	$(CXX) $(INPUT_FLAGS) -c $< -o $@
$(OUT)/lilsmoke-log.o: src/input/Log.cpp $(INPUT_HEADERS) | $(OUT)
	$(CXX) $(INPUT_FLAGS) -c $< -o $@
$(OUT)/bootstrap.o: src/prepare/bootstrap.cpp $(INPUT_HEADERS) src/prepare/font8x8_basic.h | $(OUT)
	$(CXX) $(INPUT_FLAGS) -Isrc/adaptation -Isrc/prepare -c $< -o $@
$(OUT)/sm64-prepare: $(OUT)/bootstrap.o $(OBJECTS)
	$(CXX) $^ -L$(SYSROOT)/usr/lib -lSDL2 -lwebp -ljson-c -lpthread -ldl -o $@
	$(STRIP) $@
$(OUT)/sm64-bin2c: src/prepare/bin2c.c | $(OUT)
	$(CC) -std=c99 -O2 -Wall -Wextra -Werror -pedantic $(MIPS_FLAGS) -s $< -o $@
$(OUT)/sm64-build-shell: src/prepare/build_shell.c | $(OUT)
	$(CC) -std=c99 -O2 -Wall -Wextra -Werror -pedantic $(MIPS_FLAGS) -s $< -o $@

.PHONY: all test

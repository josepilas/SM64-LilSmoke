# SPDX-License-Identifier: MPL-2.0
SDK ?= /opt/lilsmoke-sdk
CROSS ?= $(SDK)/bin/mipsel-buildroot-linux-musl-
SYSROOT ?= $(SDK)/mipsel-buildroot-linux-musl/sysroot
OUT ?= build
CC := $(CROSS)gcc
CXX := $(CROSS)g++
SDL_FLAGS := -I$(SYSROOT)/usr/include/SDL2
INPUT_FLAGS := -std=c++17 -O2 $(SDL_FLAGS) -Isrc/input -include src/input/input-wire.h
OBJECTS := $(OUT)/lilsmoke-input.o $(OUT)/lilsmoke-common-input.o $(OUT)/lilsmoke-keymap.o $(OUT)/lilsmoke-log.o

all: $(OUT)/sm64-prepare
test:
	PYTHONPATH=src/prepare:tools python3 -m unittest discover -s tests -p 'test_*.py'
$(OUT):
	mkdir -p $@
$(OUT)/lilsmoke-input.o: src/adaptation/input_bridge.cpp | $(OUT)
	$(CXX) $(INPUT_FLAGS) -Isrc/adaptation -c $< -o $@
$(OUT)/lilsmoke-common-input.o: src/input/Input.cpp | $(OUT)
	$(CXX) $(INPUT_FLAGS) -c $< -o $@
$(OUT)/lilsmoke-keymap.o: src/input/Keymap.cpp | $(OUT)
	$(CXX) $(INPUT_FLAGS) -c $< -o $@
$(OUT)/lilsmoke-log.o: src/input/Log.cpp | $(OUT)
	$(CXX) $(INPUT_FLAGS) -c $< -o $@
$(OUT)/sm64-prepare: src/prepare/bootstrap.cpp $(OBJECTS)
	$(CXX) -std=c++17 -O2 -Wall -Wextra $(SDL_FLAGS) -Isrc/adaptation -Isrc/prepare \
	    $^ -L$(SYSROOT)/usr/lib -lSDL2 -lwebp -ljson-c -lpthread -ldl -o $@

.PHONY: all test

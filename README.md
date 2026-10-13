# SM64-LilSmoke

Native original-engine Super Mario 64 adaptation
for LilSmokeOS on the tested MIPS device lineage.

This repository contains source and build/extraction tools, not a ROM,
extracted texture/music/sample collection or a pre-cooked full game.
The original game engine is based on [n64decomp/sm64](https://github.com/n64decomp/sm64)
and the pinned [DrUm78 software port](https://github.com/DrUm78/sm64-port).
It is not a recreated-game demo or a Windows emulator.

## Bring Your Own Data

Install the `.lilpck` through LilSmoke Explorer, then put one supported **US
1.0** ROM in the installed application's `original/` directory. Its name is
arbitrary; Z64, V64 and N64 byte order are identified from contents.
Expected normalized SHA-1: `9bef1128717f958171a4afac3ed78ee2bb4e86ce`.

First launch extracts data and prepares a private native runtime. The loading
screen displays the phase, percentage and MB extracted. Initial preparation
also compiles source locally and can take substantial time; later launches
use the verified cache. Cancel leaves the original ROM intact. After a
durable verified cache and original-engine preflight succeed, only the copy
inside the application's `original/` is deleted. Keep your separate dump backup.

Do not distribute the generated `.sm64-cache` or its game executable. The
upstream graphics implementation permits source redistribution but explicitly
prohibits binary redistribution; its binary is compiled locally, not shipped.
Save data is kept separately in `saves/` and survives a cache rebuild.

## Controls

| Input | Action |
| --- | --- |
| Left stick | Movement and speed |
| A | Jump |
| X / B | Punch, kick, grab and throw |
| LT | Crouch, long jump and ground pound |
| RB | Camera mode |
| Right stick | N64 C-button camera controls |
| Start | Original pause |
| Start + X | LilSmoke options menu |
| Start + Select | Exit |

## Target And Build

The supported runtime is MIPS32r2/o32 little-endian, musl, software SDL2/DirectFB
and CPU-only rendering. No OpenGL, GLES, Vulkan or desktop display server is
required. The tested reference device has one MIPS CPU and 128 MiB RAM; this
is not a claim that every M8 marketing variant is compatible.

The preparation UI and authored input objects can be rebuilt without any ROM:

```sh
make SDK=/path/to/lilsmoke-sdk
```

Set `CROSS` and `SYSROOT` when using a differently laid-out SDK. The SDK needs
SDL2, libwebp, json-c and the C++17 musl toolchain. Runtime cooking uses bundled
native tools and matching source materials. Source/build recipes are retained
under `tools/`; original engine and platform changes retain their own notices.

Run transaction/package regression tests on Linux with `make test`. To rebuild
the asset-free package, unpack the release and use its `runtime/` as the reviewed
compiler/library template:

```sh
python3 tools/package.py --runtime /path/to/unpacked/runtime \
  --sdk /path/to/lilsmoke-sdk --output SM64-LilSmoke-Pre-Alpha-0.0.1.lilpck
```

This rebuild compiles the authored UI/input code and conversion tools from source.
It does not need a ROM, cook game data, or distribute a graphics binary. The
native compiler, Python and dependency rebuilding recipes and matching source
materials are also retained in the package's `runtime/corresponding-source/`.
The runtime supplies musl libraries; DirectFB and hardware display/audio drivers
are part of the LilSmokeOS platform contract.

## License

Authored LilSmoke adaptations are **MPL-2.0**. Upstream game-engine source,
graphics code, fonts, tools, compiler and libraries retain their individual
licenses. `LICENSE` is not a blanket grant for Nintendo's game assets or for
redistribution of the locally generated original-game runtime.

Release acceptance must include fresh-ROM extraction, controller cancellation,
original-game launch, restart without the ROM, saves and the target RAM budget.
See `RELEASE-STATUS.json` for the exact completed tests and outstanding limits.

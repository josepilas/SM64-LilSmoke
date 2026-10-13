#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""First-run native build backend for the original SM64 engine."""
import argparse
import hashlib
import json
import os
import re
from pathlib import Path, PurePosixPath
import shlex
import shutil
import signal
import sys
import tarfile

from cook import Cache, Cancelled, digest_file, emit, regular, run_stage, verify_mips_executable


def extract_source(archive, work, expected, cancel=lambda: False):
    regular(archive, 32 * 1024 * 1024)
    if digest_file(archive) != expected:
        raise ValueError('Bundled engine source failed checksum')
    work.mkdir()
    total = 0
    seen = set()
    with tarfile.open(archive, 'r|gz') as source:
        for item in source:
            if cancel():
                raise Cancelled('Preparation cancelled; original ROM kept')
            name = PurePosixPath(item.name)
            if name.is_absolute() or '..' in name.parts or '\\' in item.name or not name.parts:
                raise ValueError('Unsafe source archive path')
            if item.name.casefold() in seen or not (item.isdir() or item.isfile()):
                raise ValueError('Source archive contains duplicates or links')
            seen.add(item.name.casefold())
            total += item.size
            if total > 128 * 1024 * 1024 or len(seen) > 10000:
                raise ValueError('Source archive exceeds the device budget')
            source.extract(item, work, filter='data')


class NativeBuild:
    def __init__(self, app):
        self.app = Path(app).absolute()
        self.runtime = self.app / 'runtime'
        recipe_path = self.runtime / 'recipe.json'
        regular(recipe_path, 65536)
        self.recipe = json.loads(recipe_path.read_text())
        if self.recipe.get('schema') != 1 or self.recipe.get('version') != 'Pre-Alpha-0.0.1':
            raise ValueError('Unsupported bundled build recipe')
        self.identity = digest_file(recipe_path)
        self.cancelled = False

    def cancel(self, *_):
        self.cancelled = True

    def build(self, stage, progress, cancel):
        work = stage / 'source'
        extract_source(self.runtime / 'source.tar.gz', work, self.recipe['source_sha256'], cancel)
        shutil.copyfile(stage / 'baserom.us.z64', work / 'baserom.us.z64')
        tools = self.runtime / 'compiler/wrappers'
        python = self.runtime / 'python/usr/bin/python3.11'
        sysroot = self.runtime / 'sysroot'
        env_path = str(tools) + ':' + str(self.runtime / 'bin') + ':/usr/bin:/bin'
        os.environ['PATH'] = env_path
        os.environ['PYTHONHOME'] = str(self.runtime / 'python/usr')
        os.environ['PYTHONDONTWRITEBYTECODE'] = '1'
        os.environ['LC_ALL'] = 'C'
        os.environ['MAKEFLAGS'] = '-j1'
        os.environ['OMP_NUM_THREADS'] = '1'
        os.environ.pop('LD_PRELOAD', None)
        asset_map = json.loads((work / 'assets.json').read_text())
        us_assets = {name: value[-2] for name, value in asset_map.items()
            if not name.startswith('@') and 'us' in value[-1]}
        total_assets = len(us_assets)
        pending = None
        completed = set()
        extracted = 0

        def extracted_line(line):
            nonlocal pending, extracted
            if pending is not None:
                path = work / pending
                if path.is_file() and not path.is_symlink():
                    extracted += path.stat().st_size
                    completed.add(pending)
                    pending = None
            if line.startswith('extracting '):
                name = line[len('extracting '):]
                if name in us_assets and name not in completed:
                    pending = name
            progress('Extracting assets', 10 + 40 * len(completed) // max(1, total_assets), extracted)

        run_stage([str(python), '-u', 'extract_assets.py', 'us'], work,
            stage / 'extraction.log', extracted_line, cancel)
        extracted_line('')
        # The byte counter counts actual extracted files, not the input ROM size.
        extracted = sum((work / name).stat().st_size for name in us_assets
            if (work / name).is_file() and not (work / name).is_symlink())
        progress('Preparing engine', 50, extracted)

        return self.compile(stage,work,extracted,progress,cancel)

    def compile(self,stage,work,extracted,progress,cancel):
        tools=self.runtime/'compiler/wrappers'
        python=self.runtime/'python/usr/bin/python3.11'
        sysroot=self.runtime/'sysroot'

        flags = ['-O2', '-mips32r2', '-mtune=74kc', '-mabi=32', '-mfp32', '-mhard-float',
            '-mdspr2', '-fno-fast-math', '-fno-strict-aliasing', '-fwrapv', '-fcommon',
            '--param=ggc-min-expand=30', '--param=ggc-min-heapsize=32768',
            '-D_LANGUAGE_C', '-DVERSION_US', '-DNOWUP', '-DNON_MATCHING', '-DAVOID_UB',
            '-DTARGET_LINUX', '-DNO_SEGMENTED_MEMORY', '-DENABLE_SOFTRAST', '-DF3DEX_GBI_2E',
            '-DCONVERT', '-nostdinc', '-isystem', str(self.runtime / 'compiler/usr/lib/gcc/mipsel-linux-gnu/12/include'),
            '-isystem', str(sysroot / 'usr/include'), '-Iinclude', '-Isrc', '-I.',
            '-Ibuild/original-mips/include', '-Ibuild/original-mips', '-Ililsmoke',
            '-I' + str(sysroot / 'usr/include/SDL2')]
        cflags = shlex.join(flags)
        cc = shlex.quote(str(tools / 'cc'))
        build = work / 'build/original-mips'
        build.mkdir(parents=True, exist_ok=True)
        for name in ('lilsmoke-input.o', 'lilsmoke-common-input.o', 'lilsmoke-keymap.o', 'lilsmoke-log.o'):
            source = self.runtime / 'objects' / name
            regular(source, 4 * 1024 * 1024)
            shutil.copyfile(source, build / name)
        command = [str(tools / 'make'), '-j1', '-f', 'Makefile', '-f', 'lilsmoke/public.mk',
            'VERSION=us', 'COMPILER=gcc', 'TARGET_FUNKEY=0', 'TARGET_OD=0',
            'ENABLE_OPENGL=0', 'ENABLE_OPENGL_LEGACY=0', 'ENABLE_SOFTRAST=1',
            'NOEXTRACT=1', 'BUILD_DIR=build/original-mips', 'CC=' + cc,
            'MAKE=' + shlex.quote(str(tools / 'make')),
            'CPP=' + cc + ' -E -x c -P -Wno-trigraphs', 'CC_CHECK=' + cc + ' -fsyntax-only',
            'AS=' + shlex.quote(str(tools / 'as')), 'OBJCOPY=' + shlex.quote(str(tools / 'objcopy')),
            'CC_CHECK_CFLAGS=' + cflags, 'CFLAGS=' + cflags,
            'PYTHON=' + shlex.quote(str(python)), 'RUNTIME_SYSROOT=' + shlex.quote(str(sysroot)),
            'RUNTIME_LIB=' + shlex.quote(str(self.runtime / 'lib'))]
        count = 0
        total_objects = self.recipe['compile_jobs']

        def compiled_line(line):
            nonlocal count
            if ' -c ' in line and ' -o ' in line:
                count += 1
                progress('Preparing engine', min(95, 50 + count * 45 // max(1, total_objects)), extracted)

        run_stage(command, work, stage / 'build.log', compiled_line, cancel)
        generated = work / 'build/original-mips/sm64.us.f3dex2e'
        regular(generated, 32 * 1024 * 1024)
        verify_mips_executable(generated)
        validation_home=stage/'validation-saves'
        validation_home.mkdir(exist_ok=True)
        validation=dict(os.environ, HOME=str(validation_home),
            SDL_AUDIODRIVER='dummy',LILSMOKE_HCFB='0',SM64_ORIGINAL_PREFLIGHT='1',
            LILSMOKE_M8_AUDIO='0',SM64_ORIGINAL_TEST_FRAMES='30',SM64_ORIGINAL_RENDER_EVERY='1',
            LD_LIBRARY_PATH=str(self.runtime/'lib')+':/usr/lib:/lib')
        for key in ('SM64_ORIGINAL_EVIDENCE','SM64_ORIGINAL_INTERACTIVE_AFTER','SM64_SOFTWARE_WIDTH'):
            validation.pop(key,None)
        completed=False
        def validated_line(line):
            nonlocal completed
            if re.search(r'OriginalEngineTest=FINISHED Frames=30 Rendered=30(?: |$)',line):
                completed=True
        run_stage([str(generated)],work,stage/'validation.log',validated_line,cancel,
            env=validation,timeout=60)
        if not completed:
            raise RuntimeError('Cooked original engine did not finish the render validation; original ROM kept')
        shutil.copyfile(generated, stage / 'game')
        progress('Verifying assets', 98, extracted)
        # Only the verified locally linked game and small diagnostic logs persist.
        shutil.rmtree(work)
        shutil.rmtree(validation_home)
        return stage / 'game', extracted


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--app', type=Path, required=True)
    args = parser.parse_args()
    builder = NativeBuild(args.app)
    signal.signal(signal.SIGTERM, builder.cancel)
    signal.signal(signal.SIGINT, builder.cancel)
    try:
        game = Cache(args.app, builder.identity).prepare(builder.build, emit, lambda: builder.cancelled)
        print(json.dumps(dict(ready=str(game))), flush=True)
    except (OSError, ValueError, RuntimeError) as error:
        print(json.dumps(dict(error=str(error))), flush=True)
        return 130 if isinstance(error, Cancelled) else 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

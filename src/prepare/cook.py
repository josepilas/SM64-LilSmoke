#!/usr/bin/env python3
# SPDX-License-Identifier: MPL-2.0
"""BYO-ROM preparation transactions; never distribute the generated cache."""
import errno
import hashlib
import json
import os
from pathlib import Path
import signal
import stat
import struct
import subprocess
import tempfile
import time
import uuid

US_SHA1 = '9bef1128717f958171a4afac3ed78ee2bb4e86ce'
ROM_BYTES = 8 * 1024 * 1024
BLOCK_BYTES = 128 * 1024
MAGICS = {b'\x80\x37\x12\x40': 1, b'\x37\x80\x40\x12': 2, b'\x40\x12\x37\x80': 4}


class Cancelled(RuntimeError):
    pass


def regular(path, maximum=None):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
        raise ValueError('Expected a regular, unshared file')
    if maximum is not None and info.st_size > maximum:
        raise ValueError('File exceeds the memory/storage budget')
    return info


def directory(path):
    if not stat.S_ISDIR(path.lstat().st_mode):
        raise ValueError('Expected a real directory, not a link')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            os.fsync(fd)
        except OSError as error:
            if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                raise
            os.sync()
    finally:
        os.close(fd)


def atomic_json(path, value):
    fd, temporary = tempfile.mkstemp(prefix='.commit-', dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(value, stream, sort_keys=True, separators=(',', ':'))
            stream.write('\n')
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
        sync_directory(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def identity(info):
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)


def normalized_chunks(fd, cancel=lambda: False):
    os.lseek(fd, 0, os.SEEK_SET)
    header = os.read(fd, 4)
    order = MAGICS.get(header)
    if order is None:
        raise ValueError('Unsupported N64 ROM byte order')
    os.lseek(fd, 0, os.SEEK_SET)
    while True:
        if cancel():
            raise Cancelled('Preparation cancelled; original ROM kept')
        block = os.read(fd, BLOCK_BYTES)
        if not block:
            return
        if len(block) % 4:
            raise ValueError('Truncated N64 ROM')
        if order == 2:
            output = bytearray(len(block))
            output[0::2], output[1::2] = block[1::2], block[0::2]
            block = output
        elif order == 4:
            output = bytearray(len(block))
            for at in range(4):
                output[at::4] = block[3-at::4]
            block = output
        yield block


class Rom:
    def __init__(self, path, expected=US_SHA1, cancel=lambda: False):
        self.path = Path(path)
        self.fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            self.before = os.fstat(self.fd)
            if not stat.S_ISREG(self.before.st_mode) or self.before.st_nlink != 1 or self.before.st_size != ROM_BYTES:
                raise ValueError('Only a regular 8 MiB US 1.0 ROM is supported')
            self.expected = expected
            self.verify(cancel)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None

    def verify(self, cancel=lambda: False):
        digest = hashlib.sha1()
        for block in normalized_chunks(self.fd, cancel):
            digest.update(block)
        if identity(os.fstat(self.fd)) != identity(self.before):
            raise ValueError('ROM changed during preparation')
        if digest.hexdigest() != self.expected:
            raise ValueError('Unsupported ROM: US 1.0 contents are required')

    def write_normalized(self, output, progress, cancel=lambda: False):
        total = 0
        fd = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            for block in normalized_chunks(self.fd, cancel):
                stream.write(block)
                total += len(block)
                progress('Checking ROM', total * 10 // ROM_BYTES, 0)
            stream.flush()
            os.fsync(stream.fileno())
        self.verify(cancel)


def discover(original, cancel=lambda: False, expected=US_SHA1):
    directory(original)
    matches = []
    rejected = False
    try:
        for item in sorted(original.iterdir()):
            info = item.lstat()
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size != ROM_BYTES:
                continue
            try:
                matches.append(Rom(item, expected, cancel))
            except ValueError:
                rejected = True
        if len(matches) > 1:
            raise ValueError('Keep exactly one supported ROM inside original/')
        if not matches:
            raise ValueError('Unsupported ROM in original/' if rejected else 'Put your US 1.0 ROM in original/')
        return matches.pop()
    finally:
        for rom in matches:
            rom.close()


def digest_file(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(BLOCK_BYTES), b''):
            digest.update(block)
    return digest.hexdigest()


def verify_mips_executable(path):
    info = regular(path, 32 * 1024 * 1024)
    with path.open('rb') as stream:
        header = stream.read(52)
        if len(header) != 52 or header[:7] != b'\x7fELF\x01\x01\x01':
            raise ValueError('Cooked game is not a little-endian ELF32 executable')
        kind, machine = struct.unpack_from('<HH', header, 16)
        offset = struct.unpack_from('<I', header, 28)[0]
        size, count = struct.unpack_from('<HH', header, 42)
        if machine != 8 or kind not in (2, 3) or size != 32 or not 0 < count <= 64 or offset + size * count > info.st_size:
            raise ValueError('Invalid MIPS executable program table')
        interpreter = None
        for index in range(count):
            stream.seek(offset + index * size)
            entry = stream.read(size)
            program, file_offset, _, _, file_size = struct.unpack_from('<IIIII', entry)
            if file_offset + file_size > info.st_size:
                raise ValueError('Truncated MIPS executable segment')
            if program == 3:
                if interpreter is not None or not 2 <= file_size <= 128:
                    raise ValueError('Invalid executable interpreter')
                stream.seek(file_offset)
                interpreter = stream.read(file_size)
        if interpreter != b'/lib/ld-musl-mipsel.so.1\0':
            raise ValueError('Game must link to the LilSmoke musl runtime, not compiler glibc')


class Cache:
    def __init__(self, app, recipe):
        self.app = Path(app).absolute()
        directory(self.app)
        self.root = self.app / '.sm64-cache'
        self.root.mkdir(exist_ok=True)
        directory(self.root)
        self.recipe = recipe

    def current(self):
        try:
            path = self.root / 'current.json'
            regular(path, 4096)
            value = json.loads(path.read_text())
            generation = value['generation']
            if not isinstance(generation, str) or not generation.startswith('ready-') or len(generation) > 80 or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789-' for c in generation):
                return None
            if value['recipe'] != self.recipe or value['rom_sha1'] != US_SHA1:
                return None
            folder = self.root / generation
            directory(folder)
            game = folder / 'game'
            verify_mips_executable(game)
            if digest_file(game) != value['game_sha256']:
                return None
            return game
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def prepare(self, builder, progress, cancel=lambda: False, delete_original=True):
        lock = self.root / 'prepare.lock'
        lock_fd = os.open(lock, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        # The kernel releases this lock on exit/power loss; there is no stale PID lock.
        import fcntl
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            game = self.current()
            if game:
                if delete_original and (self.app / 'original').is_dir():
                    try:
                        rom = discover(self.app / 'original', cancel)
                    except ValueError:
                        rom = None
                    if rom is not None:
                        try:
                            self.remove_original(self.app / 'original', rom, cancel)
                        finally:
                            rom.close()
                progress('Assets ready', 100, 0)
                return game
            original = self.app / 'original'
            rom = discover(original, cancel)
            try:
                stage = Path(tempfile.mkdtemp(prefix='working-', dir=self.root))
                rom.write_normalized(stage / 'baserom.us.z64', progress, cancel)
                game, extracted = builder(stage, progress, cancel)
                if game != stage / 'game':
                    raise ValueError('Builder returned a file outside the preparation transaction')
                if cancel():
                    raise Cancelled('Preparation cancelled; original ROM kept')
                verify_mips_executable(game)
                # VFAT exposes mount-wide modes and may reject a private chmod.
                if not os.access(game,os.X_OK):
                    game.chmod((game.stat().st_mode & 0o777) | 0o100)
                if not os.access(game,os.X_OK):
                    raise ValueError('Storage does not permit native app execution; original ROM kept')
                with game.open('rb') as stream:
                    os.fsync(stream.fileno())
                game_hash = digest_file(game)
                # Scratch assets stay private and are not necessary after final linkage.
                (stage / 'baserom.us.z64').unlink()
                sync_directory(stage)
                ready = self.root / ('ready-' + uuid.uuid4().hex)
                stage.rename(ready)
                sync_directory(self.root)
                atomic_json(self.root / 'current.json', dict(schema=1, recipe=self.recipe,
                    rom_sha1=US_SHA1, generation=ready.name, game_sha256=game_hash,
                    extracted_bytes=extracted))
                result = self.current()
                if result is None:
                    raise ValueError('Durable cache failed readback; original ROM kept')
                if delete_original and not cancel():
                    self.remove_original(original, rom, cancel)
                progress('Assets ready', 100, extracted)
                return result
            finally:
                rom.close()
        finally:
            os.close(lock_fd)

    def remove_original(self, original, rom, cancel):
        rom.verify(cancel)
        fd = os.open(original, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            current = os.stat(rom.path.name, dir_fd=fd, follow_symlinks=False)
            if identity(current) != identity(rom.before) or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1:
                raise ValueError('Original ROM path changed; no ROM was deleted')
            if cancel():
                raise Cancelled('Preparation cancelled; original ROM kept')
            os.unlink(rom.path.name, dir_fd=fd)
            try:
                os.fsync(fd)
            except OSError as error:
                if error.errno not in (errno.EINVAL, errno.ENOTSUP):
                    raise
                os.sync()
        finally:
            os.close(fd)


def run_stage(command, cwd, log, on_line, cancel=lambda: False, env=None, timeout=3600):
    """Run trusted build recipes, terminate their whole process group on cancel."""
    import selectors
    process = subprocess.Popen(command, cwd=cwd, stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT, start_new_session=True, text=False, env=env)
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    pending = bytearray()
    deadline=time.monotonic()+timeout
    try:
        with log.open('wb') as output:
            while selector.get_map():
                if cancel():
                    raise Cancelled('Preparation cancelled; original ROM kept')
                if time.monotonic()>deadline:
                    raise RuntimeError('Preparation step timed out; original ROM kept')
                for key, _ in selector.select(0.1):
                    data = os.read(key.fileobj.fileno(), 8192)
                    if not data:
                        selector.unregister(key.fileobj)
                        continue
                    output.write(data)
                    pending.extend(data)
                    while b'\n' in pending:
                        line, _, pending = pending.partition(b'\n')
                        on_line(line.decode('utf-8', 'replace'))
                    if len(pending) > 65536:
                        pending.clear()
            if process.wait() != 0:
                raise RuntimeError('Local asset build failed; see the private preparation log')
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
        selector.close()
        process.stdout.close()


def emit(phase, percentage, extracted_bytes):
    print(json.dumps(dict(phase=phase, percent=min(100, max(0, percentage)),
        extracted_bytes=extracted_bytes)), flush=True)

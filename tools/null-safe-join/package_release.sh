#!/usr/bin/env bash
# Licensed to the Apache Software Foundation (ASF) under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  The ASF licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#   http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing,
# software distributed under the License is distributed on an
# "AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
# KIND, either express or implied.  See the License for the
# specific language governing permissions and limitations
# under the License.

set -euo pipefail

# Python's standard library keeps ELF inspection and manifests portable; the
# packaged BE must still be Linux x86_64. This script never executes the BE.
exec python3 - "$@" <<'PY'
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import struct
import subprocess
import sys
import tarfile
import tempfile
import zipfile


def fail(message):
    raise ValueError(message)


def sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def git(source, *args):
    return subprocess.check_output(
        ["git", "-C", str(source), *args], text=True, stderr=subprocess.PIPE
    ).strip()


def inspect_elf(path):
    with path.open("rb") as stream:
        header = stream.read(64)
    if len(header) != 64 or header[:7] != b"\x7fELF\x02\x01\x01":
        fail("be/lib/doris_be must be a 64-bit little-endian ELF file")
    kind, machine, version = struct.unpack_from("<HHI", header, 16)
    if machine != 62 or kind not in (2, 3) or version != 1:
        fail("be/lib/doris_be must be an x86-64 ELF executable (EM_X86_64=62)")
    return {"format": "ELF64", "endianness": "little", "machine": "x86-64",
            "e_machine": machine, "e_type": kind}


def inspect_binaries(root, required_files):
    for relative in required_files:
        path = root / relative
        if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
            fail(f"missing, empty or symlinked required output file: {relative}")
    if not os.access(root / "be/lib/doris_be", os.X_OK):
        fail("be/lib/doris_be is not executable")
    elf = inspect_elf(root / "be/lib/doris_be")
    with zipfile.ZipFile(root / "fe/lib/doris-fe.jar") as jar:
        if "org/apache/doris/DorisFE.class" not in jar.namelist():
            fail("doris-fe.jar does not contain org/apache/doris/DorisFE.class")
        if jar.testzip() is not None:
            fail("doris-fe.jar failed ZIP CRC validation")
    return elf


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(
        prog="package_release.sh",
        description="Package completed build.sh FE/BE output; does not build, test or install Doris."
    )
    parser.add_argument("--source", required=True, type=Path, help="clean source checkout used by the build")
    parser.add_argument("--output", required=True, type=Path, help="completed build.sh output containing fe/ and be/")
    parser.add_argument("--destination", required=True, type=Path, help="new delivery directory; must not exist")
    parser.add_argument("--build-record", required=True, type=Path, help="JSON recording the actual successful build")
    parser.add_argument("--name", help="optional archive root name (letters, digits, dot, underscore, hyphen)")
    args = parser.parse_args()
    source = args.source.resolve(strict=True)
    output = args.output.resolve(strict=True)
    destination = args.destination.absolute()
    if destination.exists() or destination.is_symlink():
        fail("destination already exists; choose a new delivery directory")
    destination = destination.parent.resolve(strict=True) / destination.name
    if destination == output or output in destination.parents or source in destination.parents:
        fail("destination must be outside both the source checkout and build output")

    required_files = (
        "be/lib/doris_be", "fe/lib/doris-fe.jar", "be/bin/start_be.sh", "be/bin/stop_be.sh",
        "fe/bin/start_fe.sh", "fe/bin/stop_fe.sh", "be/conf/be.conf", "fe/conf/fe.conf",
        "be/LICENSE-dist.txt", "fe/LICENSE-dist.txt", "be/NOTICE.txt", "fe/NOTICE.txt",
    )
    inspect_binaries(output, required_files)

    record_path = args.build_record.resolve(strict=True)
    record_bytes = record_path.read_bytes()
    record = json.loads(record_bytes.decode("utf-8"))
    if not isinstance(record, dict) or type(record.get("build_exit_code")) is not int or record["build_exit_code"] != 0:
        fail("build record must report actual build_exit_code 0")
    for key in ("source_commit", "build_type", "build_command", "completed_at_utc"):
        if not isinstance(record.get(key), str) or not record[key].strip():
            fail(f"build record requires nonempty string: {key}")
    if not isinstance(record.get("options"), dict) or not record["options"]:
        fail("build record requires a nonempty options object (including CPU/build options)")
    if not re.fullmatch(r"[0-9a-f]{40}", record["source_commit"]):
        fail("source_commit must be the full 40-character build commit")
    if not re.fullmatch(r"[A-Za-z0-9_-]+", record["build_type"]):
        fail("invalid build_type")
    head = git(source, "rev-parse", "HEAD")
    if Path(git(source, "rev-parse", "--show-toplevel")).resolve() != source:
        fail("source must be the Git checkout root")
    if record["source_commit"] != head:
        fail("build record source_commit differs from source checkout HEAD")
    if git(source, "status", "--porcelain", "--untracked-files=all"):
        fail("source checkout has tracked or non-ignored untracked changes; commit the intended build inputs first")
    name = args.name or f"doris-fork-{head[:12]}-linux-x86_64-{record['build_type'].lower()}"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
        fail("invalid package name")
    if name.casefold() == "package-manifest":
        fail("package-manifest is reserved for delivery metadata")

    # These are the component-level release directories populated by build.sh.
    release_dirs = {
        "fe": {"bin", "conf", "lib", "webroot", "arthas", "mysql_ssl_default_certificate", "licenses", "plugins"},
        "be": {"bin", "conf", "dict", "lib", "tools", "www", "licenses", "plugins"},
    }
    common_files = {"LICENSE-dist.txt", "NOTICE.txt"}
    runtime_dirs = {"log", "logs", "doris-meta", "storage", "temp_dir"}
    inputs = []
    for component, allowed in release_dirs.items():
        root = output / component
        if root.is_symlink():
            fail(f"component directory cannot be a symlink: {root}")
        for required in ("bin", "conf", "lib", "licenses"):
            if not (root / required).is_dir() or not any((root / required).iterdir()):
                fail(f"missing or empty release directory: {component}/{required}")
        for child in sorted(root.iterdir()):
            if child.name in runtime_dirs:
                if child.is_symlink() or not child.is_dir() or any(not p.is_dir() for p in child.rglob("*")):
                    fail(f"runtime directory contains data; use unused build output: {component}/{child.name}")
                continue
            if child.name not in allowed | common_files:
                fail(f"unknown component entry; review before packaging: {component}/{child.name}")
            inputs.append(child)
            for path in [child, *child.rglob("*")] if child.is_dir() else [child]:
                relative = path.relative_to(output)
                if any(c in str(relative) for c in "\n\r\\"):
                    fail(f"unsupported manifest filename: {relative!s}")
                mode = path.lstat().st_mode
                if path.is_symlink():
                    target = os.readlink(path)
                    if os.path.isabs(target) or root not in path.resolve(strict=True).parents:
                        fail(f"symlink must resolve inside its component: {relative}")
                elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                    fail(f"special filesystem entry cannot be packaged: {relative}")
                if any(part in runtime_dirs for part in relative.parts[1:]) or path.suffix in (".log", ".pid"):
                    fail(f"runtime file/directory found in release payload: {relative}")

    # All validation above is read-only. Only this newly created temporary tree is mutated.
    temporary = Path(tempfile.mkdtemp(prefix=".doris-package-", dir=destination.parent))
    try:
        package = temporary / name
        package.mkdir()
        for path in inputs:
            target = package / path.relative_to(output)
            target.parent.mkdir(parents=True, exist_ok=True)
            if path.is_dir() and not path.is_symlink():
                shutil.copytree(path, target, symlinks=True)
            elif path.is_symlink():
                target.symlink_to(os.readlink(path))
            else:
                shutil.copy2(path, target)
        for component in release_dirs:
            root = package / component
            for path in root.rglob("*"):
                if path.is_symlink() and (os.path.isabs(os.readlink(path))
                                         or root not in path.resolve(strict=True).parents):
                    fail(f"packaged symlink escapes its component: {path}")
        # The manifest describes the copied payload, not an earlier input header.
        elf = inspect_binaries(package, required_files)
        if git(source, "rev-parse", "HEAD") != head or git(source, "status", "--porcelain", "--untracked-files=all"):
            fail("source checkout changed while packaging")

        metadata = package / "package-manifest"
        metadata.mkdir()
        write_json(metadata / "BUILD-INFO.json", {
            "package_name": name,
            "packaged_at_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "source_commit": head, "source_checkout_clean": True,
            "source_checkout": str(source), "build_output": str(output),
            "build_record": record, "build_record_sha256": hashlib.sha256(record_bytes).hexdigest(),
            "be_elf": elf,
            "build_record_provenance": "caller-supplied; not an independently executed build",
            "validation_scope": "ELF header, FE entry class/ZIP CRC, release layout, clean source, file hashes",
            "not_validated": ["compiler-to-output provenance", "CPU instruction support", "dynamic library compatibility",
                              "FE/BE startup", "unit or regression tests", "upgrade compatibility"],
            "excluded_runtime_directories": sorted(runtime_dirs),
            "components": ["fe", "be"],
        })
        entries = []
        for path in sorted(package.rglob("*")):
            info = path.lstat()
            entry = {"path": path.relative_to(package).as_posix(), "mode": oct(stat.S_IMODE(info.st_mode))}
            if path.is_symlink():
                entry.update(type="symlink", target=os.readlink(path))
            elif path.is_dir():
                entry.update(type="directory")
            else:
                entry.update(type="file", size=info.st_size, sha256=sha256(path))
            entries.append(entry)
        write_json(metadata / "FILES.json", {"scope": "payload and BUILD-INFO.json; excludes FILES.json and SHA256SUMS", "entries": entries})
        checksums = [f"{entry['sha256']}  {entry['path']}\n" for entry in entries if entry["type"] == "file"]
        checksums.append(f"{sha256(metadata / 'FILES.json')}  package-manifest/FILES.json\n")
        (metadata / "SHA256SUMS").write_text("".join(checksums), encoding="utf-8")
        archive = temporary / f"{name}.tar.gz"
        with tarfile.open(archive, "w:gz", compresslevel=1) as tar:
            tar.add(package, arcname=name)
        (temporary / f"{archive.name}.sha256").write_text(f"{sha256(archive)}  {archive.name}\n", encoding="utf-8")
        shutil.copytree(metadata, temporary / "package-manifest")
        shutil.rmtree(package)
        # mkdir reserves the new destination atomically. rename of a directory
        # alone can overwrite an empty directory created after an exists check.
        destination.mkdir()
        reserved = destination.stat()
        published = [(destination, reserved.st_dev, reserved.st_ino)]
        try:
            for path in sorted(temporary.rglob("*")):
                target = destination / path.relative_to(temporary)
                if path.is_dir():
                    target.mkdir()
                else:
                    # Same-filesystem hard links publish complete files and fail
                    # if another writer has already created the target name.
                    os.link(path, target)
                info = target.lstat()
                published.append((target, info.st_dev, info.st_ino))
        except OSError:
            # Only remove entries created by this invocation, still with the
            # recorded identity. rmdir preserves directories with other content.
            for path, device, inode in reversed(published):
                try:
                    info = path.lstat()
                    if (info.st_dev, info.st_ino) == (device, inode):
                        path.rmdir() if stat.S_ISDIR(info.st_mode) else path.unlink()
                except OSError:
                    pass
            raise
        print(f"Packaged: {destination / archive.name}")
        print("Packaging only: runtime, regression and upgrade validation are still separate.")
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


try:
    main()
except (OSError, ValueError, subprocess.CalledProcessError, zipfile.BadZipFile, tarfile.TarError) as error:
    print(f"Packaging refused: {error}", file=sys.stderr)
    sys.exit(1)
PY

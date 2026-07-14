# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

"""
Minor utility functions
"""
from __future__ import annotations

import os
import re
import threading
import time
from functools import wraps
from typing import Any, Callable

from maya.app.general.fileTexturePathResolver import _patternToRegex


def _get_dir_timeout() -> float:
    """
    Maximum number of seconds to wait for a single directory scan before treating
    the directory as unreachable. Guards asset detection against hangs on slow or
    disconnected network shares. Override with the DEADLINE_MAYA_DIR_TIMEOUT
    environment variable.
    """
    try:
        return float(os.environ.get("DEADLINE_MAYA_DIR_TIMEOUT", "10"))
    except (TypeError, ValueError):
        return 10.0


def _run_with_timeout(func: Callable, timeout: float, *args: Any) -> Any:
    """
    Runs a potentially blocking filesystem call in a daemon thread and waits at most
    `timeout` seconds for it to complete.

    Returns the function's result on success. Raises TimeoutError if it does not
    finish in time, or re-raises any exception raised by `func`.

    Note: on timeout the underlying OS call may keep running in the background
    daemon thread. We stop waiting on it so the submitter UI does not hang on an
    unreachable directory; the daemon thread is abandoned and dies with the process.
    """
    result: dict[str, Any] = {}

    def target() -> None:
        try:
            result["value"] = func(*args)
        except BaseException as e:  # noqa: BLE001
            result["error"] = e

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    thread.join(timeout)

    if thread.is_alive():
        raise TimeoutError(f"timed out after {timeout}s")
    if "error" in result:
        raise result["error"]
    return result.get("value")


# Per-scan cache of directory listings, keyed by directory path. Asset
# detection resolves many references (and, for animated/UDIM textures, many
# animation frames of a single reference) into the same directory; without this
# cache each one re-enumerates the whole directory. On network shares that
# directory enumeration is a latency-bound round-trip, so repeating it is the
# dominant cost. The cache turns it into at most one listing per directory per
# scan. Cleared at the start of each scan via clear_directory_cache().
#
# The listing is built with os.scandir (not os.listdir + os.path.isfile): scandir
# returns each entry's file-vs-dir type from the single directory read, so the
# "is this a regular file?" check costs no extra stat syscall. On Windows/SMB the
# directory enumeration already carries file attributes, so this removes a
# per-matched-file network round-trip — the bulk of the remaining slowness.
_DIR_LISTING_CACHE: dict[str, list[str]] = {}


def clear_directory_cache() -> None:
    """
    Clear the per-scan directory listing cache.

    Call this at the start of each asset-detection scan so a fresh submission
    sees the current state of disk rather than listings cached from a prior run.
    """
    _DIR_LISTING_CACHE.clear()


def _list_dir_files(dirname: str) -> list[str]:
    """
    Names of the regular files in `dirname`, cached for the duration of a scan
    and guarded by a timeout.

    Uses os.scandir so the file-vs-dir test reuses the directory read instead of
    issuing an os.path.isfile stat per entry. The same directory is enumerated at
    most once per scan even when many file references (or many animation frames of
    one reference) resolve into it. Returns [] for a missing or unreachable
    directory; on a timeout the empty result is cached so a dead share is not
    retried for every reference/frame.
    """
    cached = _DIR_LISTING_CACHE.get(dirname)
    if cached is not None:
        return cached

    def _scan() -> list[str]:
        # Keep the existence check: scandir errors out if the dir doesn't exist.
        if not os.path.isdir(dirname):
            return []
        names: list[str] = []
        # entry.is_file() follows symlinks (matching the previous os.path.isfile
        # behavior) and uses the dirent type cached by scandir, so it does not
        # add a stat per entry on the common path.
        with os.scandir(dirname) as it:
            for entry in it:
                try:
                    if entry.is_file():
                        names.append(entry.name)
                except OSError:
                    # Broken symlink / racing deletion: treat as not a regular file.
                    continue
        return names

    timeout = _get_dir_timeout()
    try:
        entries = _run_with_timeout(_scan, timeout)
    except TimeoutError:
        print(
            f"Warning: Timed out after {timeout:.0f}s scanning directory '{dirname}'. "
            "The directory may be on a slow or unreachable network share, or contain a "
            "very large number of files. Skipping it so asset detection can continue."
        )
        entries = []

    _DIR_LISTING_CACHE[dirname] = entries
    return entries


def join_paths(first: str, *remainder: str) -> str:
    """
    Wrapper for os.path.join which replaces all backslashes (maya only uses forward slashes.)
    """
    return os.path.join(first, *remainder).replace("\\", "/")


def timed_func(func: Callable):
    """Decorator that wraps a function gives performance timing"""

    @wraps(func)
    def wrapped(*args, **kwargs):
        start = time.perf_counter()
        result = func(*args, **kwargs)
        end = time.perf_counter()
        elapsed = end - start
        print(
            f"func: {func.__name__} with args: {args}, kwargs: {kwargs}, took {elapsed:.3f} seconds"
        )
        return result

    return wrapped


def findAllFilesForPattern(pattern: str, frameNumber: int) -> list[str]:
    """
    As of Maya 2023, a replacement for maya.app.general.fileTexturePathResolver.findAllFilesForPattern

    This is a faster version of the function provided by Maya, since it
    only does the file existence check after it verifies the regex matches.

    We've also removed a _split_path function call that found the result of os.path.split and
    the original path separator between directory and filename. We don't care about the
    original path separator since we immediately turn this into Path objects. We do not
    return a list of Paths here so that we can keep the same interface as the original
    function.

    Original Doc string:
            Given a path, possibly containing tags in the file name, find all files in
            the same directory that match the tags. If none found, just return pattern
            that we looked for.
    """
    dirname, basename = os.path.split(pattern)
    if not (dirname and basename):
        return []

    # Enumerate the directory once per scan (cached, timeout-guarded). The listing
    # is already filtered to regular files via scandir, so no per-file os.path.isfile
    # stat is needed here. The resulting set is identical to the previous
    # listdir + isfile implementation.
    files = _list_dir_files(dirname)
    if not files:
        return []

    local_basename = basename
    if frameNumber is not None:
        # _patternToRegex handles frame tokens, but this is for only finding files for a specific frame
        local_basename = local_basename.replace("<f>", "0*" + str(frameNumber))
        local_basename = local_basename.replace("<frame>", "0*" + str(frameNumber))

    try:
        regex = _patternToRegex(local_basename)
    except re.error as e:
        # Handle paths with regex special characters (e.g., "/path/to/cache[1].bif")
        # Brackets cause "bad character range" errors when compiled as regex patterns.
        # Use re.escape to treat the basename as a literal string instead of a pattern.
        print(f"Warning: Regex error for pattern '{pattern}': {e}. Using literal matching.")
        regex = re.escape(local_basename)

    return [os.path.join(dirname, f) for f in files if re.match(regex, f, flags=re.IGNORECASE)]

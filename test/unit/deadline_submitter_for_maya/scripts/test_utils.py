# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import re
import time
from unittest.mock import MagicMock, patch

import pytest

from deadline.maya_submitter.utils import join_paths, timed_func
from deadline.maya_submitter import utils as utils_module


@pytest.fixture(autouse=True)
def _clear_dir_cache():
    """Isolate the per-scan directory listing cache between tests."""
    utils_module.clear_directory_cache()
    yield
    utils_module.clear_directory_cache()


def _scandir_cm(names, is_file=True):
    """
    Build a context-manager mock mimicking os.scandir() over the given names.

    Each call should get a fresh one (use as a side_effect) because the underlying
    iterator is exhausted after a single pass.
    """
    entries = []
    for n in names:
        entry = MagicMock()
        entry.name = n
        entry.is_file.return_value = is_file
        entries.append(entry)
    cm = MagicMock()
    cm.__enter__.return_value = iter(entries)
    cm.__exit__.return_value = False
    return cm


def test_timed_func(capsys):
    """Basic test to ensure the timed captures func and timing info"""
    # GIVEN
    args = ("args",)
    kwargs = {"key": "word"}

    @timed_func
    def quick_func(*args, **kwargs) -> bool:
        print("I'm a quick func")
        return True

    # WHEN
    result = quick_func(*args, **kwargs)
    output = capsys.readouterr()

    # THEN
    # ensure the inner func ran properly
    assert result is True
    assert "I'm a quick func" in output.out

    # ensure we have the decorator info
    expected_re = (
        rf"func: quick_func with args: {re.escape(str(args))}, "
        rf"kwargs: {re.escape(str(kwargs))}, "
        r"took \d.\d{3} seconds"
    )
    match = re.search(expected_re, output.out)
    assert match is not None


@pytest.mark.parametrize(
    ("first_path, second_path, expected_output"),
    [
        (
            "test\\path",
            "path\\",
            "test/path/path/",
        ),
        (
            "test",
            "path",
            "test/path",
        ),
        (
            "test/path",
            "path",
            "test/path/path",
        ),
    ],
)
def test_join_paths(first_path: str, second_path: str, expected_output: str):
    """Basic test to ensure backslash paths are replaced"""
    assert join_paths(first_path, second_path) == expected_output


@pytest.mark.parametrize("frame_symbol", ["<f>", "<frame>"])
@patch.object(utils_module.os, "scandir")
@patch.object(utils_module.os.path, "isdir", return_value=True)
@patch.object(utils_module, "_patternToRegex")
def test_findAllFilesForPattern(
    mock_patternToRegex: MagicMock,
    mock_isdir: MagicMock,
    mock_scandir: MagicMock,
    frame_symbol: str,
) -> None:
    # GIVEN
    pattern = f"/test/file/path.{frame_symbol}.png"
    frame_number = 2
    names = [
        "/test/file/path.0001.png",
        "/test/file/path.0002.png",
        "/test/file/path.0003.png",
        "/test/file/path.000002.png",
        "/test/file/path.2.png",
    ]
    mock_scandir.side_effect = lambda _dirname: _scandir_cm(names)
    # TODO: Figure out what patternToRegex actually does
    mock_patternToRegex.return_value = pattern.replace("<f>", f"0*{frame_number}").replace(
        "<frame>", f"0*{frame_number}"
    )

    # WHEN
    result = utils_module.findAllFilesForPattern(pattern, frame_number)

    # THEN
    assert result == [
        "/test/file/path.0002.png",
        "/test/file/path.000002.png",
        "/test/file/path.2.png",
    ]


class TestFindAllFilesForPatternRegexErrors:
    """Tests for findAllFilesForPattern regex error handling"""

    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_handles_regex_error_with_brackets_file_exists(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
    ) -> None:
        """Test that regex errors with special characters fall back to escaped regex matching"""
        # GIVEN
        pattern = "/path/to/cache[1].bif"
        mock_patternToRegex.side_effect = re.error("bad character range")
        mock_scandir.side_effect = lambda _dirname: _scandir_cm(["cache[1].bif", "cache[2].bif"])

        # WHEN
        result = utils_module.findAllFilesForPattern(pattern, 0)

        # THEN
        assert result == [pattern]

    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_handles_regex_error_with_brackets_file_not_exists(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
    ) -> None:
        """Test that regex errors return empty list when no matching files exist"""
        # GIVEN
        pattern = "/path/to/cache[1].bif"
        mock_patternToRegex.side_effect = re.error("bad character range")
        mock_scandir.side_effect = lambda _dirname: _scandir_cm(["cache[2].bif", "cache[3].bif"])

        # WHEN
        result = utils_module.findAllFilesForPattern(pattern, 0)

        # THEN
        assert result == []

    @pytest.mark.parametrize(
        "pattern",
        [
            "/path/to/file[1].abc",
            "/path/to/file(1).abc",
            "/path/to/file{1}.abc",
            "/path/to/file[1-2].abc",
        ],
    )
    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_handles_various_special_characters(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
        pattern: str,
    ) -> None:
        """Test that various regex special characters are handled correctly"""
        # GIVEN
        dirname, basename = pattern.rsplit("/", 1)
        mock_patternToRegex.side_effect = re.error("bad character range")
        mock_scandir.side_effect = lambda _dirname: _scandir_cm([basename])

        # WHEN
        result = utils_module.findAllFilesForPattern(pattern, 0)

        # THEN
        assert result == [pattern]


class TestFindAllFilesForPatternTimeout:
    """Tests for the directory-scan timeout guard in findAllFilesForPattern."""

    @patch.dict("os.environ", {"DEADLINE_MAYA_DIR_TIMEOUT": "0.2"})
    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex", return_value=".*")
    def test_timeout_skips_directory_and_returns_empty(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
        capsys,
    ) -> None:
        """A directory scan that blocks past the timeout is skipped, returning []."""

        # GIVEN a scandir that blocks far longer than the configured timeout
        def _slow_scandir(_dirname: str):
            time.sleep(5)
            return _scandir_cm(["should-never-be-reached.png"])

        mock_scandir.side_effect = _slow_scandir

        # WHEN
        result = utils_module.findAllFilesForPattern("/mnt/dead-share/tex.png", 0)

        # THEN — detection continues with an empty result and a clear warning
        assert result == []
        captured = capsys.readouterr()
        assert "Timed out" in captured.out
        assert "/mnt/dead-share" in captured.out

    def test_run_with_timeout_returns_value(self) -> None:
        # GIVEN/WHEN
        result = utils_module._run_with_timeout(lambda x: x * 2, 5, 21)
        # THEN
        assert result == 42

    def test_run_with_timeout_reraises_underlying_error(self) -> None:
        # GIVEN a function that raises
        def _boom() -> None:
            raise ValueError("kaboom")

        # WHEN / THEN — the original exception is propagated, not swallowed
        with pytest.raises(ValueError, match="kaboom"):
            utils_module._run_with_timeout(_boom, 5)

    def test_run_with_timeout_raises_timeout_error(self) -> None:
        # GIVEN a function that blocks longer than the timeout
        with pytest.raises(TimeoutError):
            utils_module._run_with_timeout(lambda: time.sleep(5), 0.1)


class TestGetDirTimeout:
    """Tests for _get_dir_timeout env-var parsing."""

    @patch.dict("os.environ", {}, clear=True)
    def test_defaults_to_ten_seconds(self) -> None:
        assert utils_module._get_dir_timeout() == 10.0

    @patch.dict("os.environ", {"DEADLINE_MAYA_DIR_TIMEOUT": "3"})
    def test_reads_env_override(self) -> None:
        assert utils_module._get_dir_timeout() == 3.0

    @patch.dict("os.environ", {"DEADLINE_MAYA_DIR_TIMEOUT": "not-a-number"})
    def test_falls_back_on_invalid_value(self) -> None:
        assert utils_module._get_dir_timeout() == 10.0


class TestDirectoryListingCache:
    """Tests for the per-scan directory listing cache in findAllFilesForPattern."""

    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_same_directory_listed_once_across_references(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
    ) -> None:
        """Multiple references into one directory should enumerate it only once."""
        # GIVEN three different files in the same directory
        mock_scandir.side_effect = lambda _dirname: _scandir_cm(["a.png", "b.png", "c.png"])
        mock_patternToRegex.side_effect = lambda b: re.escape(b)

        # WHEN we resolve three separate patterns in that directory
        utils_module.findAllFilesForPattern("/tex/a.png", 0)
        utils_module.findAllFilesForPattern("/tex/b.png", 0)
        utils_module.findAllFilesForPattern("/tex/c.png", 0)

        # THEN os.scandir ran exactly once for the shared directory
        mock_scandir.assert_called_once_with("/tex")

    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_same_directory_listed_once_across_frames(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
    ) -> None:
        """Resolving many animation frames of one pattern lists the dir only once."""
        # GIVEN
        mock_scandir.side_effect = lambda _dirname: _scandir_cm(
            ["seq.0001.exr", "seq.0002.exr", "seq.0003.exr"]
        )
        mock_patternToRegex.side_effect = lambda b: re.escape(b)

        # WHEN the same pattern is resolved for many frames (as _expand_path does)
        for frame in range(1, 50):
            utils_module.findAllFilesForPattern("/render/seq.<f>.exr", frame)

        # THEN the directory was enumerated exactly once
        mock_scandir.assert_called_once_with("/render")

    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_cache_clear_forces_relist(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
    ) -> None:
        """clear_directory_cache() makes the next scan re-enumerate the directory."""
        # GIVEN
        mock_scandir.side_effect = lambda _dirname: _scandir_cm(["a.png"])
        mock_patternToRegex.side_effect = lambda b: re.escape(b)

        # WHEN
        utils_module.findAllFilesForPattern("/tex/a.png", 0)
        utils_module.clear_directory_cache()
        utils_module.findAllFilesForPattern("/tex/a.png", 0)

        # THEN the directory is listed again after the cache is cleared
        assert mock_scandir.call_count == 2

    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_cached_result_still_matches_per_pattern(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
    ) -> None:
        """A shared cached listing still resolves to the correct files per pattern."""
        # GIVEN
        mock_scandir.side_effect = lambda _dirname: _scandir_cm(["a.png", "b.png", "c.png"])
        mock_patternToRegex.side_effect = lambda b: re.escape(b)

        # WHEN / THEN — each pattern resolves only its own file from the shared listing
        assert utils_module.findAllFilesForPattern("/tex/a.png", 0) == ["/tex/a.png"]
        assert utils_module.findAllFilesForPattern("/tex/b.png", 0) == ["/tex/b.png"]
        assert utils_module.findAllFilesForPattern("/tex/missing.png", 0) == []

    @patch.object(utils_module.os, "scandir")
    @patch.object(utils_module.os.path, "isdir", return_value=True)
    @patch.object(utils_module, "_patternToRegex")
    def test_directories_excluded_from_results(
        self,
        mock_patternToRegex: MagicMock,
        mock_isdir: MagicMock,
        mock_scandir: MagicMock,
    ) -> None:
        """Entries that are directories (is_file() False) are not returned."""
        # GIVEN a directory containing a file and a sub-directory that both match
        file_entry = MagicMock()
        file_entry.name = "tex.png"
        file_entry.is_file.return_value = True
        dir_entry = MagicMock()
        dir_entry.name = "tex.png.d"
        dir_entry.is_file.return_value = False

        cm = MagicMock()
        cm.__enter__.return_value = iter([file_entry, dir_entry])
        cm.__exit__.return_value = False
        mock_scandir.side_effect = lambda _dirname: cm
        mock_patternToRegex.side_effect = lambda b: ".*"  # match everything

        # WHEN
        result = utils_module.findAllFilesForPattern("/tex/tex.png", 0)

        # THEN only the regular file is returned
        assert result == ["/tex/tex.png"]

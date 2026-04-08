# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
from __future__ import annotations

import os
import re
import time
from functools import lru_cache
from pathlib import Path
from typing import Any, Generator, Iterable

from .file_path_editor import FilePathEditor
from .scene import Animation, RendererNames, Scene
from .utils import findAllFilesForPattern

import maya.cmds

_FRAME_RE = re.compile("#+")

# Regex to extract string values from .vrscene plugin parameters.
# Matches: parameter_name="value"; (the value part, which is a file path)
# The vrscene format uses paramName="stringValue"; for string parameters.
_VRSCENE_STRING_PARAM_RE = re.compile(r'=\s*"([^"]+)"\s*;')

# Known vrscene plugin types and their attributes that contain file paths.
# This covers the most common cases: textures, geometry proxies, IES lights, etc.
_VRSCENE_FILE_PLUGINS: dict[str, set[str]] = {
    "BitmapBuffer": {"file"},
    "GeomMeshFile": {"file"},
    "LightIES": {"ies_file"},
    "TexPtex": {"ptex_file"},
    "VRayScene": {"filepath"},
    "PhxShaderCache": {"cache_path"},
}


class AssetIntrospector:
    def parse_scene_assets(self, progress_callback=None) -> set[Path]:
        """
        Searches the scene for assets, and filters out assets that are not needed for Rendering.

        Args:
            progress_callback: Optional callback function that takes a string argument for progress updates

        Returns:
            set[Path]: A set containing filepaths of assets needed for Rendering
        """
        # clear filesystem cache from last run
        self._expand_path.cache_clear()
        # Grab tx files (if we need to)
        assets: set[Path] = set()

        # Grab any yeti files
        if progress_callback:
            progress_callback("Searching for Yeti cache files...")
        assets.update(self._get_yeti_files(progress_callback))

        if Scene.renderer() == RendererNames.arnold.value:
            if progress_callback:
                progress_callback("Searching for Arnold texture files...")
            assets.update(self._get_tx_files(progress_callback))
        elif Scene.renderer() == RendererNames.renderman.value:
            if progress_callback:
                progress_callback("Searching for Renderman texture files...")
            assets.update(self._get_tex_files(progress_callback))

        if Scene.renderer() == RendererNames.vray.value:
            if progress_callback:
                progress_callback("Searching for VRayScene linked files...")
            assets.update(self._get_vrscene_linked_files(progress_callback))

        file_refs = list(FilePathEditor.fileRefs())
        total_refs = len(file_refs)
        print(f"Processing {total_refs} file references")
        if progress_callback:
            progress_callback(f"Processing {total_refs} file references...")

        for i, ref in enumerate(file_refs):
            normalized_path = os.path.normpath(ref.path)
            # Files without tokens may already have been checked, if so, skip
            if normalized_path in assets:
                continue
            # Files with tokens may have already been checked when grabbing arnold's tx files.
            # Since the expand path returns a generator, it'll actually skip rechecking
            # these files since it returns the original generator which is exhausted.
            for path in self._expand_path(normalized_path):
                assets.add(path)
            # Only refresh UI every 100 elements to improve performance
            if i % 100 == 0:
                print(f"Processed {i+1}/{total_refs} file references at {time.time()}")
                if progress_callback:
                    progress_callback(f"Processed {i+1}/{total_refs} file references...")

        # Iterate through every node and list all attributes that are filenames
        # Then replace all tokens and check if it's a real path
        # If it's not already in assets, add it to assets
        for path in self._get_node_attr_paths(expand_tokens=True):
            normalized_path = os.path.normpath(path)
            frame_re_matches = _FRAME_RE.findall(normalized_path)
            if frame_re_matches or "<f>" in normalized_path or "<frame>" in normalized_path:
                for expanded_path in self._expand_path(normalized_path):
                    assets.add(expanded_path)
            else:
                assets.add(Path(normalized_path))

        assets.add(Path(Scene.name()))

        if progress_callback:
            progress_callback(f"Found {len(assets)} assets in total")

        return assets

    def _get_yeti_files(self, progress_callback=None) -> set[Path]:
        """
        If Yeti plugin nodes are in the scene, searches for fur cache files

        Args:
            progress_callback: Optional callback function for progress updates

        Returns:
            set[Path]: A set of yeti files
        """
        yeti_files: set[Path] = set()
        cache_files = Scene.yeti_cache_files()
        total_files = len(cache_files)

        if total_files > 0:
            print(f"Processing {total_files} Yeti cache files")
            if progress_callback:
                progress_callback(f"Processing {total_files} Yeti cache files...")

        for i, cache_path in enumerate(cache_files):
            for expanded_path in self._expand_path(cache_path):
                yeti_files.add(expanded_path)

            # For every 100 yeti files, update progress
            if i % 100 == 0 and i > 0:
                print(f"Processed {i}/{total_files} Yeti cache files at {time.time()}")
                if progress_callback:
                    progress_callback(f"Processed {i}/{total_files} Yeti cache files...")

        if total_files > 0:
            print(f"Completed processing all {total_files} Yeti cache files at {time.time()}")
            if progress_callback:
                progress_callback(f"Completed processing all {total_files} Yeti cache files")

        return yeti_files

    def _get_tex_files(self, progress_callback=None) -> set[Path]:
        """
        Searches for Renderman .tex files

        Args:
            progress_callback: Optional callback function for progress updates

        Returns:
            set[Path]: A set of tex files associated to scene textures
        """

        from maya.cmds import filePathEditor  # type: ignore
        from rfm2.txmanager_maya import get_texture_by_path  # type: ignore

        # We query Maya's file path editor for all referenced external files
        # And then query RenderMan's Tx Manager to get the name for the .tex files
        # (needed because the filename can include color space information)
        filename_tex_set: set[Path] = set()
        directories = filePathEditor(listDirectories="", query=True)

        total_files = 0
        processed = 0

        # First count total files for better progress reporting
        for directory in directories:
            files = filePathEditor(listFiles=directory, withAttribute=True, query=True)
            total_files += len(files) // 2  # files come in pairs (filename, attribute)

        print(f"Processing {total_files} Renderman texture files")
        if progress_callback:
            progress_callback(f"Processing {total_files} Renderman texture files...")

        for directory in directories:
            files = filePathEditor(listFiles=directory, withAttribute=True, query=True)
            for i, (filename, attribute) in enumerate(zip(files[0::2], files[1::2])):
                full_path = os.path.join(directory, filename)
                # Expand tags if any are present
                for expanded_path in self._expand_path(full_path):
                    # get_texture_by_path expects an attribute, not a node
                    if "." in attribute:
                        # add the original texture
                        filename_tex_set.add(expanded_path)
                        try:
                            # Returns a key error if the resource is not in tx manager
                            filename_tex = get_texture_by_path(str(expanded_path), attribute)
                            filename_tex_set.add(Path(filename_tex))
                        except KeyError:
                            pass

                processed += 1
                # For every 100 texture files, update progress
                if processed % 100 == 0:
                    print(
                        f"Processed {processed}/{total_files} Renderman texture files at {time.time()}"
                    )
                    if progress_callback:
                        progress_callback(
                            f"Processed {processed}/{total_files} Renderman texture files..."
                        )

        # Final count
        if total_files > 0:
            print(
                f"Completed processing all {total_files} Renderman texture files at {time.time()}"
            )
            if progress_callback:
                progress_callback(f"Completed processing all {total_files} Renderman texture files")

        return filename_tex_set

    def _get_tx_files(self, progress_callback=None) -> set[Path]:
        """
        Searches for both source and tx files for Arnold

        Args:
            progress_callback: Optional callback function for progress updates

        Returns:
            set[Path]: A set of original asset paths and their associated tx files.
        """

        arnold_textures_files: set[Path] = set()
        if not Scene.autotx() and not Scene.use_existing_tiled_textures():
            return arnold_textures_files

        texture_files = list(self._get_arnold_texture_files())
        total_textures = len(texture_files)
        print(f"Processing {total_textures} Arnold texture files")
        if progress_callback:
            progress_callback(f"Processing {total_textures} Arnold texture files...")

        for i, img_path in enumerate(texture_files):
            for expanded_path in self._expand_path(img_path):
                arnold_textures_files.add(expanded_path)
                # expanded files are guaranteed to exist, but we haven't checked the associated .tx file yet
                if os.path.isfile(expanded_path.with_suffix(".tx")):
                    arnold_textures_files.add(expanded_path.with_suffix(".tx"))

            # For every 100 texture files, update progress
            if i % 100 == 0 and i > 0:
                print(f"Processed {i}/{total_textures} Arnold texture files at {time.time()}")
                if progress_callback:
                    progress_callback(f"Processed {i}/{total_textures} Arnold texture files...")

        # Final count
        if total_textures > 0:
            print(
                f"Completed processing all {total_textures} Arnold texture files at {time.time()}"
            )
            if progress_callback:
                progress_callback(f"Completed processing all {total_textures} Arnold texture files")

        return arnold_textures_files

    def _get_vrscene_linked_files(self, progress_callback=None) -> set[Path]:
        """
        Finds all VRayScene nodes in the Maya scene, reads their referenced .vrscene files,
        and parses those files to discover linked assets (textures, Alembic, VrayMesh, VDB, etc.).

        Args:
            progress_callback: Optional callback function for progress updates

        Returns:
            set[Path]: A set of file paths referenced inside .vrscene files
        """
        vrscene_assets: set[Path] = set()

        # Find all VRayScene nodes in the Maya scene
        vrscene_nodes = maya.cmds.ls(type="VRayScene") or []
        if not vrscene_nodes:
            return vrscene_assets

        total_nodes = len(vrscene_nodes)
        print(f"Processing {total_nodes} VRayScene node(s) for linked files")
        if progress_callback:
            progress_callback(f"Processing {total_nodes} VRayScene node(s) for linked files...")

        for i, node in enumerate(vrscene_nodes):
            # The .vrscene file path is stored in the "FilePath" attribute
            if not maya.cmds.attributeQuery("FilePath", node=node, exists=True):
                continue

            vrscene_path = maya.cmds.getAttr(f"{node}.FilePath")
            if not vrscene_path or not isinstance(vrscene_path, str):
                continue

            vrscene_path = vrscene_path.strip()
            if not vrscene_path:
                continue

            # Resolve relative paths against the Maya project
            if not os.path.isabs(vrscene_path):
                vrscene_path = os.path.join(Scene.project_path(), vrscene_path)

            vrscene_path = os.path.normpath(vrscene_path)

            if not os.path.isfile(vrscene_path):
                print(f"Warning: VRayScene file not found: {vrscene_path}")
                continue

            print(f"Parsing VRayScene file: {vrscene_path}")
            if progress_callback:
                progress_callback(f"Parsing VRayScene file ({i+1}/{total_nodes}): {vrscene_path}")

            linked_files = self._parse_vrscene_file(vrscene_path)
            vrscene_assets.update(linked_files)

        if vrscene_assets:
            print(f"Found {len(vrscene_assets)} linked file(s) in VRayScene files")
            if progress_callback:
                progress_callback(f"Found {len(vrscene_assets)} linked file(s) in VRayScene files")

        return vrscene_assets

    def _parse_vrscene_file(self, vrscene_path: str) -> set[Path]:
        """
        Parses a .vrscene file and extracts all referenced file paths.

        The .vrscene format is text-based with plugin blocks like:
            BitmapBuffer bitmapBuffer1 {
                file="path/to/texture.exr";
            }

        We use two strategies:
        1. Targeted: Track known plugin types and their file attributes
        2. Broad: For any string parameter value that looks like a file path with a
           recognized extension, include it as well (catches custom/unknown plugins)

        Args:
            vrscene_path: Absolute path to the .vrscene file

        Returns:
            set[Path]: A set of resolved file paths found in the .vrscene file
        """
        linked_files: set[Path] = set()
        vrscene_dir = os.path.dirname(vrscene_path)

        # File extensions commonly referenced in vrscene files
        _asset_extensions = {
            ".exr",
            ".hdr",
            ".hdri",
            ".tif",
            ".tiff",
            ".png",
            ".jpg",
            ".jpeg",
            ".bmp",
            ".tga",
            ".tx",
            ".tex",
            ".rat",
            ".abc",
            ".vrmesh",
            ".vdb",
            ".obj",
            ".ies",
            ".vrscene",
            ".osl",
            ".oso",
            ".vrmat",
            ".vismat",
        }

        current_plugin_type: str | None = None
        in_plugin_block = False

        try:
            with open(vrscene_path, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    stripped = line.strip()

                    # Skip comments and empty lines
                    if not stripped or stripped.startswith("//"):
                        continue

                    # Handle #include directives (vrscene supports C-style includes)
                    if stripped.startswith("#include"):
                        match = re.match(r'#include\s+"([^"]+)"', stripped)
                        if match:
                            include_path = match.group(1)
                            if not os.path.isabs(include_path):
                                include_path = os.path.join(vrscene_dir, include_path)
                            include_path = os.path.normpath(include_path)
                            if os.path.isfile(include_path):
                                linked_files.add(Path(include_path))
                                # Recursively parse included vrscene files
                                linked_files.update(self._parse_vrscene_file(include_path))
                        continue

                    # Detect plugin block start: "PluginType instanceName {"
                    if not in_plugin_block and "{" in stripped:
                        parts = stripped.split()
                        if len(parts) >= 2:
                            current_plugin_type = parts[0]
                            in_plugin_block = True
                        continue

                    # Detect plugin block end
                    if stripped == "}":
                        current_plugin_type = None
                        in_plugin_block = False
                        continue

                    if not in_plugin_block:
                        continue

                    # Extract string parameter values
                    matches = _VRSCENE_STRING_PARAM_RE.findall(line)
                    for value in matches:
                        is_known_file_attr = False

                        # Strategy 1: Check if this is a known file attribute
                        if current_plugin_type in _VRSCENE_FILE_PLUGINS:
                            param_name = line.split("=")[0].strip() if "=" in line else ""
                            if param_name in _VRSCENE_FILE_PLUGINS[current_plugin_type]:
                                is_known_file_attr = True

                        # Strategy 2: Check if the value looks like a file path
                        _, ext = os.path.splitext(value.lower())
                        has_asset_extension = ext in _asset_extensions

                        if is_known_file_attr or has_asset_extension:
                            file_path = value
                            # Resolve relative paths against the vrscene file's directory
                            if not os.path.isabs(file_path):
                                file_path = os.path.join(vrscene_dir, file_path)
                            file_path = os.path.normpath(file_path)

                            # Handle <UDIM> tokens by finding all matching tile files
                            if "<UDIM>" in file_path:
                                file_dir = os.path.dirname(file_path)
                                file_base = os.path.basename(file_path).split("<UDIM>")[0]
                                try:
                                    for f in os.listdir(file_dir):
                                        if f.startswith(file_base) and os.path.isfile(
                                            os.path.join(file_dir, f)
                                        ):
                                            linked_files.add(
                                                Path(os.path.normpath(os.path.join(file_dir, f)))
                                            )
                                except (OSError, FileNotFoundError):
                                    # Directory may not exist locally; add the pattern path
                                    # so it can be resolved on the farm
                                    linked_files.add(Path(file_path))
                            elif os.path.exists(file_path):
                                linked_files.add(Path(file_path))
                            else:
                                # Still add it - the path might be valid on the render farm
                                # after path mapping
                                linked_files.add(Path(file_path))

        except (OSError, IOError) as e:
            print(f"Warning: Could not read VRayScene file {vrscene_path}: {e}")

        return linked_files

    def _get_arnold_texture_files(self) -> dict[str, Any]:
        """
        Imports inner Arnold functions to get list of textures.

        Returns:
            dict[str, texture_info]: A mapping of original absolute texture paths to their properties.
        """
        import mtoa.txManager.lib as mtoa  # type: ignore

        try:
            return mtoa.get_scanned_files(mtoa.scene_default_texture_scan)
        except re.error as e:
            if "bad escape" in str(e):
                try:
                    import maya.cmds

                    mtoa_version = maya.cmds.pluginInfo("mtoa", query=True, version=True)
                except Exception:
                    mtoa_version = "UNKNOWN - Please check 'Arnold' > 'About'"

                # Handle a known MTOA bug where this function is broken when using UDIM textures (logged as MTOA-1424 at Autodesk)
                # This is fixed in MTOA 5.3.5: https://help.autodesk.com/view/ARNOL/ENU/?guid=arnold_for_maya_535_html
                raise Exception(
                    "This error may be caused by a known bug in Arnold for Maya (MtoA) versions less than 5.3.5 (MTOA-1424). "
                    "If the installed version of Arnold for Maya is less than 5.3.5, please upgrade to MtoA 5.3.5 or newer and try again. "
                    f"Detected MtoA Version: {mtoa_version}"
                ) from e
            raise

    def _flatten_and_validate_paths(self, raw_paths: list[Any]) -> list[str]:
        """
        Flattens and validates attribute values from Maya's getAttr command.

        Maya's getAttr returns different types depending on the attribute:
        - str: Single file path (regular attributes)
        - list[str]: Multiple file paths (multi-attributes like Bifrost caches)
        - None: Empty/unset attributes

        This method converts all values to a flat list of strings.

        Args:
            raw_paths: Raw output from maya.cmds.getAttr calls

        Returns:
            list[str]: Flattened list of valid string paths
        """
        flattened_paths: list[str] = []

        for raw_path in raw_paths:
            if raw_path is None:
                # Skip None/empty values
                continue
            elif isinstance(raw_path, str):
                # Single string path - add if not empty
                if raw_path.strip():
                    flattened_paths.append(raw_path)
            elif isinstance(raw_path, list):
                # List of paths - recursively flatten (handles nested lists if they occur)
                nested_paths = self._flatten_and_validate_paths(raw_path)
                flattened_paths.extend(nested_paths)
            else:
                # Handle unexpected data types for better debugging
                print(
                    f"Warning: Unexpected data type in asset paths: {type(raw_path)} = {raw_path}"
                )

        return flattened_paths

    def _get_node_attr_paths(self, expand_tokens=False):
        """
        FilePathEditor by default leaves out many file types like caches.
        This function iterates through nodes in the scene and filters for filepath attributes

        Returns:
            [str]: A list of all the paths found
        """
        paths: list[str] = []
        # Excluding vraySettings attributes because the referenced file paths cause issues
        # when auto-populating the attachment input directories
        excluded_attrs_by_node: dict[str, set[str]] = {
            "vraySettings": {
                "vraySettings.sys_memory_tracking_output_path",
                "vraySettings.sys_time_tracking_output_dir",
            }
        }
        for node in maya.cmds.ls():
            attrs: list[str] = maya.cmds.listAttr(
                node, usedAsFilename=True, fullNodeName=True, multi=True
            )
            # We need to make sure we're including Bifrost caches, but listAttr won't find them
            # Bifrost simulation caches use the "absoluteCacheName" attribute, which is not marked with "usedAsFilename"
            if maya.cmds.attributeQuery("absoluteCacheName", node=node, exists=True):
                if attrs is None:
                    attrs = []
                attrs.append("%s.absoluteCacheName" % node)
            if attrs is not None:
                if excluded_attrs := excluded_attrs_by_node.get(str(node), set()):
                    attrs = [attr for attr in attrs if attr not in excluded_attrs]
                # Get raw attribute values (can be strings, lists, or None)
                raw_attr_values = [maya.cmds.getAttr(attr) for attr in attrs]
                # Flatten and validate paths to ensure all are strings
                new_paths: list[str] = self._flatten_and_validate_paths(raw_attr_values)
                if expand_tokens:
                    new_paths = [self._expand_tokens(path, object_name=node) for path in new_paths]
                paths.extend(new_paths)

        return paths

    @lru_cache(maxsize=None)
    def _expand_path(self, path: str) -> Generator[Path, None, None]:
        """
        Some animated textures are padded with multiple '#' characters to indicate the current frame
        number, while others such as animated multi-tiled UV textures will have tokens such as <f>,
        or <UDIM> which are replaced at render time.

        This function expands these tokens and characters to find all the assets which will be
        required at render time.

        This function gets called for a variety of file groupings (ie. Arnold's txmanager, Maya's FilePathEditor)
        Since this func has an lru cache and returns a generator, it'll actually skip rechecking these files since
        it returns the original generator which is exhausted. You can, however, force it to recheck
        these files by performing asset_introspector._expand_path.cache_clear() call.

        Args:
            path (str): A path with tokens to replace

        Yields:
            Generator[str, None, None]: A series of paths that match the pattern provided.
        """
        frame_re_matches = _FRAME_RE.findall(path)

        frame_list: Iterable[int] = [0]
        if frame_re_matches or "<f>" in path or "<frame>" in path:
            frame_list = Animation.frame_list()

        for frame in frame_list:
            working_path = path
            for group in frame_re_matches:
                working_path = working_path.replace(group, str(frame).zfill(len(group)))
            paths = findAllFilesForPattern(working_path, frame)
            for p in paths:
                if not p.endswith(":Zone.Identifier"):  # Metadata files that erroneously match
                    yield Path(p)

    def _expand_tokens(self, path: str, object_name="<object>") -> str:
        path = path.replace("<project>", os.path.normpath(maya.cmds.internalVar(uwd=True)))
        path = path.replace("<object>", object_name)

        filepath = maya.cmds.file(q=True, sn=True)
        filename = os.path.basename(filepath)
        scene_name, _ = os.path.splitext(filename)
        path = path.replace("<scene>", scene_name)

        return path

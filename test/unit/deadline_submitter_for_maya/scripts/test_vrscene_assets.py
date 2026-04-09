# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, call, patch
from typing import Generator

import pytest

import deadline.maya_submitter.assets as assets_module
from deadline.maya_submitter.scene import RendererNames


class TestParseSceneAssetsVRayScene:
    """Tests that parse_scene_assets calls _get_vrscene_linked_files when renderer is VRay"""

    @pytest.fixture(autouse=True)
    def mock_expand_path(self) -> Generator[MagicMock, None, None]:
        with patch.object(assets_module.AssetIntrospector, "_expand_path") as m:
            m.side_effect = lambda p: [Path(p)]
            yield m

    @pytest.fixture(autouse=True)
    def mock_get_yeti_files(self) -> Generator[MagicMock, None, None]:
        with patch.object(assets_module.AssetIntrospector, "_get_yeti_files") as m:
            m.return_value = set()
            yield m

    @pytest.fixture(autouse=True)
    def mock_renderer(self) -> Generator[MagicMock, None, None]:
        with patch.object(assets_module.Scene, "renderer") as m:
            yield m

    @pytest.fixture(autouse=True)
    def mock_fileRefs(self) -> Generator[MagicMock, None, None]:
        with patch.object(assets_module.FilePathEditor, "fileRefs") as m:
            m.return_value = []
            yield m

    @pytest.fixture(autouse=True)
    def mock_scene_name(self) -> Generator[MagicMock, None, None]:
        with patch.object(assets_module.Scene, "name") as m:
            m.return_value = "/path/to/scene.ma"
            yield m

    @patch.object(assets_module.AssetIntrospector, "_get_vrscene_linked_files")
    def test_gets_vrscene_linked_files_when_vray(
        self,
        mock_get_vrscene_linked_files: MagicMock,
        mock_renderer: MagicMock,
    ) -> None:
        # GIVEN
        vrscene_files = {
            Path("/path/to/texture1.exr"),
            Path("/path/to/proxy.vrmesh"),
            Path("/path/to/volume.vdb"),
        }
        mock_get_vrscene_linked_files.return_value = vrscene_files
        mock_renderer.return_value = RendererNames.vray.value
        progress_callback = MagicMock()

        # WHEN
        results = assets_module.AssetIntrospector().parse_scene_assets(progress_callback)

        # THEN
        for f in vrscene_files:
            assert f in results
        mock_get_vrscene_linked_files.assert_called_once_with(progress_callback)
        progress_callback.assert_has_calls(
            [call("Searching for VRayScene linked files...")],
            any_order=True,
        )

    @patch.object(assets_module.AssetIntrospector, "_get_tx_files")
    @patch.object(assets_module.AssetIntrospector, "_get_vrscene_linked_files")
    def test_does_not_get_vrscene_files_when_not_vray(
        self,
        mock_get_vrscene_linked_files: MagicMock,
        mock_get_tx_files: MagicMock,
        mock_renderer: MagicMock,
    ) -> None:
        # GIVEN
        mock_renderer.return_value = RendererNames.arnold.value
        mock_get_tx_files.return_value = set()

        # WHEN
        assets_module.AssetIntrospector().parse_scene_assets()

        # THEN
        mock_get_vrscene_linked_files.assert_not_called()


class TestGetVrsceneLinkedFiles:
    """Tests for AssetIntrospector._get_vrscene_linked_files"""

    @pytest.fixture(autouse=True)
    def mock_maya(self) -> Generator[MagicMock, None, None]:
        with patch.object(assets_module, "maya") as m:
            yield m

    @pytest.fixture(autouse=True)
    def mock_scene_project_path(self) -> Generator[MagicMock, None, None]:
        with patch.object(assets_module.Scene, "project_path") as m:
            m.return_value = "/project"
            yield m

    def test_returns_empty_when_no_vrscene_nodes(self, mock_maya: MagicMock) -> None:
        # GIVEN
        mock_maya.cmds.ls.return_value = []

        # WHEN
        result = assets_module.AssetIntrospector()._get_vrscene_linked_files()

        # THEN
        assert result == set()

    def test_returns_empty_when_ls_returns_none(self, mock_maya: MagicMock) -> None:
        # GIVEN
        mock_maya.cmds.ls.return_value = None

        # WHEN
        result = assets_module.AssetIntrospector()._get_vrscene_linked_files()

        # THEN
        assert result == set()

    @patch.object(assets_module.AssetIntrospector, "_parse_vrscene_file")
    @patch("os.path.isfile")
    def test_parses_vrscene_files_from_nodes(
        self,
        mock_isfile: MagicMock,
        mock_parse_vrscene: MagicMock,
        mock_maya: MagicMock,
    ) -> None:
        # GIVEN
        mock_maya.cmds.ls.return_value = ["vrsceneNode1", "vrsceneNode2"]
        mock_maya.cmds.attributeQuery.return_value = True
        mock_maya.cmds.getAttr.side_effect = [
            "/path/to/scene1.vrscene",
            "/path/to/scene2.vrscene",
        ]
        mock_isfile.return_value = True
        mock_parse_vrscene.side_effect = [
            {Path("/path/to/texture1.exr")},
            {Path("/path/to/proxy.vrmesh"), Path("/path/to/volume.vdb")},
        ]
        progress_callback = MagicMock()

        # WHEN
        result = assets_module.AssetIntrospector()._get_vrscene_linked_files(progress_callback)

        # THEN
        assert result == {
            Path("/path/to/texture1.exr"),
            Path("/path/to/proxy.vrmesh"),
            Path("/path/to/volume.vdb"),
        }
        assert mock_parse_vrscene.call_count == 2
        progress_callback.assert_has_calls(
            [call("Processing 2 VRayScene node(s) for linked files...")],
            any_order=True,
        )

    @patch.object(assets_module.AssetIntrospector, "_parse_vrscene_file")
    @patch("os.path.isfile")
    def test_resolves_relative_vrscene_paths(
        self,
        mock_isfile: MagicMock,
        mock_parse_vrscene: MagicMock,
        mock_maya: MagicMock,
        mock_scene_project_path: MagicMock,
    ) -> None:
        # GIVEN
        mock_maya.cmds.ls.return_value = ["vrsceneNode1"]
        mock_maya.cmds.attributeQuery.return_value = True
        mock_maya.cmds.getAttr.return_value = "scenes/my_scene.vrscene"  # relative path
        mock_isfile.return_value = True
        mock_parse_vrscene.return_value = set()

        # WHEN
        assets_module.AssetIntrospector()._get_vrscene_linked_files()

        # THEN
        expected_path = os.path.normpath("/project/scenes/my_scene.vrscene")
        mock_parse_vrscene.assert_called_once_with(expected_path)

    @patch.object(assets_module.AssetIntrospector, "_parse_vrscene_file")
    @patch("os.path.isfile")
    def test_skips_nodes_without_filepath_attr(
        self,
        mock_isfile: MagicMock,
        mock_parse_vrscene: MagicMock,
        mock_maya: MagicMock,
    ) -> None:
        # GIVEN
        mock_maya.cmds.ls.return_value = ["vrsceneNode1"]
        mock_maya.cmds.attributeQuery.return_value = False  # No FilePath attribute

        # WHEN
        result = assets_module.AssetIntrospector()._get_vrscene_linked_files()

        # THEN
        assert result == set()
        mock_parse_vrscene.assert_not_called()

    @patch.object(assets_module.AssetIntrospector, "_parse_vrscene_file")
    @patch("os.path.isfile")
    def test_skips_empty_filepath(
        self,
        mock_isfile: MagicMock,
        mock_parse_vrscene: MagicMock,
        mock_maya: MagicMock,
    ) -> None:
        # GIVEN
        mock_maya.cmds.ls.return_value = ["vrsceneNode1"]
        mock_maya.cmds.attributeQuery.return_value = True
        mock_maya.cmds.getAttr.return_value = ""

        # WHEN
        result = assets_module.AssetIntrospector()._get_vrscene_linked_files()

        # THEN
        assert result == set()
        mock_parse_vrscene.assert_not_called()

    @patch.object(assets_module.AssetIntrospector, "_parse_vrscene_file")
    @patch("os.path.isfile")
    def test_skips_nonexistent_vrscene_file(
        self,
        mock_isfile: MagicMock,
        mock_parse_vrscene: MagicMock,
        mock_maya: MagicMock,
    ) -> None:
        # GIVEN
        mock_maya.cmds.ls.return_value = ["vrsceneNode1"]
        mock_maya.cmds.attributeQuery.return_value = True
        mock_maya.cmds.getAttr.return_value = "/path/to/missing.vrscene"
        mock_isfile.return_value = False

        # WHEN
        result = assets_module.AssetIntrospector()._get_vrscene_linked_files()

        # THEN
        assert result == set()
        mock_parse_vrscene.assert_not_called()


class TestParseVrsceneFile:
    """Tests for AssetIntrospector._parse_vrscene_file"""

    def test_extracts_bitmap_buffer_file(self, tmp_path: Path) -> None:
        # GIVEN
        texture_file = tmp_path / "textures" / "wood.exr"
        texture_file.parent.mkdir(parents=True)
        texture_file.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f"BitmapBuffer bitmapBuffer1 {{\n" f'    file="{texture_file}";\n' f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(texture_file))) in result

    def test_extracts_geom_mesh_file(self, tmp_path: Path) -> None:
        # GIVEN
        mesh_file = tmp_path / "meshes" / "tree.vrmesh"
        mesh_file.parent.mkdir(parents=True)
        mesh_file.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f"GeomMeshFile geomMeshFile1 {{\n" f'    file="{mesh_file}";\n' f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(mesh_file))) in result

    def test_extracts_alembic_file(self, tmp_path: Path) -> None:
        # GIVEN
        abc_file = tmp_path / "caches" / "anim.abc"
        abc_file.parent.mkdir(parents=True)
        abc_file.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f"GeomMeshFile geomMeshFile1 {{\n" f'    file="{abc_file}";\n' f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(abc_file))) in result

    def test_extracts_vdb_file_by_extension(self, tmp_path: Path) -> None:
        # GIVEN
        vdb_file = tmp_path / "volumes" / "smoke.vdb"
        vdb_file.parent.mkdir(parents=True)
        vdb_file.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f"PhxShaderSim phxShaderSim1 {{\n" f'    cache_path="{vdb_file}";\n' f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(vdb_file))) in result

    def test_extracts_ies_light_file(self, tmp_path: Path) -> None:
        # GIVEN
        ies_file = tmp_path / "lights" / "spot.ies"
        ies_file.parent.mkdir(parents=True)
        ies_file.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(f"LightIES lightIES1 {{\n" f'    ies_file="{ies_file}";\n' f"}}\n")

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(ies_file))) in result

    def test_extracts_multiple_files_from_multiple_plugins(self, tmp_path: Path) -> None:
        # GIVEN
        texture1 = tmp_path / "tex1.exr"
        texture2 = tmp_path / "tex2.hdr"
        mesh = tmp_path / "proxy.vrmesh"
        for f in [texture1, texture2, mesh]:
            f.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f"BitmapBuffer bitmap1 {{\n"
            f'    file="{texture1}";\n'
            f"}}\n"
            f"BitmapBuffer bitmap2 {{\n"
            f'    file="{texture2}";\n'
            f"}}\n"
            f"GeomMeshFile meshFile1 {{\n"
            f'    file="{mesh}";\n'
            f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert len(result) == 3
        assert Path(os.path.normpath(str(texture1))) in result
        assert Path(os.path.normpath(str(texture2))) in result
        assert Path(os.path.normpath(str(mesh))) in result

    def test_detects_files_by_extension_in_unknown_plugins(self, tmp_path: Path) -> None:
        # GIVEN
        texture = tmp_path / "custom_texture.tiff"
        texture.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f"SomeCustomPlugin customPlugin1 {{\n" f'    custom_param="{texture}";\n' f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(texture))) in result

    def test_skips_comments(self, tmp_path: Path) -> None:
        # GIVEN
        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            "// BitmapBuffer commented_out {\n"
            '//     file="/path/to/should_not_appear.exr";\n'
            "// }\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert len(result) == 0

    def test_handles_include_directives(self, tmp_path: Path) -> None:
        # GIVEN
        texture = tmp_path / "tex.exr"
        texture.touch()

        included_file = tmp_path / "included.vrscene"
        included_file.write_text(f"BitmapBuffer bitmap1 {{\n" f'    file="{texture}";\n' f"}}\n")

        main_file = tmp_path / "main.vrscene"
        main_file.write_text(f'#include "{included_file}"\n')

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(main_file))

        # THEN
        # Should include both the included vrscene file itself and the texture inside it
        assert Path(os.path.normpath(str(included_file))) in result
        assert Path(os.path.normpath(str(texture))) in result

    def test_resolves_relative_paths_against_vrscene_dir(self, tmp_path: Path) -> None:
        # GIVEN
        textures_dir = tmp_path / "textures"
        textures_dir.mkdir()
        texture = textures_dir / "wood.exr"
        texture.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text("BitmapBuffer bitmap1 {\n" '    file="textures/wood.exr";\n' "}\n")

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(texture))) in result

    def test_includes_nonexistent_files(self, tmp_path: Path) -> None:
        """Non-existent files should still be included since they may exist on the render farm after path mapping"""
        # GIVEN
        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            "BitmapBuffer bitmap1 {\n" '    file="/farm/textures/wood.exr";\n' "}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath("/farm/textures/wood.exr")) in result

    def test_handles_empty_vrscene_file(self, tmp_path: Path) -> None:
        # GIVEN
        vrscene_file = tmp_path / "empty.vrscene"
        vrscene_file.write_text("")

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert result == set()

    def test_handles_unreadable_file(self, tmp_path: Path) -> None:
        # GIVEN - a path that doesn't exist
        vrscene_path = str(tmp_path / "nonexistent.vrscene")

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(vrscene_path)

        # THEN
        assert result == set()

    def test_ignores_non_file_string_params(self, tmp_path: Path) -> None:
        """String parameters that don't look like file paths should be ignored"""
        # GIVEN
        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            "SettingsOutput settingsOutput1 {\n"
            '    img_dir="renders/output";\n'
            '    img_file="beauty";\n'
            "}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        # These don't have recognized file extensions and aren't known file attributes
        assert len(result) == 0

    def test_handles_realistic_vrscene_content(self, tmp_path: Path) -> None:
        """Test with a realistic vrscene file structure"""
        # GIVEN
        tex1 = tmp_path / "diffuse.exr"
        tex2 = tmp_path / "normal.png"
        proxy = tmp_path / "tree.vrmesh"
        for f in [tex1, tex2, proxy]:
            f.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            "// V-Ray scene file\n"
            "// Exported by V-Ray for Maya\n"
            "\n"
            f"BitmapBuffer diffuse_bitmap {{\n"
            f'    file="{tex1}";\n'
            f"    gamma=2.2;\n"
            f"    color_space=2;\n"
            f"}}\n"
            f"\n"
            f"TexBitmap diffuse_tex {{\n"
            f"    bitmap=diffuse_bitmap;\n"
            f"    uvwgen=uvwGen1;\n"
            f"}}\n"
            f"\n"
            f"BitmapBuffer normal_bitmap {{\n"
            f'    file="{tex2}";\n'
            f"    gamma=1.0;\n"
            f"}}\n"
            f"\n"
            f"GeomMeshFile tree_proxy {{\n"
            f'    file="{proxy}";\n'
            f"    anim_type=0;\n"
            f"}}\n"
            f"\n"
            f"BRDFVRayMtl material1 {{\n"
            f"    diffuse=diffuse_tex;\n"
            f"    bump_map=normal_bitmap;\n"
            f"}}\n"
            f"\n"
            f"Node tree_node {{\n"
            f"    geometry=tree_proxy;\n"
            f"    material=material1;\n"
            f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert len(result) == 3
        assert Path(os.path.normpath(str(tex1))) in result
        assert Path(os.path.normpath(str(tex2))) in result
        assert Path(os.path.normpath(str(proxy))) in result

    def test_expands_udim_tokens(self, tmp_path: Path) -> None:
        """Test that <UDIM> tokens in texture paths are expanded to all matching tile files"""
        # GIVEN
        textures_dir = tmp_path / "textures"
        textures_dir.mkdir()
        # Create UDIM tile files
        tile_files = []
        for udim in [1001, 1002, 1003, 1011]:
            tile = textures_dir / f"wood_{udim}.exr"
            tile.touch()
            tile_files.append(tile)
        # Also create an unrelated file that shouldn't match
        (textures_dir / "stone_1001.exr").touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f"BitmapBuffer bitmap1 {{\n" f'    file="{textures_dir}/wood_<UDIM>.exr";\n' f"}}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        for tile in tile_files:
            assert Path(os.path.normpath(str(tile))) in result
        # The unrelated file should not be included
        assert Path(os.path.normpath(str(textures_dir / "stone_1001.exr"))) not in result

    def test_udim_with_missing_directory(self, tmp_path: Path) -> None:
        """Test that UDIM paths with non-existent directories are still added as pattern paths"""
        # GIVEN
        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            "BitmapBuffer bitmap1 {\n" '    file="/nonexistent/dir/wood_<UDIM>.exr";\n' "}\n"
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath("/nonexistent/dir/wood_<UDIM>.exr")) in result

    def test_parses_compact_single_line_format(self, tmp_path: Path) -> None:
        """Test parsing vrscene files where plugin blocks are on a single line (compact export)"""
        # GIVEN
        tex1 = tmp_path / "AI52_001_Table_Glossiness.png"
        tex2 = tmp_path / "AI52_001_Table_Diffuse.exr"
        tex1.touch()
        tex2.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f'BitmapBuffer glossiness_map@bitmap {{filter_type=5;filter_blur=1;color_space=2;rgb_color_space="lin_srgb";gamma=1;maya_compatible=1;allow_negative_colors=1;file="{tex1}";load_file=1;ifl_start_frame=0;ifl_playback_rate=1;ifl_end_condition=0;}}'
            f'BitmapBuffer diffuse_map@bitmap {{filter_type=5;filter_blur=1;color_space=2;rgb_color_space="lin_srgb";gamma=1;maya_compatible=1;allow_negative_colors=1;file="{tex2}";load_file=1;ifl_start_frame=0;ifl_playback_rate=1;ifl_end_condition=0;}}'
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert Path(os.path.normpath(str(tex1))) in result
        assert Path(os.path.normpath(str(tex2))) in result

    def test_parses_mixed_compact_and_multiline_format(self, tmp_path: Path) -> None:
        """Test parsing vrscene files with a mix of compact and multi-line blocks"""
        # GIVEN
        tex1 = tmp_path / "compact_texture.png"
        tex2 = tmp_path / "multiline_texture.exr"
        proxy = tmp_path / "model.vrmesh"
        tex1.touch()
        tex2.touch()
        proxy.touch()

        vrscene_file = tmp_path / "scene.vrscene"
        vrscene_file.write_text(
            f'BitmapBuffer compact_bitmap {{filter_type=5;file="{tex1}";gamma=1;}}\n'
            f"BitmapBuffer multiline_bitmap {{\n"
            f'    file="{tex2}";\n'
            f"    gamma=2.2;\n"
            f"}}\n"
            f'GeomMeshFile proxy1 {{file="{proxy}";anim_type=0;}}\n'
        )

        # WHEN
        result = assets_module.AssetIntrospector()._parse_vrscene_file(str(vrscene_file))

        # THEN
        assert len(result) == 3
        assert Path(os.path.normpath(str(tex1))) in result
        assert Path(os.path.normpath(str(tex2))) in result
        assert Path(os.path.normpath(str(proxy))) in result

# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
"""Unit tests for ``deadline.maya_submitter.renderers`` output-prefix resolution.

These cover the renderer-aware base-prefix behaviour that fixes the V-Ray output-prefix
bug (upstream issue #443): V-Ray keeps its filename prefix on ``vraySettings.fileNamePrefix``
rather than ``defaultRenderGlobals.imageFilePrefix``, so a V-Ray scene must resolve its
prefix from the V-Ray node instead of falling back to the ``<Scene>`` submitter default.
"""

from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

# ``renderers`` imports ``render_layers`` which imports ``maya.mel``; that submodule is not
# in the package-level mock list, so stub it before importing the module under test.
sys.modules.setdefault("maya.mel", MagicMock())

from deadline.maya_submitter import renderers  # noqa: E402


def _fake_getattr(values: dict[str, object]):
    """Return a getAttr replacement that looks up attribute paths in ``values``."""

    def _getattr(attr, *args, **kwargs):
        return values.get(attr)

    return _getattr


class TestGetPrefixAttribute:
    def test_vray_uses_vray_settings_attribute(self) -> None:
        values = {"defaultRenderGlobals.currentRenderer": "vray"}
        with patch.object(
            renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)
        ), patch.object(renderers.maya.cmds, "objExists", return_value=True):
            assert renderers._get_prefix_attribute() == "vraySettings.fileNamePrefix"

    def test_vray_missing_settings_node_falls_back_to_default_attribute(self) -> None:
        values = {"defaultRenderGlobals.currentRenderer": "vray"}
        with patch.object(
            renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)
        ), patch.object(renderers.maya.cmds, "objExists", return_value=False):
            assert renderers._get_prefix_attribute() == "defaultRenderGlobals.imageFilePrefix"

    def test_non_vray_uses_default_attribute(self) -> None:
        values = {"defaultRenderGlobals.currentRenderer": "arnold"}
        with patch.object(renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)):
            assert renderers._get_prefix_attribute() == "defaultRenderGlobals.imageFilePrefix"


class TestGetBaseOutputPrefix:
    def test_vray_reads_vray_file_name_prefix(self) -> None:
        # GIVEN a V-Ray scene whose prefix lives on vraySettings and whose
        # defaultRenderGlobals prefix is empty (the usual V-Ray case).
        values = {
            "defaultRenderGlobals.currentRenderer": "vray",
            "defaultRenderGlobals.imageFilePrefix": "",
            "vraySettings.fileNamePrefix": "shots/<Layer>/beauty",
        }
        with patch.object(
            renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)
        ), patch.object(renderers.maya.cmds, "objExists", return_value=True):
            assert renderers._get_base_output_prefix() == "shots/<Layer>/beauty"

    def test_vray_empty_prefix_falls_back_to_scene_token(self) -> None:
        # For V-Ray, the legacy defaultRenderGlobals attribute is not consulted; an empty
        # vraySettings prefix resolves straight to the <Scene> default.
        values = {
            "defaultRenderGlobals.currentRenderer": "vray",
            "defaultRenderGlobals.imageFilePrefix": "unused/legacy",
            "vraySettings.fileNamePrefix": "",
        }
        with patch.object(
            renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)
        ), patch.object(renderers.maya.cmds, "objExists", return_value=True):
            assert renderers._get_base_output_prefix() == "<Scene>"

    def test_vray_missing_node_uses_render_globals(self) -> None:
        # GIVEN V-Ray is the renderer but the vraySettings node does not exist.
        values = {
            "defaultRenderGlobals.currentRenderer": "vray",
            "defaultRenderGlobals.imageFilePrefix": "globals/prefix",
        }
        with patch.object(
            renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)
        ), patch.object(renderers.maya.cmds, "objExists", return_value=False):
            assert renderers._get_base_output_prefix() == "globals/prefix"

    def test_non_vray_uses_render_globals(self) -> None:
        values = {
            "defaultRenderGlobals.currentRenderer": "arnold",
            "defaultRenderGlobals.imageFilePrefix": "arnold/prefix",
        }
        with patch.object(renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)):
            assert renderers._get_base_output_prefix() == "arnold/prefix"

    def test_non_vray_empty_prefix_falls_back_to_scene_token(self) -> None:
        values = {
            "defaultRenderGlobals.currentRenderer": "mayaSoftware",
            "defaultRenderGlobals.imageFilePrefix": "",
        }
        with patch.object(renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)):
            assert renderers._get_base_output_prefix() == "<Scene>"


class TestGetOutputPrefixWithTokens:
    def test_vray_prefix_flows_through_token_expansion(self) -> None:
        # GIVEN a V-Ray scene with a single layer and single camera (no tokens prepended).
        values = {
            "defaultRenderGlobals.currentRenderer": "vray",
            "vraySettings.fileNamePrefix": "myshot/beauty",
        }
        with patch.object(
            renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)
        ), patch.object(renderers.maya.cmds, "objExists", return_value=True), patch.object(
            renderers, "get_renderable_camera_names", return_value=["persp"]
        ), patch.object(
            renderers, "get_all_renderable_render_layer_names", return_value=["masterLayer"]
        ):
            assert renderers.get_output_prefix_with_tokens() == "myshot/beauty"

    def test_vray_prefix_gets_layer_and_camera_tokens_when_multiple(self) -> None:
        # GIVEN multiple layers and cameras and a V-Ray prefix lacking those tokens.
        values = {
            "defaultRenderGlobals.currentRenderer": "vray",
            "vraySettings.fileNamePrefix": "beauty",
        }
        with patch.object(
            renderers.maya.cmds, "getAttr", side_effect=_fake_getattr(values)
        ), patch.object(renderers.maya.cmds, "objExists", return_value=True), patch.object(
            renderers, "get_renderable_camera_names", return_value=["camA", "camB"]
        ), patch.object(
            renderers, "get_all_renderable_render_layer_names", return_value=["layerA", "layerB"]
        ):
            # Layer token is prepended first, then camera -> "<Layer>/<Camera>/beauty".
            assert renderers.get_output_prefix_with_tokens() == "<Layer>/<Camera>/beauty"

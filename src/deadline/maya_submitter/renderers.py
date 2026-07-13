# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
from __future__ import annotations

from collections import deque

import maya.cmds

from .cameras import get_renderable_camera_names
from .render_layers import get_all_renderable_render_layer_names


def get_width() -> int:
    """
    Retrieves the width as currently specified
    """
    return maya.cmds.getAttr("defaultResolution.width")


def get_height() -> int:
    """
    Retrieves the height as currently specified.
    """
    return maya.cmds.getAttr("defaultResolution.height")


_LAYER_TOKENS = ("<Layer>", "<RenderLayer>", "%l")
_CAMERA_TOKENS = ("<Camera>", "%c")


def _get_current_renderer() -> str:
    """
    Returns the active renderer as recorded in the scene (e.g. "vray"), or "" if unknown.
    """
    try:
        return maya.cmds.getAttr("defaultRenderGlobals.currentRenderer") or ""
    except Exception:
        return ""


def _get_vray_file_name_prefix() -> str:
    """
    Returns the V-Ray output filename prefix (``vraySettings.fileNamePrefix``), or "".

    V-Ray does not use ``defaultRenderGlobals.imageFilePrefix``; it stores the artist's
    configured output prefix on the ``vraySettings`` node. The node may not exist (e.g.
    V-Ray not loaded), so this is guarded.
    """
    try:
        if not maya.cmds.objExists("vraySettings"):
            return ""
        return maya.cmds.getAttr("vraySettings.fileNamePrefix") or ""
    except Exception:
        return ""


def _get_base_output_prefix():
    """
    Retrieves the output prefix as specified in the scene.

    The prefix source is renderer-aware: V-Ray keeps its filename prefix on
    ``vraySettings.fileNamePrefix`` rather than ``defaultRenderGlobals.imageFilePrefix``
    (which is typically empty for a V-Ray scene). For a V-Ray scene we therefore prefer the
    V-Ray prefix so the artist's configured naming is respected, falling back to the standard
    render-globals prefix and finally to the ``<Scene>`` default.
    """
    if _get_current_renderer() == "vray":
        vray_prefix = _get_vray_file_name_prefix()
        if vray_prefix:
            return vray_prefix

    prefix = maya.cmds.getAttr("defaultRenderGlobals.imageFilePrefix")
    if prefix:
        return prefix
    return "<Scene>"


def get_output_prefix_with_tokens():
    """
    Retrieves the Output Prefix adding in all missing tokens
    """
    prefix = _get_base_output_prefix()

    sections = deque(prefix.split("/"))

    if len(get_renderable_camera_names()) > 1 and not any(
        token in prefix for token in _CAMERA_TOKENS
    ):
        sections.appendleft("<Camera>")
    if len(get_all_renderable_render_layer_names()) > 1 and not any(
        token in prefix for token in _LAYER_TOKENS
    ):
        sections.appendleft("<Layer>")

    return "/".join(sections)

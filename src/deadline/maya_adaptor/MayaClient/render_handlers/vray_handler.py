# Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.

import os

from .default_maya_handler import DefaultMayaHandler
from ..dir_map import DirectoryMapping

import maya.cmds
import maya.mel


class VRayHandler(DefaultMayaHandler):
    """Render Handler for V-Ray"""

    def __init__(self):
        """
        Initializes the V-Ray Renderer Handler.
        """
        super().__init__()
        self.action_dict["vrscene_pathmapping"] = self.set_vrscene_pathmapping

    def vraySettingsNodeExists(self) -> bool:
        """
        Check if vraySettings node exists. If not found, an attempt will be made to create it.

        Returns True if vraySettings node was found or created successfully.
        """
        if maya.cmds.objExists("vraySettings"):
            return True

        print("MayaClient: vraySettings node not found in the scene!", flush=True)

        if not maya.mel.eval("exists vrayCreateVRaySettingsNode"):
            # V-Ray is probably not loaded? Wrong version? Unable to create vray node
            return False

        maya.mel.eval("vrayCreateVRaySettingsNode")
        if not maya.cmds.objExists("vraySettings"):
            print("MayaClient: Unable to create vraySettings node.", flush=True)
            return False

        print("MayaClient: Created default vraySettings node.", flush=True)
        return True

    def start_render(self, data: dict) -> None:
        """
        Starts a render.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['frame']

        Raises:
            RuntimeError: Thrown when a render-time error is found.
            Causes include:
            - If no camera was specified
            - No renderable camera was found
            - No frame was specified
            - No vraySettings node found in the scene
            - Output image type is not exr for a region render
        """
        if not maya.cmds.pluginInfo("vrayformaya", query=True, loaded=True):
            raise RuntimeError(
                "MayaClient: The VRay for Maya plugin was not loaded. Please verify that VRay is installed."
            )

        frame = data.get("frame")
        if frame is None:
            raise RuntimeError("MayaClient: start_render called without a frame number.")

        self.camera_name = self.get_camera_to_render(data)
        if self.camera_name is None:
            raise RuntimeError("MayaClient: start_render called without a valid camera.")
        self.render_kwargs["camera"] = self.camera_name

        if not self.vraySettingsNodeExists():
            raise RuntimeError(
                "MayaClient: start_render called with missing vraySettings node in the scene."
            )

        # In order of preference, use the task's output_file_prefix, the step's output_file_prefix, or the scene file setting.
        output_file_prefix = data.get("output_file_prefix", self.output_file_prefix)
        if output_file_prefix:
            maya.cmds.setAttr(
                "defaultRenderGlobals.imageFilePrefix", output_file_prefix, type="string"
            )

        if self.image_width is not None:
            maya.cmds.setAttr("vraySettings.width", self.image_width)
            print(f"Set image width to {self.image_width}", flush=True)
        if self.image_height is not None:
            maya.cmds.setAttr("vraySettings.height", self.image_height)
            print(f"Set image height to {self.image_height}", flush=True)

        # Always set the animation type to 2 (specific frames) and one frame animation
        maya.cmds.setAttr("vraySettings.animType", 2)
        maya.cmds.setAttr("vraySettings.animFrames", str(frame), type="string")

        # We want to write images so force set the translator settings for rendering an image without vrscene file export.
        maya.cmds.setAttr("vraySettings.vrscene_render_on", 1)
        maya.cmds.setAttr("vraySettings.vrscene_on", 0)

        # Set the V-Ray GPU engine from 3 (RTX mode) to 2 (CUDA mode)
        if maya.cmds.getAttr("vraySettings.productionEngine") == 3:
            maya.cmds.setAttr("vraySettings.productionEngine", 2)
            print("MayaClient: Changing V-Ray GPU engine from RTX to CUDA mode.", flush=True)

        # Disable distributed rendering
        maya.cmds.setAttr("vraySettings.sys_distributed_rendering_on", 0)

        # Adjust the output settings
        maya.cmds.setAttr("vraySettings.dontSaveImage", 0)
        maya.cmds.setAttr("vraySettings.noAlpha", 0)
        maya.cmds.setAttr("vraySettings.dontSaveRgbChannel", 0)

        # Set the log message level to 3 (report errors, warnings and general information) if needed
        if maya.cmds.getAttr("vraySettings.sys_message_level") < 3:
            maya.cmds.setAttr("vraySettings.sys_message_level", 3)

        # Perform setup for region rendering if needed, otherwise just use the output size from the submission
        region = [
            data.get(field)
            for field in ("region_min_x", "region_min_y", "region_max_x", "region_max_y")
        ]
        if any(v is not None for v in region):
            print(f"MayaClient: Region bounds {region} specified.", flush=True)

            region_minX, region_minY, region_maxX, region_maxY = region
            region_str = (
                f"(minX={region_minX}, minY={region_minY}, maxX={region_maxX}, maxY={region_maxY})"
            )
            if any(v is None for v in region):
                raise RuntimeError(
                    f"MayaClient: Region bounds {region_str} must be fully defined or all empty, but were partially specified."
                )

            # Set the output filename
            maya.cmds.setAttr(
                "vraySettings.fileNamePrefix", data.get("output_file_prefix"), type="string"
            )

            # Set to allow region in batch rendering
            maya.cmds.setAttr("vraySettings.vfbRgnOffBatch", 0)

            # Set to use VFB
            maya.cmds.setAttr("vraySettings.vfbOn", 1)

            # Make sure the image output is set to EXR. If it isn't the region render will not work so we must fail
            ouput_extension = maya.cmds.getAttr("vraySettings.imageFormatStr")

            # The exr extension name can be one of "exr", "exr (deep)", or "exr (multichannel)"
            if "exr" not in ouput_extension.lower():
                raise RuntimeError(
                    f'MayaClient: Output image format is set to "{ouput_extension}" but must be EXR for region rendering to work.'
                )

            # Set the region to render
            region_cmd = f"vray vfbControl -setregion {region_minX} {region_minY} {region_maxX} {region_maxY}; vraySetBatchDoRegion {region_minX} {region_minY} {region_maxX} {region_maxY};"

            print(f"Setting render region: {region_cmd}")
            maya.mel.eval(region_cmd)

            get_region_cmd = "vray vfbControl -getregion"
            print(
                f"Checking the region is set correctly: {maya.mel.eval(get_region_cmd)}", flush=True
            )

        maya.cmds.vrend(**self.render_kwargs)
        print(f"MayaClient: Finished Rendering Frame {frame}\n", flush=True)

    def set_output_file_prefix(self, data: dict) -> None:
        """
        Sets the output file prefix.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['output_file_prefix']
        """
        prefix = data.get("output_file_prefix")
        if prefix and self.vraySettingsNodeExists():
            maya.cmds.setAttr("vraySettings.fileNamePrefix", prefix, type="string")

    def set_render_layer(self, data: dict) -> None:
        """
        Sets the render layer.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: ['render_layer']

        Raises:
            RuntimeError: If the render layer cannot be found
        """
        render_layer_name = self.get_render_layer_to_render(data)
        if render_layer_name:
            maya.cmds.editRenderLayerGlobals(currentRenderLayer=render_layer_name)

    def set_vrscene_pathmapping(self, data: dict) -> None:
        """
        Applies path mapping rules to file paths inside .vrscene files referenced
        by VRayScene nodes.

        Maya's dirmap remaps the VRayScene node's FilePath attribute, but V-Ray's
        own parser reads the paths inside the vrscene file directly. This method
        rewrites those internal paths so textures, proxies, and other assets resolve
        correctly on the worker.

        Args:
            data (dict): The data given from the Adaptor. Keys expected: []
        """
        if not DirectoryMapping.get_activated():
            return

        vrscene_nodes = maya.cmds.ls(type="VRayScene") or []
        if not vrscene_nodes:
            return

        # Collect the mapping rules once
        rules = list(DirectoryMapping.mappings.items())
        if not rules:
            return

        patched_files: set[str] = set()

        for node in vrscene_nodes:
            if not maya.cmds.attributeQuery("FilePath", node=node, exists=True):
                continue

            # Get the already-remapped path (dirmap applies to the attribute value)
            vrscene_path = maya.cmds.getAttr(f"{node}.FilePath")
            if not vrscene_path or not isinstance(vrscene_path, str):
                continue

            vrscene_path = vrscene_path.strip()
            if not vrscene_path or not os.path.isfile(vrscene_path):
                continue

            # Avoid patching the same file twice (multiple nodes can reference it)
            norm_path = os.path.normpath(vrscene_path)
            if norm_path in patched_files:
                continue
            patched_files.add(norm_path)

            self._remap_vrscene_file(norm_path, rules)

    @staticmethod
    def _remap_vrscene_file(vrscene_path: str, rules: list[tuple[str, str]]) -> None:
        """
        Rewrites file paths inside a .vrscene file using the given path mapping rules.

        Only replaces paths inside quoted string parameter values (name="value";)
        to avoid corrupting other data in the file.

        Args:
            vrscene_path: Absolute path to the .vrscene file on disk.
            rules: List of (source_prefix, dest_prefix) path mapping pairs.
        """
        try:
            with open(vrscene_path, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()
        except (OSError, IOError) as e:
            print(f"MayaClient: Warning: Could not read vrscene file for path mapping: {e}")
            return

        original_content = content

        # Replace paths inside quoted string values only.
        # Pattern matches: ="<path>"; and we substitute inside the quotes.
        for source, dest in rules:
            if source not in content:
                continue
            # Normalize separators: vrscene files may use forward or back slashes
            for src_variant in (source, source.replace("/", "\\"), source.replace("\\", "/")):
                if src_variant in content:
                    content = content.replace(src_variant, dest)

        if content != original_content:
            try:
                with open(vrscene_path, "w", encoding="utf-8") as f:
                    f.write(content)
                print(
                    f"MayaClient: Applied path mapping to vrscene file: {vrscene_path}",
                    flush=True,
                )
            except (OSError, IOError) as e:
                print(
                    f"MayaClient: Warning: Could not write path-mapped vrscene file: {e}",
                    flush=True,
                )

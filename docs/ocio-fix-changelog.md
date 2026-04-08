# OCIO Config Path Fix — Changelog & Technical Notes

## Problem

When the `OCIO` environment variable is set by pipeline tools (AYON, ShotGrid, etc.) to configure color management globally, the Deadline Cloud Maya submitter and adaptor did not detect or properly handle it. This caused:

1. The submitter ignored the `OCIO` env var — only checked `maya.cmds.colorManagementPrefs`
2. The `OCIOConfigFile` job parameter was never populated
3. On the worker, Maya/V-Ray tried to load the unmapped Windows path (e.g. `Z:\OCIO\Maya2022-default\config.ocio`) embedded in the scene file
4. V-Ray fell back to raw (no color management), producing incorrect renders

## Fix Iterations

### fix/ocio-v1 — OCIO env var detection + pre-scene action ordering

**Files changed:** `scene.py`, `adaptor.py`, `default_maya_handler.py`

- `Scene.ocio_config_file()` now falls back to `os.environ.get("OCIO")` when Maya's `colorManagementPrefs` returns a `<MAYA_RESOURCES>` path or no usable path
- Moved `ocio_config_file` from `_MAYA_INIT_KEYS` (processed after scene open) to `_PRE_SCENE_OPTIONAL_KEYS` (processed before scene open)
- `set_ocio_config_file()` sets the `OCIO` env var and stores the path for deferred `colorManagementPrefs` after scene open

**Result:** V-Ray no longer falls back to raw — it reads the `OCIO` env var. However, Maya's own color management still logs errors about the unmapped path during scene open.

### fix/ocio-v2 — Set colorManagementPrefs before scene open

**Files changed:** `default_maya_handler.py`

- Added `maya.cmds.colorManagementPrefs(e=True, configFilePath=...)` call in `set_ocio_config_file()` before scene opens

**Result:** No effect — Maya overrides the global prefs with the path embedded in the scene file when `maya.cmds.file(open=True)` runs.

### fix/ocio-v3 — Disable color management during scene open

**Files changed:** `default_maya_handler.py`

- Temporarily disabled `cmEnabled` before `maya.cmds.file(open=True)`, re-enabled after with correct path

**Result:** No effect — Maya still reads and attempts to load the `.cfp` attribute from the scene file regardless of `cmEnabled` state.

### fix/ocio-v4 — Rewrite OCIO path in .ma scene file before opening

**Files changed:** `default_maya_handler.py`

- Before `maya.cmds.file(open=True)`, reads the `.ma` file as text and replaces the `.cfp` attribute value with the correct mapped path using regex
- Pattern matched: `setAttr ".cfp" -type "string" "<path>";`
- Falls back gracefully if the file can't be read/written

**Result:** All OCIO errors eliminated. Maya opens the scene with the correct path already in place. V-Ray and Maya both use the correct OCIO config.

## Known Limitations

### .mb (Maya Binary) files

The scene file patching in v4 only works for `.ma` (ASCII) files. For `.mb` (binary) files:

- The `.cfp` attribute cannot be rewritten before opening because the format is binary
- The `OCIO` env var is still set before scene open, so **V-Ray will use the correct OCIO config** (V-Ray reads `OCIO` env var directly)
- Maya's internal color management will still log the warning about the unmapped path, but this is cosmetic
- After scene open, `colorManagementPrefs` is called with the correct path, fixing Maya's internal state
- **The render result will be correct** — V-Ray's color pipeline is driven by the `OCIO` env var, not Maya's `colorManagementPrefs`

### Will the render result always be the same with V-Ray?

**Yes, for the actual pixel data.** V-Ray reads the OCIO config from the `OCIO` environment variable at render time. Since we set this before the scene opens (in all versions v1+), V-Ray always has the correct config regardless of whether the `.ma` patching succeeds or not.

The differences between versions are only about **suppressing Maya's error messages**, not about render correctness:

| Version | V-Ray render correct? | Maya OCIO errors? |
|---------|----------------------|-------------------|
| No fix  | No (raw fallback)    | Yes               |
| v1      | Yes                  | Yes               |
| v2      | Yes                  | Yes               |
| v3      | Yes                  | Yes               |
| v4 (.ma)| Yes                  | No                |
| v4 (.mb)| Yes                  | Yes (cosmetic)    |

### Recommendation

Use v4 (`fix/ocio-v4`). It provides the cleanest logs for `.ma` files and correct renders for both `.ma` and `.mb` files. The remaining Maya warnings for `.mb` files are cosmetic and do not affect the render output.

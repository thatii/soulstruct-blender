from __future__ import annotations

__all__ = [
    "ExportLooseHKXMapCollision",
    "ExportHKXMapCollisionIntoBinder",
    "ExportHKXMapCollisionIntoHKXBHD",
    "ExportMSBMapCollision",
]

import re
import traceback
import typing as tp
from pathlib import Path

import numpy as np

import bpy
from bpy.props import StringProperty, BoolProperty, IntProperty
from bpy_extras.io_utils import ImportHelper, ExportHelper

from soulstruct.dcx import DCXType
from soulstruct.containers import Binder, BinderEntry
from soulstruct.games import DARK_SOULS_DSR
from soulstruct_havok.wrappers.hkx2015 import MapCollisionHKX

from io_soulstruct.general.cached import get_cached_file
from io_soulstruct.utilities import *
from .utilities import *

if tp.TYPE_CHECKING:
    from io_soulstruct.type_checking import MSB_TYPING

LOOSE_HKX_COLLISION_NAME_RE = re.compile(r"^([hl])(\w{6})A(\d\d)$")  # game-readable model name; no extensions
NUMERIC_HKX_COLLISION_NAME_RE = re.compile(r"^([hl])(\d{4})B(\d)A(\d\d)$")  # standard map model name; no extensions


def get_mesh_children(
    operator: LoggingOperator, bl_parent: bpy.types.Object, get_other_resolution: bool
) -> tuple[list, list, str]:
    """Return a tuple of `(bl_meshes, other_res_bl_meshes, other_res)`."""
    bl_meshes = []
    other_res_bl_meshes = []
    other_res = ""

    target_res = bl_parent.name[0]
    if get_other_resolution:
        match target_res:
            case "h":
                other_res = "l"
            case "l":
                other_res = "h"
            case _:
                raise HKXMapCollisionExportError(
                    f"Selected Empty parent '{bl_parent.name}' must start with 'h' or 'l' to get other resolution."
                )

    for child in bl_parent.children:
        child_res = child.name.lower()[0]
        if child.type != "MESH":
            operator.warning(f"Ignoring non-mesh child '{child.name}' of selected Empty parent.")
        elif child_res == target_res:
            bl_meshes.append(child)
        elif get_other_resolution and child_res == other_res:  # cannot be empty here
            other_res_bl_meshes.append(child)
        else:
            operator.warning(f"Ignoring child '{child.name}' of selected Empty parent with non-'h', non-'l' name.")

    # Ensure meshes have the same order as they do in the Blender viewer.
    bl_meshes.sort(key=lambda obj: natural_keys(obj.name))
    other_res_bl_meshes.sort(key=lambda obj: natural_keys(obj.name))

    return bl_meshes, other_res_bl_meshes, other_res


class ExportLooseHKXMapCollision(LoggingOperator, ExportHelper):
    """Export HKX from a selection of Blender meshes."""
    bl_idname = "export_scene.hkx_map_collision"
    bl_label = "Export Loose Map Collision"
    bl_description = "Export child meshes of selected Blender empty parent to a HKX collision file"

    # ExportHelper mixin class uses this
    filename_ext = ".hkx"

    filter_glob: StringProperty(
        default="*.hkx;*.hkx.dcx",
        options={'HIDDEN'},
        maxlen=255,  # Max internal buffer length, longer would be clamped.
    )

    dcx_type: get_dcx_enum_property(DCXType.Null)  # typically no DCX compression for map collisions

    write_other_resolution: BoolProperty(
        name="Write Other Resolution",
        description="Write the other resolution of the collision (h/l) if its submeshes are also under this parent",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        """Must select a single empty parent of only (and at least one) child meshes."""
        settings = cls.settings(context)
        if not settings.is_game(DARK_SOULS_DSR):
            return False  # TODO: DS1R only.
        is_empty_selected = len(context.selected_objects) == 1 and context.selected_objects[0].type == "EMPTY"
        if not is_empty_selected:
            return False
        children = context.selected_objects[0].children
        return len(children) >= 1 and all(child.type == "MESH" for child in children)

    def invoke(self, context, _event):
        """Set default export name to name of object (before first space and without Blender dupe suffix)."""
        if not context.selected_objects:
            return super().invoke(context, _event)

        obj = context.selected_objects[0]
        if obj.get("Model File Stem", None) is not None:
            self.filepath = obj["Model File Stem"] + ".hkx"
        self.filepath = obj.name.split(" ")[0].split(".")[0] + ".hkx"
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        selected_objs = [obj for obj in context.selected_objects]
        if not selected_objs:
            return self.error("No Empty with child meshes selected for HKX export.")
        if len(selected_objs) > 1:
            return self.error("More than one object cannot be selected for HKX export.")
        hkx_parent = selected_objs[0]

        hkx_path = Path(self.filepath)
        if not LOOSE_HKX_COLLISION_NAME_RE.match(hkx_path.name) is None:
            return self.warning(
                f"HKX file name '{hkx_path.name}' does not match the expected name pattern for "
                f"a HKX collision parent object and will not function in-game: 'h......A..' or 'l......A..'"
            )
        # NOTE: We don't care if 'Model File Stem' doesn't match here.
        hkx_entry_stem = hkx_path.name.split(".")[0]  # needed for internal HKX name

        try:
            bl_meshes, other_res_bl_meshes, other_res = get_mesh_children(self, hkx_parent, self.write_other_resolution)
        except HKXMapCollisionExportError as ex:
            traceback.print_exc()
            return self.error(f"Children of object '{hkx_parent.name}' cannot be exported. Error: {ex}")

        # TODO: Not needed for meshes only?
        if bpy.ops.object.mode_set.poll():
            bpy.ops.object.mode_set(mode="OBJECT", toggle=False)

        exporter = HKXMapCollisionExporter(self, context)

        try:
            hkx = exporter.export_hkx_map_collision(bl_meshes, name=hkx_entry_stem)
        except Exception as ex:
            traceback.print_exc()
            return self.error(f"Cannot get exported HKX for '{hkx_parent.name}'. Error: {ex}")
        hkx.dcx_type = DCXType[self.dcx_type]

        other_res_hkx = None
        if other_res:
            other_res_hkx_entry_stem = f"{other_res}{hkx_entry_stem[1:]}"  # needed for internal HKX name
            if other_res_bl_meshes:
                try:
                    other_res_hkx = exporter.export_hkx_map_collision(
                        other_res_bl_meshes, name=other_res_hkx_entry_stem
                    )
                except Exception as ex:
                    traceback.print_exc()
                    return self.error(
                        f"Cannot get exported HKX for other resolution '{other_res_hkx_entry_stem}. Error: {ex}"
                    )
                other_res_hkx.dcx_type = DCXType[self.dcx_type]
            else:
                self.warning(f"No Blender mesh children found for other resolution '{other_res_hkx_entry_stem}'.")

        try:
            # Will create a `.bak` file automatically if absent.
            hkx.write(hkx_path)
        except Exception as ex:
            traceback.print_exc()
            return self.error(f"Cannot write exported HKX '{hkx_parent.name}' to '{hkx_path}'. Error: {ex}")

        if other_res_hkx:
            other_res_hkx_path = hkx_path.with_name(f"{other_res}{hkx_path.name[1:]}")  # `other_res` guaranteed here
            try:
                # Will create a `.bak` file automatically if absent.
                other_res_hkx.write(other_res_hkx_path)
            except Exception as ex:
                traceback.print_exc()
                return self.error(
                    f"Wrote target resolution HKX '{hkx_path}', but cannot write other-resolution HKX "
                    f"to '{other_res_hkx_path}'. Error: {ex}"
                )

        return {"FINISHED"}


class ExportHKXMapCollisionIntoBinder(LoggingOperator, ImportHelper):
    bl_idname = "export_scene.hkx_map_collision_binder"
    bl_label = "Export Map Collision Into Binder"
    bl_description = "Export a HKX collision file into a FromSoftware Binder (BND/BHD)"

    # ImportHelper mixin class uses this
    filename_ext = ".hkxbhd"

    filter_glob: StringProperty(
        default="*.hkxbhd;*.hkxbhd.dcx",
        options={'HIDDEN'},
        maxlen=255,
    )

    dcx_type: get_dcx_enum_property(DCXType.DS1_DS2)  # map collisions in DS1 binder are compressed

    write_other_resolution: BoolProperty(
        name="Write Other Resolution",
        description="Write the other resolution of the collision (h/l) if its submeshes are also under this parent",
        default=True,
    )

    overwrite_existing: BoolProperty(
        name="Overwrite Existing",
        description="Overwrite first existing '{name}.hkx{.dcx}' matching entry in Binder",
        default=True,
    )

    default_entry_flags: IntProperty(
        name="Default Flags",
        description="Flags to set to Binder entry if it needs to be created",
        default=0x2,
    )

    default_entry_path: StringProperty(
        name="Default Path",
        description="Path prefix to use for Binder entry if it needs to be created. Use {name} as a format "
                    "placeholder for the name of this HKX object and {map} as a format placeholder for map string "
                    "'mAA_BB_00_00', which will try to be detected from HKX name (eg 'h0500B1A12' -> 'm12_01_00_00')",
        default="{map}\\{name}.hkx.dcx",  # note that HKX files inside DSR BHDs are indeed DCX-compressed
    )

    @classmethod
    def poll(cls, context):
        """Must select a single empty parent of only (and at least one) child meshes."""
        settings = cls.settings(context)
        if not settings.is_game(DARK_SOULS_DSR):
            return False  # TODO: DS1R only.
        is_empty_selected = len(context.selected_objects) == 1 and context.selected_objects[0].type == "EMPTY"
        if not is_empty_selected:
            return False
        children = context.selected_objects[0].children
        return len(children) >= 1 and all(child.type == "MESH" for child in children)

    def execute(self, context):
        print("Executing HKX export to Binder...")

        selected_objs = [obj for obj in context.selected_objects]
        if not selected_objs:
            return self.error("No Empty with child meshes selected for HKX export.")
        if len(selected_objs) > 1:
            return self.error("More than one object cannot be selected for HKX export.")
        hkx_parent = selected_objs[0]

        hkx_binder_path = Path(self.filepath)

        hkx_entry_stem = hkx_parent.get("Model File Stem", get_bl_obj_stem(hkx_parent))
        if not LOOSE_HKX_COLLISION_NAME_RE.match(hkx_entry_stem) is None:
            self.warning(
                f"HKX map collision model name '{hkx_entry_stem}' should generally be 'h....B.A..' or 'l....B.A..'."
            )
        # NOTE: If this is a new collision, its name must be in standard numeric format so that the map can be
        # detected for the new Binder entry path.
        # TODO: Honestly, probably don't need the full entry path in the Binder.

        try:
            bl_meshes, other_res_bl_meshes, other_res = get_mesh_children(self, hkx_parent, self.write_other_resolution)
        except HKXMapCollisionExportError as ex:
            raise HKXMapCollisionExportError(f"Children of object '{hkx_parent}' cannot be exported. Error: {ex}")

        hkxbhd, other_res_hkxbhd = load_hkxbhds(hkx_binder_path, other_res=other_res)

        try:
            export_hkx_to_binder(
                self,
                context,
                bl_meshes,
                hkxbhd,
                hkx_entry_stem,
                dcx_type=DCXType[self.dcx_type],
                default_entry_path=self.default_entry_path,
                default_entry_flags=self.default_entry_flags,
                overwrite_existing=self.overwrite_existing,
            )
        except Exception as ex:
            traceback.print_exc()
            return self.error(f"Could not execute HKX export to Binder. Error: {ex}")

        if other_res:
            other_res_hkx_entry_stem = f"{other_res}{hkx_entry_stem[1:]}" if other_res else None
            try:
                export_hkx_to_binder(
                    self,
                    context,
                    other_res_bl_meshes,
                    other_res_hkxbhd,
                    other_res_hkx_entry_stem,
                    dcx_type=DCXType[self.dcx_type],
                    default_entry_path=self.default_entry_path,
                    default_entry_flags=self.default_entry_flags,
                    overwrite_existing=self.overwrite_existing,
                )
            except Exception as ex:
                traceback.print_exc()
                return self.error(f"Could not execute HKX export to Binder. Error: {ex}")

        try:
            hkxbhd.write()
        except Exception as ex:
            traceback.print_exc()
            return self.error(f"Could not write Binder to '{hkx_binder_path}'. Error: {ex}")

        if other_res_hkxbhd:
            try:
                other_res_hkxbhd.write()
            except Exception as ex:
                traceback.print_exc()
                return self.error(f"Could not write Binder to '{hkx_binder_path}'. Error: {ex}")

        return {"FINISHED"}


class ExportHKXMapCollisionIntoHKXBHD(LoggingOperator):
    """Export a HKX collision file into a FromSoftware DSR map directory BHD."""
    bl_idname = "export_scene_map.hkx_map_collision_entry"
    bl_label = "Export Map Collision"
    bl_description = (
        "Export HKX map collisions into HKXBHD binder in appropriate game map (DS1R only)"
    )

    @classmethod
    def poll(cls, context):
        """Must select empty parents of only (and at least one) child meshes.

        TODO: Also currently for DS1R only.
        """
        settings = cls.settings(context)
        if not settings.can_auto_export:
            return False
        if not settings.is_game(DARK_SOULS_DSR):
            return False
        if not context.selected_objects:
            return False
        for obj in context.selected_objects:
            if obj.type != "EMPTY":
                return False
            if not obj.children:
                return False
            if not all(child.type == "MESH" for child in obj.children):
                return False
        return True

    def execute(self, context):
        if not self.poll(context):
            return self.error("Must select a parent of one or more collision submeshes.")

        settings = self.settings(context)
        settings.save_settings()

        export_kwargs = dict(
            operator=self,
            context=context,
            dcx_type=DCXType.DS1_DS2,  # DS1R (inside HKXBHD)
            default_entry_path="{map}\\{name}.hkx.dcx",  # DS1R
            default_entry_flags=0x2,
            overwrite_existing=True,
        )

        opened_hkxbhds = {"h": {}, "l": {}}  # type: dict[str, dict[Path, Binder]]  # keys are relative HKXBHD paths
        return_strings = set()

        for hkx_parent in context.selected_objects:

            res = hkx_parent.name[0]
            if res not in "hl":
                self.error(f"Selected object '{hkx_parent.name}' must start with 'h' or 'l' to export.")
                continue

            if settings.detect_map_from_parent:
                if hkx_parent.parent is None:
                    return self.error(
                        f"Object '{hkx_parent.name}' has no parent. Deselect 'Detect Map from Parent' to use single "
                        f"game map specified in Soulstruct plugin settings."
                    )
                map_stem = hkx_parent.parent.name.split(" ")[0]
                if not MAP_STEM_RE.match(map_stem):
                    return self.error(
                        f"Parent object '{hkx_parent.parent.name}' does not start with a valid map stem."
                    )
            else:
                map_stem = settings.map_stem

            # Guess HKX stem from first 10 characters of name of selected object if 'Model File Stem' is not set.
            # TODO: This assumes DS1 model stem formatting.
            hkx_entry_stem = hkx_parent.get("Model File Stem", hkx_parent.name[:10])

            if not LOOSE_HKX_COLLISION_NAME_RE.match(hkx_entry_stem):
                return self.error(
                    f"Selected object's model stem '{hkx_entry_stem}' does not match the required name pattern for "
                    f"a DS1 HKX collision parent object: 'h......A..' or 'l......A..'"
                )

            # If HKX name is standard, check that it matches the selected map stem and warn user if not.
            numeric_match = NUMERIC_HKX_COLLISION_NAME_RE.match(hkx_entry_stem)
            if numeric_match is None:
                self.warning(
                    f"Selected object model stem '{hkx_entry_stem}' does not match the standard name pattern for "
                    f"a DS1 HKX map collision model: 'h####B#A##' or 'l####B#A##'. Exporting anyway."
                )
            else:
                block, area = int(numeric_match.group(3)), int(numeric_match.group(4))
                expected_map_stem = f"m{area:02d}_{block:02d}_00_00"
                if expected_map_stem != map_stem:
                    self.warning(
                        f"Map area and/or block in name of selected object model stem '{hkx_entry_stem}' does not "
                        f"match the export destination map '{map_stem}'. Exporting anyway."
                    )

            try:
                bl_meshes, other_res_bl_meshes, other_res = get_mesh_children(self, hkx_parent, True)
            except HKXMapCollisionExportError as ex:
                raise HKXMapCollisionExportError(f"Children of object '{hkx_parent}' cannot be exported. Error: {ex}")

            if res == "h":
                res_meshes = {
                    "h": bl_meshes,
                    "l": other_res_bl_meshes,
                }
            else:
                # Swap res and meshes.
                res_meshes = {
                    "h": other_res_bl_meshes,
                    "l": bl_meshes,
                }

            for r in "hl":
                meshes = res_meshes[r]
                if not meshes:
                    continue
                opened_res_hkxbhds = opened_hkxbhds[r]
                relative_hkxbhd_path = Path(f"map/{map_stem}/{r}{map_stem[1:]}.hkxbhd")  # no DCX
                if relative_hkxbhd_path not in opened_res_hkxbhds:
                    try:
                        hkxbhd_path = settings.prepare_project_file(relative_hkxbhd_path, False, must_exist=True)
                    except FileNotFoundError as ex:
                        return self.error(
                            f"Could not find HKXBHD file '{relative_hkxbhd_path}' for map '{map_stem}'. Error: {ex}"
                        )

                    relative_hkxbdt_path = Path(f"map/{map_stem}/{r}{map_stem[1:]}.hkxbdt")  # no DCX
                    try:
                        settings.prepare_project_file(relative_hkxbdt_path, False, must_exist=True)  # path not needed
                    except FileNotFoundError as ex:
                        return self.error(
                            f"Could not find HKXBDT file '{relative_hkxbdt_path}' for map '{map_stem}'. Error: {ex}"
                        )

                    opened_res_hkxbhds[relative_hkxbhd_path] = Binder.from_path(hkxbhd_path)

                hkxbhd = opened_res_hkxbhds[relative_hkxbhd_path]

                try:
                    export_hkx_to_binder(
                        bl_meshes=meshes,
                        hkxbhd=hkxbhd,
                        hkx_entry_stem=hkx_entry_stem,
                        map_stem=map_stem,
                        **export_kwargs,
                    )
                except Exception as ex:
                    traceback.print_exc()
                    self.error(f"Could not execute HKX export to Binder. Error: {ex}")

        for opened_res_hkxbhds in opened_hkxbhds.values():
            for relative_hkxbhd_path, hkxbhd in opened_res_hkxbhds.items():
                return_strings |= settings.export_file(self, hkxbhd, relative_hkxbhd_path)

        return {"FINISHED"} if "FINISHED" in return_strings else {"CANCELLED"}  # at least one success


class ExportMSBMapCollision(LoggingOperator):
    """Export a HKX collision file into a FromSoftware DSR map directory BHD."""
    bl_idname = "export_scene_map.msb_hkx_map_collision"
    bl_label = "Export Map Collision"
    bl_description = (
        "Export transform and model of HKX map collisions into MSB and HKXBHD binder in appropriate game map (DS1R)"
    )

    prefer_new_model_file_stem: BoolProperty(
        name="Prefer New Model File Stem",
        description="Use the 'Model File Stem' property on the Blender mesh parent to update the model file stem in "
                    "the MSB and determine the HKX entry stem to write. If disabled, the MSB model name will be used.",
        default=True,
    )

    @classmethod
    def poll(cls, context):
        """Must select empty parents of only (and at least one) child meshes.

        TODO: Also currently for DS1R only.
        """
        settings = cls.settings(context)
        if not settings.can_auto_export:
            return False
        if not settings.is_game(DARK_SOULS_DSR):
            return False
        if not context.selected_objects:
            return False
        for obj in context.selected_objects:
            if obj.type != "EMPTY":
                return False
            if not obj.children:
                return False
            if not all(child.type == "MESH" for child in obj.children):
                return False
        return True

    def execute(self, context):
        if not self.poll(context):
            return self.error("Must select a parent of one or more collision submeshes.")

        settings = self.settings(context)
        settings.save_settings()

        export_kwargs = dict(
            operator=self,
            context=context,
            dcx_type=DCXType.DS1_DS2,  # DS1R (inside HKXBHD)
            default_entry_path="{map}\\{name}.hkx.dcx",  # DS1R
            default_entry_flags=0x2,
            overwrite_existing=True,
        )

        opened_msbs = {}  # type: dict[Path, MSB_TYPING]  # keys are relative MSB paths
        opened_hkxbhds = {"h": {}, "l": {}}  # type: dict[str, dict[Path, Binder]]  # keys are relative HKXBHD paths
        edited_part_names = {}  # type: dict[str, set[str]]  # keys are MSB stems (which may differ from 'map' stems)
        return_strings = set()

        for hkx_parent in context.selected_objects:

            res = hkx_parent.name[0]
            if res not in "hl":
                self.error(f"Selected object '{hkx_parent.name}' must start with 'h' or 'l' to export.")
                continue

            if settings.detect_map_from_parent:
                if hkx_parent.parent is None:
                    return self.error(
                        f"Object '{hkx_parent.name}' has no parent. Deselect 'Detect Map from Parent' to use single "
                        f"game map specified in Soulstruct plugin settings."
                    )
                map_stem = hkx_parent.parent.name.split(" ")[0]
                if not MAP_STEM_RE.match(map_stem):
                    return self.error(
                        f"Parent object '{hkx_parent.parent.name}' does not start with a valid map stem."
                    )
                # Get oldest version of map for HKXBHD file.
                map_stem = settings.get_oldest_map_stem_version(map_stem)
            else:
                # Get oldest version of map for HKXBHD file.
                map_stem = settings.get_oldest_map_stem_version()

            relative_msb_path = settings.get_relative_msb_path(map_stem)  # will use latest map version
            msb_stem = relative_msb_path.stem

            # Get model file stem from MSB (must contain matching part).
            collision_part_name = get_bl_obj_stem(hkx_parent)
            if relative_msb_path not in opened_msbs:
                # Open new MSB.
                try:
                    msb_path = settings.prepare_project_file(relative_msb_path, False, must_exist=True)
                except FileNotFoundError as ex:
                    self.error(
                        f"Could not find MSB file '{relative_msb_path}' for map '{map_stem}'. Error: {ex}"
                    )
                    continue
                opened_msbs[relative_msb_path] = get_cached_file(msb_path, settings.get_game_msb_class())

            msb = opened_msbs[relative_msb_path]  # type: MSB_TYPING

            try:
                msb_part = msb.collisions.find_entry_name(collision_part_name)
            except KeyError:
                self.error(
                    f"Collision part '{collision_part_name}' not found in MSB {msb_stem} for map {map_stem}."
                )
                continue
            if not msb_part.model.name:
                self.error(
                    f"Collision part '{collision_part_name}' in MSB {msb_stem} for map {map_stem} has no model name."
                )
                continue

            hkx_entry_stem = hkx_parent.get("Model File Stem", None) if self.prefer_new_model_file_stem else None
            if not hkx_entry_stem:  # could be None or empty string
                # Use existing MSB model name.
                hkx_entry_stem = msb_part.model.get_model_file_stem(map_stem)
            else:
                # Update MSB model name.
                msb_part.model.set_name_from_model_file_stem(hkx_entry_stem)

            edited_msb_part_names = edited_part_names.setdefault(msb_stem, set())
            if collision_part_name in edited_msb_part_names:
                self.warning(
                    f"Navmesh part '{collision_part_name}' was exported more than once in selected meshes."
                )
            edited_msb_part_names.add(collision_part_name)

            # Warn if HKX stem in MSB is unexpected. (Only reachable if `prefer_new_model_file_stem = False`.)
            if (model_file_stem := hkx_parent.get("Model File Stem", None)) is not None:
                if model_file_stem != hkx_entry_stem:
                    self.warning(
                        f"Collision part '{hkx_entry_stem}' in MSB {msb_stem} for map {map_stem} has model name "
                        f"'{msb_part.model.name}' but Blender mesh 'Model File Stem' is '{model_file_stem}'. "
                        f"Prioritizing HKX stem from MSB model name; you may want to update the Blender mesh."
                    )

            # Update part transform in MSB.
            bl_transform = BlenderTransform.from_bl_obj(hkx_parent)
            msb_part.translate = bl_transform.game_translate
            msb_part.rotate = bl_transform.game_rotate_deg
            msb_part.scale = bl_transform.game_scale

            try:
                bl_meshes, other_res_bl_meshes, other_res = get_mesh_children(self, hkx_parent, True)
            except HKXMapCollisionExportError as ex:
                self.error(f"Children of object '{hkx_parent}' cannot be exported. Error: {ex}")
                continue

            if res == "h":
                res_meshes = {
                    "h": bl_meshes,
                    "l": other_res_bl_meshes,
                }
            else:
                # Swap res and meshes.
                res_meshes = {
                    "h": other_res_bl_meshes,
                    "l": bl_meshes,
                }

            for r in "hl":
                meshes = res_meshes[r]
                if not meshes:
                    continue
                opened_res_hkxbhds = opened_hkxbhds[r]
                relative_hkxbhd_path = Path(f"map/{map_stem}/{r}{map_stem[1:]}.hkxbhd")  # no DCX
                if relative_hkxbhd_path not in opened_res_hkxbhds:
                    try:
                        hkxbhd_path = settings.prepare_project_file(relative_hkxbhd_path, False, must_exist=True)
                    except FileNotFoundError as ex:
                        return self.error(
                            f"Could not find HKXBHD file '{relative_hkxbhd_path}' for map '{map_stem}'. Error: {ex}"
                        )

                    relative_hkxbdt_path = Path(f"map/{map_stem}/{r}{map_stem[1:]}.hkxbdt")  # no DCX
                    try:
                        settings.prepare_project_file(relative_hkxbdt_path, False, must_exist=True)  # path not needed
                    except FileNotFoundError as ex:
                        return self.error(
                            f"Could not find HKXBDT file '{relative_hkxbdt_path}' for map '{map_stem}'. Error: {ex}"
                        )

                    opened_res_hkxbhds[relative_hkxbhd_path] = Binder.from_path(hkxbhd_path)

                hkxbhd = opened_res_hkxbhds[relative_hkxbhd_path]

                try:
                    export_hkx_to_binder(
                        bl_meshes=meshes,
                        hkxbhd=hkxbhd,
                        hkx_entry_stem=hkx_entry_stem,
                        map_stem=map_stem,
                        **export_kwargs,
                    )
                except Exception as ex:
                    traceback.print_exc()
                    self.error(f"Could not execute HKX export to Binder. Error: {ex}")

        for opened_res_hkxbhds in opened_hkxbhds.values():
            for relative_hkxbhd_path, hkxbhd in opened_res_hkxbhds.items():
                return_strings |= settings.export_file(self, hkxbhd, relative_hkxbhd_path)

        for relative_msb_path, msb in opened_msbs.items():
            settings.export_file(self, msb, relative_msb_path)

        return {"FINISHED"} if "FINISHED" in return_strings else {"CANCELLED"}  # at least one success


def load_hkxbhds(hkxbhd_path: Path, other_res: str = "") -> tuple[Binder, Binder | None]:
    """Load the HKXBHD file at `hkxbhd_path` and the other resolution HKXBHD file (if `other_res` is given)."""

    try:
        hkxbhd = Binder.from_path(hkxbhd_path)
    except Exception as ex:
        raise HKXMapCollisionExportError(f"Could not load HKXBHD file '{hkxbhd_path}'. Error: {ex}")

    if not other_res:
        return hkxbhd, None

    other_res_hkxbhd = None  # type: Binder | None
    other_res_binder_name = f"{other_res}{hkxbhd_path.name[1:]}"
    other_res_binder_path = hkxbhd_path.with_name(other_res_binder_name)
    try:
        other_res_hkxbhd = Binder.from_path(other_res_binder_path)
    except Exception as ex:
        raise HKXMapCollisionExportError(
            f"Could not load HKXBHD file '{other_res_hkxbhd}' for other resolution. Error: {ex}"
        )
    return hkxbhd, other_res_hkxbhd


def find_binder_hkx_entry(
    operator: LoggingOperator,
    binder: Binder,
    hkx_entry_stem: str,
    default_entry_path: str,
    default_entry_flags: int,
    overwrite_existing: bool,
    map_stem="",
) -> BinderEntry:
    matching_entries = binder.find_entries_matching_name(rf"{hkx_entry_stem}\.hkx(\.dcx)?")

    if not matching_entries:
        # Create new entry.
        if "{map}" in default_entry_path:
            if not map_stem:
                if match := NUMERIC_HKX_COLLISION_NAME_RE.match(hkx_entry_stem):
                    block, area = int(match.group(3)), int(match.group(4))
                    map_stem = f"m{area:02d}_{block:02d}_00_00"
                else:
                    raise HKXMapCollisionExportError(
                        f"Could not determine '{{map}}' for new Binder entry from HKX name: {hkx_entry_stem}. It must "
                        f"be in the format '[hl]####A#B##' for map name 'mAA_BB_00_00' to be detected."
                    )
            entry_path = default_entry_path.format(map=map_stem, name=hkx_entry_stem)
        else:
            entry_path = default_entry_path.format(name=hkx_entry_stem)
        new_entry_id = binder.highest_entry_id + 1
        hkx_entry = BinderEntry(
            b"", entry_id=new_entry_id, path=entry_path, flags=default_entry_flags
        )
        binder.add_entry(hkx_entry)
        operator.info(f"Creating new Binder entry: ID {new_entry_id}, path '{entry_path}'")
        return hkx_entry

    if not overwrite_existing:
        raise HKXMapCollisionExportError(
            f"HKX named '{hkx_entry_stem}' already exists in Binder and overwrite is disabled."
        )

    entry = matching_entries[0]
    if len(matching_entries) > 1:
        operator.warning(
            f"Multiple HKXs named '{hkx_entry_stem}' found in Binder. Replacing first: {entry.name}"
        )
    else:
        operator.info(f"Replacing existing Binder entry: ID {entry.id}, path '{entry.path}'")
    return matching_entries[0]


def export_hkx_to_binder(
    operator: LoggingOperator,
    context: bpy.types.Context,
    bl_meshes: list[bpy.types.MeshObject],
    hkxbhd: Binder,
    hkx_entry_stem: str,
    dcx_type: DCXType,
    default_entry_path: str,
    default_entry_flags: int,
    overwrite_existing: bool,
    map_stem="",
):
    # Find Binder entry.
    try:
        hkx_entry = find_binder_hkx_entry(
            operator,
            hkxbhd,
            hkx_entry_stem,
            default_entry_path,
            default_entry_flags,
            overwrite_existing,
            map_stem,
        )
    except Exception as ex:
        raise HKXMapCollisionExportError(f"Cannot find or create Binder entry for '{hkx_entry_stem}'. Error: {ex}")

    # TODO: Not needed for meshes only?
    if bpy.ops.object.mode_set.poll():
        bpy.ops.object.mode_set(mode="OBJECT", toggle=False)

    exporter = HKXMapCollisionExporter(operator, context)

    try:
        hkx = exporter.export_hkx_map_collision(bl_meshes, name=hkx_entry_stem)
    except Exception as ex:
        raise HKXMapCollisionExportError(f"Cannot get exported HKX for '{hkx_entry_stem}'. Error: {ex}")
    hkx.dcx_type = dcx_type

    try:
        hkx_entry.set_from_binary_file(hkx)
    except Exception as ex:
        raise HKXMapCollisionExportError(f"Cannot pack exported HKX '{hkx_entry_stem}' into Binder entry. Error: {ex}")


class HKXMapCollisionExporter:
    operator: LoggingOperator

    def __init__(self, operator: LoggingOperator, context):
        self.operator = operator
        self.context = context

    def warning(self, msg: str):
        self.operator.report({"WARNING"}, msg)
        print(f"# WARNING: {msg}")

    @staticmethod
    def export_hkx_map_collision(bl_meshes, name: str) -> MapCollisionHKX:
        """Create HKX from Blender meshes (subparts).

        `name` is needed to set internally to the HKX file (though it probably doesn't impact gameplay).

        TODO: Currently only supported for DS1R and Havok 2015.
        """
        if not bl_meshes:
            raise ValueError("No meshes given to export to HKX.")

        hkx_meshes = []  # type: list[tuple[np.ndarray, np.ndarray]]
        hkx_material_indices = []  # type: list[int]

        for bl_mesh in bl_meshes:

            if bl_mesh.get("Material Index", None) is None and bl_mesh.get("material_index", None) is not None:
                # NOTE: Legacy code for previous name of this property. TODO: Remove after a few releases.
                material_index = get_bl_prop(bl_mesh, "material_index", int, default=0)
                # Move property to new name.
                bl_mesh["Material Index"] = material_index
                del bl_mesh["material_index"]
            else:
                material_index = get_bl_prop(bl_mesh, "Material Index", int, default=0)
            hkx_material_indices.append(material_index)

            # Swap Y and Z coordinates.
            hkx_verts_list = [[vert.co.x, vert.co.z, vert.co.y] for vert in bl_mesh.data.vertices]
            hkx_verts = np.array(hkx_verts_list, dtype=np.float32)
            hkx_faces = np.empty((len(bl_mesh.data.polygons), 3), dtype=np.uint32)
            for i, face in enumerate(bl_mesh.data.polygons):
                if len(face.vertices) != 3:
                    raise ValueError(
                        f"Found a non-triangular mesh face in HKX (index {i}). Mesh must be triangulated first."
                    )
                hkx_faces[i] = face.vertices

            hkx_meshes.append((hkx_verts, hkx_faces))

        hkx = MapCollisionHKX.from_meshes(
            meshes=hkx_meshes,
            hkx_name=name,
            material_indices=hkx_material_indices,
            # Bundled template HKX serves fine.
            # DCX applied by caller.
        )

        return hkx

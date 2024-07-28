from __future__ import annotations

__all__ = [
    "BaseImportSingleMSBPart",
    "BaseImportAllMSBParts",
    "BaseExportMSBParts",
]

import abc
import time
import traceback
import typing as tp
from pathlib import Path

import bpy
from io_soulstruct.general import SoulstructSettings
from io_soulstruct.general.cached import get_cached_file
from io_soulstruct.msb.operator_config import MSBPartOperatorConfig
from io_soulstruct.types import SoulstructType
from io_soulstruct.utilities.misc import *
from io_soulstruct.utilities.operators import LoggingOperator

if tp.TYPE_CHECKING:
    from io_soulstruct.type_checking import MSB_TYPING
    from soulstruct.base.maps.msb import MSBEntryList


class BaseImportSingleMSBPart(LoggingOperator):

    config: tp.ClassVar[MSBPartOperatorConfig]

    @classmethod
    def poll(cls, context):
        settings = cls.settings(context)

        try:
            cls.config.get_bl_part_type(settings.game)
        except KeyError:
            return False

        msb_path = settings.get_import_msb_path()
        if not is_path_and_file(msb_path):
            return False
        part = getattr(context.scene.soulstruct_game_enums, cls.config.GAME_ENUM_NAME)
        if part in {"", "0"}:
            return False  # no enum option selected
        return True  # MSB exists and a Character part name is selected from enum

    def execute(self, context: bpy.types.Context):
        """Import MSB Part of this subclass's subtype from value of `config.GAME_ENUM_NAME` Blender enum property."""

        settings = self.settings(context)
        msb_import_settings = context.scene.msb_import_settings

        try:
            bl_part_type = self.config.get_bl_part_type(settings.game)
        except KeyError:
            return self.error(
                f"Cannot import MSB Part subtype `{self.config.PART_SUBTYPE}` for game {settings.game.name}."
            )

        part_name = getattr(context.scene.soulstruct_game_enums, self.config.GAME_ENUM_NAME)
        if part_name in {"", "0"}:
            return self.error(f"Invalid MSB {self.config.PART_SUBTYPE} selection: {part_name}")

        if not settings.get_import_map_path():  # validation
            return self.error("Game directory and map stem must be set in Blender's Soulstruct global settings.")

        # We always use the latest MSB, if the setting is enabled.
        msb_stem = settings.get_latest_map_stem_version()
        map_stem = settings.get_oldest_map_stem_version() if not self.config.USE_LATEST_MAP_FOLDER else msb_stem
        msb_path = settings.get_import_msb_path()  # will automatically use latest MSB version if known and enabled
        msb = get_cached_file(msb_path, settings.get_game_msb_class())  # type: MSB_TYPING
        collection_name = msb_import_settings.get_collection_name(msb_stem, self.config.collection_name)
        part_collection = get_collection(collection_name, context.scene.collection)

        # Get MSB part.
        part_list = getattr(msb, self.config.MSB_LIST_NAME)
        try:
            part = part_list.find_entry_name(part_name)
        except KeyError:
            return self.error(f"MSB {self.config.PART_SUBTYPE} '{part_name}' not found in MSB.")

        try:
            # NOTE: Instance creator may not always use `map_stem` (e.g. characters).
            bl_part = bl_part_type.new_from_soulstruct_obj(
                self, context, part, part_name, part_collection, map_stem)
        except Exception as ex:
            traceback.print_exc()
            return self.error(f"Failed to import MSB {self.config.PART_SUBTYPE} part '{part.name}': {ex}")

        # Select and frame view on new instance.
        self.set_active_obj(bl_part.obj)
        bpy.ops.view3d.view_selected(use_all_regions=False)

        return {"FINISHED"}


class BaseImportAllMSBParts(LoggingOperator):

    config: tp.ClassVar[MSBPartOperatorConfig]

    @classmethod
    def poll(cls, context):
        settings = cls.settings(context)
        msb_path = settings.get_import_msb_path()

        try:
            cls.config.get_bl_part_type(settings.game)
        except KeyError:
            return False

        if not is_path_and_file(msb_path):
            return False
        return True  # MSB exists and a Character part name is selected from enum

    def invoke(self, context, event):
        """Ask user for confirmation before importing all parts, which can take a long time."""
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):

        start_time = time.perf_counter()

        settings = self.settings(context)
        try:
            bl_part_type = self.config.get_bl_part_type(settings.game)
        except KeyError:
            return self.error(
                f"Cannot import MSB Part subtype `{self.config.PART_SUBTYPE}` for game {settings.game.name}."
            )

        if not settings.get_import_map_path():  # validation
            return self.error("Game directory and map stem must be set in Blender's Soulstruct global settings.")

        msb_import_settings = context.scene.msb_import_settings
        is_name_match = msb_import_settings.get_name_match_filter()
        msb_stem = settings.get_latest_map_stem_version()
        map_stem = settings.get_oldest_map_stem_version() if not self.config.USE_LATEST_MAP_FOLDER else msb_stem
        msb_path = settings.get_import_msb_path()  # will automatically use latest MSB version if known and enabled
        msb = get_cached_file(msb_path, settings.get_game_msb_class())  # type: MSB_TYPING
        collection_name = msb_import_settings.get_collection_name(msb_stem, self.config.collection_name)
        part_collection = get_collection(collection_name, context.scene.collection)

        part_list = getattr(msb, self.config.MSB_LIST_NAME)
        part_count = 0

        for part in [part for part in part_list if is_name_match(part.name)]:
            try:
                # No need to return instance.
                bl_part_type.new_from_soulstruct_obj(
                    self, context, part, part.name, part_collection, map_stem
                )
            except Exception as ex:
                traceback.print_exc()
                self.error(f"Failed to import MSB {self.config.PART_SUBTYPE} part '{part.name}': {ex}")
                continue

            part_count += 1

        if part_count == 0:
            self.warning(
                f"No MSB {self.config.PART_SUBTYPE} parts found with {msb_import_settings.entry_name_match_mode} "
                f"filter: '{msb_import_settings.entry_name_match}'"
            )
            return {"CANCELLED"}

        self.info(
            f"Imported {part_count} / {len(part_list)} MSB {self.config.PART_SUBTYPE} parts in "
            f"{time.perf_counter() - start_time:.3f} seconds (filter: '{msb_import_settings.entry_name_match}')."
        )

        # No change in view after importing all parts.

        return {"FINISHED"}


class BaseExportMSBParts(LoggingOperator):

    config: tp.ClassVar[MSBPartOperatorConfig]

    @classmethod
    def poll(cls, context):
        if not context.selected_objects:
            return False
        for obj in context.selected_objects:
            if (
                obj.soulstruct_type != SoulstructType.MSB_PART
                or obj.MSB_PART.part_subtype != cls.config.PART_SUBTYPE
            ):
                return False
        return True

    @abc.abstractmethod
    def init(self, context: bpy.types.Context, settings: SoulstructSettings):
        """Set up operator instance state."""

    @abc.abstractmethod
    def export_model(
        self,
        operator: LoggingOperator,
        context: bpy.types.Context,
        model_mesh: bpy.types.MeshObject,
        map_stem: str,  # not needed for all types
    ):
        """Export the model associated with the given MSB Part.

        It's up to the subclass to store this on some operator instance dictionary.

        Only available for geometry. Too annoying to replicate/duplicate all the other Parts, and you'd generally never
        want to casually modify Character/Object models while exporting an MSB Part.
        """

    @abc.abstractmethod
    def finish_model_export(self, context: bpy.types.Context, settings: SoulstructSettings):
        """Export all models prepared during `export_model()` calls."""

    def execute(self, context):
        settings = self.settings(context)
        self.init(context, settings)
        return_code = self.export_parts(context)
        if return_code == {"FINISHED"}:
            # TODO: Would be nice to know if this succeeds before exporting MSBs...
            return_code = self.finish_model_export(context, settings)
        return return_code

    def export_parts(self, context):
        settings = self.settings(context)

        if not settings.map_stem and not settings.detect_map_from_collection:
            return self.error(
                "No map selected in Soulstruct settings and `Detect Map from Collection` is disabled."
            )

        model_export_mode = context.scene.msb_export_settings.model_export_mode
        bl_part_type = self.config.get_bl_part_type(settings.game)

        self.to_object_mode()

        # Record active object to restore at end (may be None).
        active_object = context.active_object

        opened_msbs = {}  # type: dict[Path, MSB_TYPING]
        exported_part_names = {}  # type: dict[str, set[str]]  # keys are MSB stems (which may differ from 'map' stems)

        for obj in context.selected_objects:

            bl_part = bl_part_type(obj)

            bl_model = bl_part.subtype_properties.model
            if not bl_model:
                return self.error(f"MSB Part '{bl_part.name}' has no model in Blender. No parts exported.")
            model_stem = get_bl_obj_tight_name(bl_model)

            # We also use the detected map stem of the Part when exporting the Model, since the Model obviously needs to
            # be present in the same map (for relevant subtypes).
            msb_stem = settings.get_latest_map_stem_version()
            map_stem = settings.get_oldest_map_stem_version() if not self.config.USE_LATEST_MAP_FOLDER else msb_stem
            relative_msb_path = settings.get_relative_msb_path(msb_stem)  # will use latest MSB version

            if relative_msb_path not in opened_msbs:
                # Open new MSB. We start with the game MSB unless `Prefer Import from Project` is enabled.
                try:
                    msb_path = settings.prepare_project_file(relative_msb_path)
                except FileNotFoundError as ex:
                    self.error(
                        f"Could not find MSB file '{relative_msb_path}' for map '{map_stem}'. Error: {ex}"
                    )
                    continue
                opened_msbs[relative_msb_path] = get_cached_file(msb_path, settings.get_game_msb_class())

            msb = opened_msbs[relative_msb_path]  # type: MSB_TYPING

            part_name = get_bl_obj_tight_name(obj)
            map_exported_part_names = exported_part_names.setdefault(msb_stem, set())
            if part_name in map_exported_part_names:
                self.warning(
                    f"Map Piece part '{part_name}' was exported more than once to MSB {msb_stem}. Last one "
                    f"will overwrite previous ones."
                )
            map_exported_part_names.add(part_name)

            # NOTE: We don't delete the existing MSB part until the last moment, once we have the new Part ready to go
            # and the Model has successfully been written, if appropriate.

            msb_model = msb.map_piece_models.new()
            msb_model.set_name_from_model_file_stem(model_stem)
            msb_model.set_auto_sib_path(map_stem=map_stem)

            msb_model_lists_names = []
            for msb_model_list_name in self.config.MSB_MODEL_LIST_NAMES:
                msb_model_list = getattr(msb, msb_model_list_name)
                msb_model_lists_names += [model.name for model in msb_model_list]
            if msb_model.name not in msb_model_lists_names:
                # Add new model to MSB. Otherwise, existing one is fine.
                getattr(msb, self.config.MSB_MODEL_LIST_NAMES[0]).append(msb_model)

            if (
                self.config.PART_SUBTYPE.is_map_geometry()
                and (
                    model_export_mode == "ALWAYS_GEOMETRY"
                    or (
                        model_export_mode == "IF_NEW"
                        and msb_model.name not in msb.map_piece_models.get_entry_names()
                    ))
            ):
                try:
                    self.export_model(self, context, bl_model, map_stem)
                except Exception as ex:
                    traceback.print_exc()
                    return self.error(
                        f"Could not export model '{bl_model.name}' of part '{bl_part.name}'. Error: {ex}"
                    )

            # NOTE: `map_stem` passed for SIB path generation should be the oldest map stem, not the latest.
            msb_part = bl_part.to_entry(self, context, settings, map_stem, msb)
            msb_list = getattr(msb, self.config.MSB_LIST_NAME)  # type: MSBEntryList
            try:
                existing_msb_part = msb_list.find_entry_name(part_name)
            except KeyError:
                pass
            else:
                # We delete existing MSB Part with the same name. There's basically zero chance that you'd want to NOT
                # overwrite exported MSB entries many times, so I don't do any checking.
                msb_list.remove(existing_msb_part)
            msb_list.append(msb_part)

        # Write modified MSBs.
        for relative_msb_path, msb in opened_msbs.items():
            settings.export_file(self, msb, relative_msb_path)

        # Select original active object.
        if active_object:
            context.view_layer.objects.active = active_object

        return {"FINISHED"}

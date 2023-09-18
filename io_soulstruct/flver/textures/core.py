from __future__ import annotations

__all__ = [
    "ImportDDS",
    "ExportTexturesIntoBinder",
    "BakeLightmapSettings",
    "BakeLightmapTextures",
    "ExportLightmapTextures",
]

import tempfile
from pathlib import Path

import bpy
from bpy_extras.io_utils import ImportHelper

from soulstruct.base.textures.dds import texconv
from soulstruct.containers import BinderEntry, DCXType, TPF

from io_soulstruct.utilities import *
from io_soulstruct.flver.utilities import MTDInfo
from .utilities import *


class ImportDDS(LoggingOperator, ImportHelper):
    """Import a DDS file (as a PNG) into a selected `Image` node."""
    bl_idname = "import_image.dds"
    bl_label = "Import DDS"
    bl_description = (
        "Import a DDS texture or single-DDS TPF binder as a PNG image, and optionally set it to all selected Image "
        "Texture nodes (does not save the PNG)"
    )

    filename_ext = ".dds"

    filter_glob: bpy.props.StringProperty(
        default="*.dds;*.tpf;*.tpf.dcx",
        options={'HIDDEN'},
        maxlen=255,
    )

    set_to_selected_image_nodes: bpy.props.BoolProperty(
        name="Set to Selected Image Node(s)",
        description="Set loaded PNG texture to any selected Image nodes",
        default=True,
    )

    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={'HIDDEN', 'SKIP_SAVE'},
    )

    directory: bpy.props.StringProperty(
        options={'HIDDEN'},
    )

    # TODO: Option to replace Blender Image with same name, if present.

    def execute(self, context):
        print("Executing DDS import...")

        file_paths = [Path(self.directory, file.name) for file in self.files]

        for file_path in file_paths:

            if TPF_RE.match(file_path.name):
                tpf = TPF.from_path(file_path)
                if len(tpf.textures) > 1:
                    # TODO: Could load all and assign to multiple selected Image Texture nodes if names match?
                    return self.error(f"Cannot import TPF file containing more than one texture: {file_path}")

                bl_image = load_tpf_texture_as_png(tpf.textures[0])
                self.info(f"Loaded DDS file from TPF as PNG: {file_path.name}")
            else:
                # Loose DDS file.
                with tempfile.TemporaryDirectory() as png_dir:
                    temp_dds_path = Path(png_dir, file_path.name)
                    temp_dds_path.write_bytes(file_path.read_bytes())  # copy
                    texconv_result = texconv("-o", png_dir, "-ft", "png", "-f", "RGBA", temp_dds_path)
                    png_path = Path(png_dir, file_path.with_suffix(".png"))
                    if png_path.is_file():
                        bl_image = bpy.data.images.load(str(png_path))
                        bl_image.pack()  # embed PNG in `.blend` file
                        self.info(f"Loaded DDS file as PNG: {file_path.name}")
                    else:
                        stdout = "\n    ".join(texconv_result.stdout.decode().split("\r\n")[3:])  # drop copyright lines
                        return self.error(f"Could not convert texture DDS to PNG:\n    {stdout}")

            # TODO: Option to iterate over ALL materials in .blend and replace a given named image with this new one.
            if self.set_to_selected_image_nodes:
                try:
                    material_nt = context.active_object.active_material.node_tree
                except AttributeError:
                    self.warning("No active object/material node tree detected to set texture to.")
                else:
                    sel = [node for node in material_nt.nodes if node.select and node.bl_idname == "ShaderNodeTexImage"]
                    if not sel:
                        self.warning("No selected Image Texture nodes to set texture to.")
                    else:
                        for image_node in sel:
                            image_node.image = bl_image
                            self.info("Set imported DDS (PNG) texture to Image Texture node.")

        return {"FINISHED"}


class ExportTexturesIntoBinder(LoggingOperator, ImportHelper):
    bl_idname = "export_image.texture_binder"
    bl_label = "Export Textures Into Binder"
    bl_description = (
        "Export image textures from selected Image Texture node(s) into a FromSoftware TPF or TPF-containing Binder "
        "(BND/BHD)"
    )

    # ImportHelper mixin class uses this
    filename_ext = ".chrbnd"

    filter_glob: bpy.props.StringProperty(
        default="*.tpf;*.tpf.dcx;*.tpfbhd;*.chrbnd;*.chrbnd.dcx;*.objbnd;*.objbnd.dcx;*.partsbnd;*.partsbnd.dcx",
        options={'HIDDEN'},
        maxlen=255,
    )

    new_texture_stem: bpy.props.StringProperty(
        name="New Texture Name",
        description="Name (without extensions) of texture to be exported (defaults to Blender image name)",
        default="",
    )

    texture_stem_to_replace: bpy.props.StringProperty(
        name="Texture Name to Replace",
        description="Name (without extensions) of texture in binder to replace (defaults to 'New Texture Name' above)",
        default="",
    )

    rename_tpf_containers: bpy.props.BoolProperty(
        name="Rename TPF Containers",
        description="Also change name of any TPF wrappers if they match 'Texture Name to Replace'",
        default=True,
    )

    new_mipmap_count: bpy.props.IntProperty(
        name="New Mipmap Count",
        description="Number of mipmaps to generate for each texture (-1 = same as replaced file, 0 = all mipmaps)",
        default=-1,
        min=-1,
    )

    files: bpy.props.CollectionProperty(
        type=bpy.types.OperatorFileListElement,
        options={'HIDDEN', 'SKIP_SAVE'},
    )

    directory: bpy.props.StringProperty(
        options={'HIDDEN'},
    )

    # TODO: Option: if texture name is changed, update that texture path in FLVER (in Binder and custom Blender prop).

    @classmethod
    def poll(cls, context):
        try:
            # TODO: What if you just want to export an image from the Image Viewer? Different operator?
            sel_tex_nodes = [
                node for node in context.active_object.active_material.node_tree.nodes
                if node.select and node.bl_idname == "ShaderNodeTexImage"
            ]
            return bool(sel_tex_nodes)
        except (AttributeError, IndexError):
            return False

    def execute(self, context):
        self.info("Executing texture export...")

        # TODO: Should this operator really support export to multiple binders simultaneously (and they'd have to be in
        #  the same folder)?
        file_paths = [Path(self.directory, file.name) for file in self.files]

        sel_tex_nodes = [
            node for node in context.active_object.active_material.node_tree.nodes
            if node.select and node.bl_idname == "ShaderNodeTexImage"
        ]
        if not sel_tex_nodes:
            return self.error("No Image Texture material node selected.")
        if len(sel_tex_nodes) > 1 and self.replace_texture_name:
            return self.error("Cannot override 'Replace Texture Name' when exporting multiple textures.")

        texture_export_infos = {}  # type: dict[Path, TextureExportInfo]
        for file_path in file_paths:
            try:
                texture_export_infos[file_path] = get_texture_export_info(str(file_path))
            except Exception as ex:
                return self.error(str(ex))

        replaced_texture_export_info = None
        for tex_node in sel_tex_nodes:
            bl_image = tex_node.image
            if not bl_image:
                self.warning("Ignoring Image Texture node with no image assigned.")
                continue

            image_stem = Path(bl_image.name).stem
            new_texture_stem = self.new_texture_stem if self.new_texture_stem else image_stem

            if self.texture_stem_to_replace:  # will only be allowed if one Image Texture is being exported
                texture_name_to_replace = Path(self.texture_stem_to_replace).stem
            else:
                texture_name_to_replace = new_texture_stem

            for file_path, texture_export_info in texture_export_infos.items():
                image_exported, dds_format = texture_export_info.inject_texture(
                    bl_image,
                    new_name=new_texture_stem,
                    name_to_replace=texture_name_to_replace,
                    rename_tpf=self.rename_tpf_containers,
                )
                if image_exported:
                    print(f"Replacing texture name '{texture_name_to_replace}' with new texture '{new_texture_stem}'")
                    self.info(
                        f"Exported '{bl_image.name}' into '{self.filepath}', replacing texture "
                        f"'{texture_name_to_replace}', with name '{new_texture_stem}' and DDS format {dds_format}."
                    )
                    replaced_texture_export_info = texture_export_info
                    break  # do not search other file paths
                else:
                    self.info(
                        f"Could not find any TPF textures to replace with Blender image "
                        f"in {file_path.name}: '{image_stem}'"
                    )
            else:
                self.warning(
                    f"Could not find any TPF textures to replace with Blender image in ANY files: '{image_stem}'"
                )

        if replaced_texture_export_info:
            # TPFs have all been updated. Now pack modified ones back to their Binders.
            try:
                write_msg = replaced_texture_export_info.write_files()
            except Exception as ex:
                return self.error(str(ex))
            self.info(write_msg)
        return {"FINISHED"}


class BakeLightmapSettings(bpy.types.PropertyGroup):

    bake_image_name: bpy.props.StringProperty(
        name="Bake Image Name",
        description="Name of image texture to bake into (leave empty to use single existing 'g_Lightmap' texture of "
                    "selected meshes)",
        default="",
    )

    bake_image_size: bpy.props.IntProperty(
        name="Bake Image Size",
        description="Size of image texture to bake into (leave at 0 to use existing texture size)",
        default=512,  # NOTE: twice the original DS1 lightmap size of 256x256
    )

    bake_device: bpy.props.EnumProperty(
        name="Bake Device",
        items=[
            ("CPU", "CPU", "Use Cycles engine with CPU"),
            ("GPU", "GPU", "Use Cycles engine with GPU"),
        ],
        default="GPU",
        description="Device (CPU/GPU) to use for Cycles bake render"
    )

    bake_samples: bpy.props.IntProperty(
        name="Bake Samples",
        description="Number of Cycles render samples to use while baking",
        default=128,
    )

    bake_margin: bpy.props.IntProperty(
        name="Bake Margin",
        description="Texture margin (in pixels) for 'UV islands' in bake",
        default=0,
    )

    bake_edge_shaders: bpy.props.BoolProperty(
        name="Bake Edge Shaders",
        description="If disabled, meshes with 'Edge'-type materials (e.g. decals) will not affect the bake render (but "
                    "may still use them in their materials)",
        default=False,
    )

    bake_rendered_only: bpy.props.BoolProperty(
        name="Bake Render-Visible Only",
        description="If enabled, only meshes with render visibility (camera icon) will affect the bake render",
        default=False,
    )

    # TODO: Option to auto-export lightmap as DDS into the appropriate map's TPFBHD binder.


class BakeLightmapTextures(LoggingOperator):

    bl_idname = "bake.lightmaps"
    bl_label = "Bake FLVER Lightmaps"
    bl_description = "Bake rendered lightmap image textures from all materials of all selected FLVERs"

    @classmethod
    def poll(cls, context):
        """FLVER meshes must be selected."""
        return context.selected_objects and all(obj.type == "MESH" for obj in context.selected_objects)

    def execute(self, context):
        self.info("Baking FLVER lightmap textures...")

        bake_settings = context.scene.bake_lightmap_settings  # type: BakeLightmapSettings

        # Get all selected FLVER meshes.
        flver_meshes = []
        for flver_mesh in context.selected_objects:
            if flver_mesh.type != "MESH":
                return self.error(f"Selected object '{flver_mesh.name}' is not a mesh.")
            flver_meshes.append(flver_mesh)

        # Set up variables/function to restore original state after bake.
        original_lightmap_strengths = []  # pairs of `(node, value)`
        render_settings = {}

        def restore_originals():
            # NOTE: Does NOT restore old active UV layer, as you most likely want to immediately see the bake result!
            for _node, _strength in original_lightmap_strengths:
                _node.inputs["Fac"].default_value = _strength
            # Restore render settings.
            if "engine" in render_settings:
                bpy.context.scene.render.engine = render_settings["engine"]
            if bpy.context.scene.render.engine == "CYCLES":
                if "device" in render_settings:
                    bpy.context.scene.cycles.device = render_settings["device"]
                if "samples" in render_settings:
                    bpy.context.scene.cycles.samples = render_settings["samples"]

        self.cleanup_callback = restore_originals

        # Find all 'g_Lightmap' texture nodes on all materials of all selected meshes.
        lightmap_texture_name = None  # type: str | None
        meshes_to_bake = []

        for mesh in flver_meshes:
            mesh: bpy.types.MeshObject
            for material_slot in mesh.material_slots:
                bl_material = material_slot.material
                try:
                    mtd_name = Path(bl_material["MTD Path"]).name
                except KeyError:
                    return self.error(f"Material '{bl_material.name}' of mesh {mesh.name} has no 'MTD Path' property.")
                mtd_info = MTDInfo.from_mtd_name(mtd_name)

                try:
                    lightmap_node = bl_material.node_tree.nodes["g_Lightmap"]
                except KeyError:
                    continue

                if lightmap_texture_name is not None and lightmap_node.image.name != lightmap_texture_name:
                    return self.error(
                        f"Found multiple 'g_Lightmap' texture nodes across materials of selected meshes. All of them "
                        f"must use the same lightmap texture."
                    )
                lightmap_texture_name = lightmap_node.image.name

                # Activate lightmap texture for bake target of this mesh/material.
                bl_material.node_tree.nodes.active = lightmap_node
                lightmap_node.select = True

                # Activate UV layer used by lightmap.
                try:
                    uv_name = lightmap_node.inputs["Vector"].links[0].from_node.attribute_name
                except (AttributeError, IndexError):
                    return self.error(
                        f"Could not find UVMap attribute for 'g_Lightmap' texture node '{lightmap_node.name}' in "
                        f"material '{bl_material.name}' of mesh {mesh.name}."
                    )
                mesh.data.uv_layers[uv_name].active = True

                # Set overlay values to zero while baking - we don't want the existing lightmap to affect the bake!
                try:
                    overlay_node = lightmap_node.outputs["Color"].links[0].to_node
                    overlay_node_fac = overlay_node.inputs["Fac"]
                except (AttributeError, IndexError):
                    self.warning(
                        f"Could not find `MixRGB` node connected to output of 'g_Lightmap' node '{lightmap_node.name}' "
                        f"in material '{bl_material.name}' of mesh {mesh.name}."
                    )
                else:
                    original_lightmap_strengths.append((overlay_node, overlay_node_fac.default_value))
                    # Detect which mix slot lightmap is using and set factor to disable lightmap while baking.
                    if overlay_node.inputs[1].links[0].from_node == lightmap_node:  # input 1
                        overlay_node_fac.default_value = 1.0
                    else:  # input 2 (expected)
                        overlay_node_fac.default_value = 0.0

                if bake_settings.bake_rendered_only and mesh.hide_render:
                    self.info(
                        f"Bake will NOT include material '{bl_material.name}' of mesh {mesh.name} that "
                        f"is hidden from rendering."
                    )
                if mtd_info.is_water:
                    self.info(
                        f"Bake will NOT include material '{bl_material.name}' of mesh {mesh.name} with "
                        f"water MTD shader: {mtd_name}"
                    )
                elif not bake_settings.bake_edge_shaders and mtd_info.edge:
                    self.info(
                        f"Bake will NOT include material '{bl_material.name}' of mesh {mesh.name} with "
                        f"'Edge' MTD shader: {mtd_name}"
                    )
                else:
                    meshes_to_bake.append(mesh)

        if not lightmap_texture_names:
            return self.error("No lightmap textures found to bake into.")
        if not meshes_to_bake:
            return self.error("No valid meshes found to bake lightmaps from.")

        # Select ONLY the meshes to be baked.
        bpy.ops.object.select_all(action="DESELECT")
        bpy.context.view_layer.objects.active = meshes_to_bake[0]  # just in case
        for mesh in meshes_to_bake:
            mesh.select_set(True)

        # Bake with Cycles.
        render_settings = {
            "engine": bpy.context.scene.render.engine,
        }
        bpy.context.scene.render.engine = "CYCLES"
        render_settings |= {
            "device": bpy.context.scene.cycles.device,
            "samples": bpy.context.scene.cycles.samples,
        }
        bpy.context.scene.cycles.device = bake_settings.bake_device
        bpy.context.scene.cycles.samples = bake_settings.bake_samples
        bpy.ops.object.bake(type="SHADOW", margin=bake_settings.bake_margin, use_selected_to_active=False)
        self.info(f"Baked {len(lightmap_texture_names)} lightmap textures: {', '.join(lightmap_texture_names)}")
        try:
            self.cleanup_callback()
        except Exception as ex:
            self.warning(f"Error during cleanup callback after Bake Lightmap operation finished: {ex}")
        return {"FINISHED"}


class ExportLightmapTextures(LoggingOperator, ImportHelper):
    bl_idname = "export_image.lightmaps"
    bl_label = "Export FLVER Lightmaps"
    bl_description = (
        "Export lightmap image textures from all materials of all selected FLVERs into a FromSoftware TPF/Binder "
        "(usually a `.tpfbhd` Binder in `mAA` folder)"
    )

    filename_ext = ".tpfbhd"

    filter_glob: bpy.props.StringProperty(
        default="*.tpf;*.tpf.dcx;*.tpfbhd;*bnd;*bnd.dcx",
        options={'HIDDEN'},
        maxlen=255,
    )

    create_new_if_missing: bpy.props.BoolProperty(
        name="Create New TPF If Missing",
        description="If enabled, exporter will create a new TPF using default DS1 lightmap settings if an existing "
                    "TPF is not found (TPFBHD files only). Leave disabled for safety if you expect to replace an "
                    "existing lightmap",
        default=False,
    )

    new_texture_dds_format: bpy.props.StringProperty(
        name="New Texture DDS Format",
        description="DDS format to use for any newly created TPFs. Default is suitable for Dark Souls 1",
        default="BC7_UNORM",
    )

    new_tpf_dcx_type: get_dcx_enum_property(
        DCXType.DS1_DS2,
        name="New TPF Compression",
        description="Type of DCX compression to apply to any newly created TPFs. Default is suitable for Dark Souls 1 "
                    "map textures going into TPFBHD Binders"
    )

    @classmethod
    def poll(cls, context):
        """FLVER armature(s) must be selected."""
        return context.selected_objects and all(obj.type == "ARMATURE" for obj in context.selected_objects)

    def execute(self, context):
        print("Executing lightmap texture export...")

        # Get active materials of all submeshes of all selected objects.
        flver_submeshes = []
        for sel_flver_armature in context.selected_objects:
            submeshes = [obj for obj in bpy.data.objects if obj.parent is sel_flver_armature and obj.type == "MESH"]
            if not submeshes:
                return self.error(f"Selected object '{sel_flver_armature.name}' has no submesh children.")
            flver_submeshes += submeshes

        bl_images = []

        for submesh in flver_submeshes:
            if not submesh.active_material:
                return self.error(f"Submesh '{submesh.name}' has no active material.")

            # Find Image Texture node of lightmap.
            for node in submesh.active_material.node_tree.nodes:
                if node.bl_idname == "ShaderNodeTexImage" and node.name.startswith("g_Lightmap |"):
                    # Found Image Texture node.
                    if not node.image:
                        self.warning(
                            f"Ignoring Image Texture node in material of mesh '{submesh.name}' with no image assigned."
                        )
                        continue  # keep searching same material
                    if node.image not in bl_images:
                        bl_images.append(node.image)
                    break  # do not look for more than one lightmap
            else:
                self.warning(f"Could not find a `g_Lightmap` Image Texture node in material of mesh '{submesh.name}'.")

        if not bl_images:
            return self.error(f"No lightmap textures found to export across selected FLVERs.")

        try:
            texture_export_info = get_texture_export_info(self.filepath)
        except Exception as ex:
            return self.error(str(ex))

        for bl_image in bl_images:
            if not bl_image:
                self.warning("Ignoring Image Texture node with no image assigned.")
                continue
            image_stem = Path(bl_image.name).stem
            image_exported, dds_format = texture_export_info.inject_texture(
                bl_image,
                new_name=image_stem,
                name_to_replace=image_stem,
                rename_tpf=False,
            )
            if image_exported:
                self.info(f"Exported texture '{image_stem}' into '{self.filepath}' with DDS format {dds_format}")
            elif self.create_new_if_missing:
                if isinstance(texture_export_info, SplitBinderTPFTextureExport):
                    new_tpf = get_lightmap_tpf(bl_image, dds_format=self.new_texture_dds_format)
                    new_tpf.dcx_type = DCXType[self.new_tpf_dcx_type]
                    new_tpf_entry_path = new_tpf.dcx_type.process_path(Path(bl_image.name).name + ".tpf")
                    texture_export_info.chrtpfbxf.add_entry(
                        BinderEntry(
                            data=new_tpf.pack_dcx(),
                            entry_id=texture_export_info.chrtpfbxf.highest_entry_id + 1,
                            path=new_tpf_entry_path,
                            flags=0x2,  # TODO: DS1 default (and everything seen so far I believe)
                        )
                    )
                else:
                    self.warning(
                        f"Could not find any TPF textures to replace with Blender image (and new TPF creation only "
                        f"currently supported for split BHD/BDT binders): '{image_stem}'"
                    )

            else:
                self.warning(
                    f"Could not find any TPF textures to replace with Blender image (and new TPF creation is "
                    f"disabled): '{image_stem}'"
                )

        # TPFs have all been updated. Now pack modified ones back to their Binders.
        try:
            write_msg = texture_export_info.write_files()
        except Exception as ex:
            return self.error(str(ex))

        self.info(write_msg)
        return {"FINISHED"}

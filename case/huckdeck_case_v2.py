"""huckdeck v2 case — button deck in front, angled OLED tier behind (MPC style).

Blender generator script (Blender 4.2+/5.x). Run it from Blender's text
editor or `blender --python case/huckdeck_case_v2.py`; it rebuilds the
"huckdeck_v2" collection from the parameters below, in millimetres.

Parts (all printed; the v1 top plate is reused unchanged):
  base_v2          — the enclosure: button section in front (takes the v1 top
                     plate), open sloped section behind for the display plate,
                     rear power slot, Pi posts, screw bosses for both plates.
  display_plate_v2 — the 30° face: window, four bosses on its back for the OLED
                     board, four counterbored corner holes. Assemble it on the
                     bench (board screwed to the plate from behind), then drop
                     it onto the base and screw it down from the outside, like
                     the lid.
  reference        — not printed: OLED board, Pi and v1 top plate placeholders
                     for checking fit in the viewport.

Both parts print without supports: base open side up, plate face down.
"""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

# ---- v1 case (unchanged, so the v1 top plate still fits) -----------------
WALL = 2.4
FLOOR_T = 2.4
CORNER_R = 6
INNER_X = 104          # 3 buttons x 36mm pitch + 2 x 16mm margin
FRONT_INNER_Y = 68     # 2 rows x 36mm pitch + 2 x 16mm margin
BASE_H = 42            # wall height of the button section
TOP_T = 3              # v1 top plate thickness
SCREW_BOSS_D = 7
CASE_PILOT_D = 2.5     # M3 self-tap
PI_HOLE_DX, PI_HOLE_DY = 58, 23
PI_W = 30
PI_POST_H = 5
PI_SCREW_D = 2.2       # M2.5 self-tap
USB_SLOT_X, USB_SLOT_W, USB_SLOT_H = -15, 26, 9

# ---- display tier -------------------------------------------------------
TILT_DEG = 30          # display face angle from horizontal
SLOPE_LEN = 56         # length of the display face along the slope
PLATE_T = 3            # display plate thickness (same as the v1 top plate)
BOARD_X, BOARD_Y = 100.7, 33.4        # OLED module PCB (measured)
HOLE_DX, HOLE_DY = 94, 28             # its M3 mounting holes (measured)
BOARD_CENTER_UP = 24   # board centre, measured up the slope from the hinge
DISP_BOSS_D = 5.5      # the panel's corner notches only clear an M3 screw head
DISP_PILOT_D = 2.5
DISP_STANDOFF = 5.1    # boss height: glass stands ~4.8 above the PCB, +0.3 clearance
LIT_X, LIT_Y = 76.8, 19.2             # lit pixel area (nominal)
LIT_FROM_LEFT, LIT_FROM_TOP = 10, 4   # lit area offset on the PCB (measured)
WINDOW_MARGIN = 1
WINDOW_R = 1.5
RIB_NOTCH_W, RIB_NOTCH_H = 40, 25     # cable pass-through under the hinge rib
PLATE_SCREW_X = 50                    # display plate corner screws: x, and up-slope positions
PLATE_SCREW_Y = (3.5, 49)
SCREW_D, SCREW_HEAD_D, SCREW_HEAD_H = 3.4, 6.0, 2.0  # M3 clearance + counterbore (v1 values)
LIP_H = 3.5
RIB_Y1 = 80            # hinge rib spans from the front section's back wall to here

# ---- derived ------------------------------------------------------------
OUTER_X = INNER_X + 2 * WALL
HINGE_Y = WALL + FRONT_INNER_Y + WALL    # rear edge of the top plate
HINGE_Z = BASE_H + TOP_T                 # top surface of the top plate
THETA = math.radians(TILT_DEG)
DEPTH = HINGE_Y + SLOPE_LEN * math.cos(THETA)
REAR_H = HINGE_Z + SLOPE_LEN * math.sin(THETA)
INNER_REAR_Y = DEPTH - WALL
# slope frame: local x = world x, local y = up the slope, local z = outward normal
SLOPE = Matrix.Translation((0, HINGE_Y, HINGE_Z)) @ Matrix.Rotation(THETA, 4, "X")
PLATE_Z = -PLATE_T                       # the base's walls end here; the plate sits on them
# lit area centre relative to the board centre (header on the right, +x)
WINDOW_DX = (LIT_FROM_LEFT + LIT_X / 2) - BOARD_X / 2
WINDOW_DY = BOARD_Y / 2 - (LIT_FROM_TOP + LIT_Y / 2)
WINDOW_X, WINDOW_Y = LIT_X + 2 * WINDOW_MARGIN, LIT_Y + 2 * WINDOW_MARGIN

COLLECTION = "huckdeck_v2"


# ---- helpers ------------------------------------------------------------
def _collection(name: str, parent=None):
    coll = bpy.data.collections.get(name)
    if coll is None:
        coll = bpy.data.collections.new(name)
        (parent or bpy.context.scene.collection).children.link(coll)
    return coll


def _obj(name: str, bm: bmesh.types.BMesh, coll) -> bpy.types.Object:
    mesh = bpy.data.meshes.new(name)
    bm.to_mesh(mesh)
    bm.free()
    obj = bpy.data.objects.new(name, mesh)
    coll.objects.link(obj)
    return obj


def rounded_rect_prism(name, sx, sy, r, z0, z1, cx=0.0, cy=0.0, coll=None, seg=12):
    bm = bmesh.new()
    pts = []
    for ox, oy, a0 in ((1, 1, 0), (-1, 1, 90), (-1, -1, 180), (1, -1, 270)):
        ccx, ccy = cx + ox * (sx / 2 - r), cy + oy * (sy / 2 - r)
        for i in range(seg + 1):
            a = math.radians(a0 + 90 * i / seg)
            pts.append((ccx + r * math.cos(a), ccy + r * math.sin(a), z0))
    face = bm.faces.new([bm.verts.new(p) for p in pts])
    res = bmesh.ops.extrude_face_region(bm, geom=[face])
    top = [g for g in res["geom"] if isinstance(g, bmesh.types.BMVert)]
    bmesh.ops.translate(bm, verts=top, vec=(0, 0, z1 - z0))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
    return _obj(name, bm, coll)


def box(name, xr, yr, zr, matrix=None, coll=None):
    """Axis-aligned box over the given ranges, then transformed by matrix."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    bmesh.ops.scale(bm, vec=(xr[1] - xr[0], yr[1] - yr[0], zr[1] - zr[0]), verts=bm.verts)
    bmesh.ops.translate(bm, vec=(sum(xr) / 2, sum(yr) / 2, sum(zr) / 2), verts=bm.verts)
    if matrix is not None:
        bmesh.ops.transform(bm, matrix=matrix, verts=bm.verts)
    return _obj(name, bm, coll)


def cylinders(name, specs, matrix=None, coll=None, seg=48):
    """One mesh holding several (cx, cy, z0, z1, d) cylinders (non-overlapping)."""
    bm = bmesh.new()
    for cx, cy, z0, z1, d in specs:
        res = bmesh.ops.create_cone(
            bm, cap_ends=True, segments=seg, radius1=d / 2, radius2=d / 2, depth=z1 - z0
        )
        bmesh.ops.translate(bm, vec=(cx, cy, (z0 + z1) / 2), verts=res["verts"])
    if matrix is not None:
        bmesh.ops.transform(bm, matrix=matrix, verts=bm.verts)
    return _obj(name, bm, coll)


def boolean(target, other, op):
    mod = target.modifiers.new(op, "BOOLEAN")
    mod.operation = op
    mod.object = other
    mod.solver = "EXACT"
    bpy.context.view_layer.objects.active = target
    with bpy.context.temp_override(object=target, active_object=target, selected_objects=[target]):
        bpy.ops.object.modifier_apply(modifier=mod.name)
    bpy.data.objects.remove(other, do_unlink=True)


# ---- build --------------------------------------------------------------
def build_base(coll):
    outer = rounded_rect_prism("base_v2", OUTER_X, DEPTH, CORNER_R, 0, REAR_H, cy=DEPTH / 2, coll=coll)
    # open top over the button section (the v1 top plate sits here)
    boolean(outer, box("cut_front_top", (-100, 100), (-1, HINGE_Y), (BASE_H, 200), coll=coll), "DIFFERENCE")
    # the walls of the rear section end one plate thickness below the slope
    # surface; the cut starts at the hinge with a vertical face, like the plate
    slope_cut = box("cut_slope", (-100, 100), (-20, 300), (PLATE_Z, 200), SLOPE, coll=coll)
    boolean(slope_cut, box("cut_slope_clip", (-100, 100), (HINGE_Y, 400), (-50, 400), coll=coll), "INTERSECT")
    boolean(outer, slope_cut, "DIFFERENCE")
    inner = rounded_rect_prism(
        "cavity", INNER_X, INNER_REAR_Y - WALL, CORNER_R - WALL, FLOOR_T, 200,
        cy=(WALL + INNER_REAR_Y) / 2, coll=coll,
    )
    boolean(outer, inner, "DIFFERENCE")

    # hinge rib: the top plate's rear edge rests on its front part, the display
    # plate's lower edge on its (slightly lower) rear part; notch for the wires
    rib_y0 = WALL + FRONT_INNER_Y
    boolean(outer, box("rib", (-INNER_X / 2 - 0.1, INNER_X / 2 + 0.1), (rib_y0, RIB_Y1), (FLOOR_T - 0.1, BASE_H), coll=coll), "UNION")
    plate_low_z = HINGE_Z - PLATE_T / math.cos(THETA)  # plate underside at the hinge
    boolean(outer, box("rib_step", (-INNER_X / 2, INNER_X / 2), (HINGE_Y, RIB_Y1 + 1), (plate_low_z - 0.2, 60), coll=coll), "DIFFERENCE")
    boolean(outer, box("rib_notch", (-RIB_NOTCH_W / 2, RIB_NOTCH_W / 2), (rib_y0 - 1, RIB_Y1 + 1), (-1, FLOOR_T + RIB_NOTCH_H), coll=coll), "DIFFERENCE")

    # lid screw bosses, same positions as v1 (M3 self-tap)
    front_cy = WALL + FRONT_INNER_Y / 2
    corners = [
        (sx * (INNER_X / 2 - SCREW_BOSS_D / 2 + 1), front_cy + sy * (FRONT_INNER_Y / 2 - SCREW_BOSS_D / 2 + 1))
        for sx in (-1, 1) for sy in (-1, 1)
    ]
    boss_top = BASE_H - 3.2
    boolean(outer, cylinders("lid_bosses", [(x, y, FLOOR_T - 0.1, boss_top, SCREW_BOSS_D) for x, y in corners], coll=coll), "UNION")
    boolean(outer, cylinders("lid_pilots", [(x, y, boss_top - 10, boss_top + 1, CASE_PILOT_D) for x, y in corners], coll=coll), "DIFFERENCE")

    # display plate screw bosses: under the plate, merged into the side walls,
    # the rib (front pair) and the rear wall (rear pair)
    plate_screws = [(sx * PLATE_SCREW_X, y) for sx in (-1, 1) for y in PLATE_SCREW_Y]
    boolean(outer, cylinders("plate_bosses", [(x, y, PLATE_Z - 7, PLATE_Z - 0.05, SCREW_BOSS_D) for x, y in plate_screws], SLOPE, coll=coll), "UNION")
    boolean(outer, cylinders("plate_pilots", [(x, y, PLATE_Z - 7.5, PLATE_Z + 1, CASE_PILOT_D) for x, y in plate_screws], SLOPE, coll=coll), "DIFFERENCE")
    # anything that poked outside the shell (boss ends inside the rear wall)
    boolean(outer, box("trim_rear", (-100, 100), (DEPTH, DEPTH + 50), (-10, 200), coll=coll), "DIFFERENCE")

    # Pi under the display tier, ports 1mm from the rear wall
    pi_cy = INNER_REAR_Y - 1 - PI_W / 2
    posts = [(px * PI_HOLE_DX / 2, pi_cy + py * PI_HOLE_DY / 2) for px in (-1, 1) for py in (-1, 1)]
    boolean(outer, cylinders("pi_posts", [(x, y, FLOOR_T - 0.1, FLOOR_T + PI_POST_H, 6) for x, y in posts], coll=coll), "UNION")
    boolean(outer, cylinders("pi_pilots", [(x, y, FLOOR_T + 1, FLOOR_T + PI_POST_H + 1, PI_SCREW_D) for x, y in posts], coll=coll), "DIFFERENCE")
    slot_z0 = FLOOR_T + PI_POST_H - 1
    boolean(outer, box("usb_slot", (USB_SLOT_X - USB_SLOT_W / 2, USB_SLOT_X + USB_SLOT_W / 2), (INNER_REAR_Y - 1, DEPTH + 1), (slot_z0, slot_z0 + USB_SLOT_H), coll=coll), "DIFFERENCE")
    return outer


def build_display_plate(coll):
    # slab in the slope frame, trimmed to the case footprint (rounded corners)
    # and to vertical end faces at the hinge and the rear
    plate = box("display_plate_v2", (-100, 100), (-20, 100), (PLATE_Z, 0), SLOPE, coll=coll)
    boolean(plate, rounded_rect_prism("footprint", OUTER_X, DEPTH, CORNER_R, -50, 200, cy=DEPTH / 2, coll=coll), "INTERSECT")
    boolean(plate, box("hinge_cut", (-100, 100), (-50, HINGE_Y), (-50, 200), coll=coll), "DIFFERENCE")
    # registration lip inside the rear wall (the hinge end butts the top plate,
    # the sides are located by the screws; a front lip would hit the board)
    lip_x = INNER_X / 2 - 8
    lip = box("lip_rear", (-100, 100), (-20, 100), (PLATE_Z - LIP_H, PLATE_Z + 0.1), SLOPE, coll=coll)
    boolean(lip, box("lip_rear_clip", (-lip_x, lip_x), (INNER_REAR_Y - 0.2 - WALL, INNER_REAR_Y - 0.2), (-50, 200), coll=coll), "INTERSECT")
    boolean(plate, lip, "UNION")
    # OLED board: bosses on the back, pilot holes, window
    holes = [(hx * HOLE_DX / 2, BOARD_CENTER_UP + hy * HOLE_DY / 2) for hx in (-1, 1) for hy in (-1, 1)]
    boolean(plate, cylinders("disp_bosses", [(x, y, PLATE_Z - DISP_STANDOFF, PLATE_Z + 0.1, DISP_BOSS_D) for x, y in holes], SLOPE, coll=coll), "UNION")
    boolean(plate, cylinders("disp_pilots", [(x, y, PLATE_Z - DISP_STANDOFF - 1, -1.0, DISP_PILOT_D) for x, y in holes], SLOPE, coll=coll), "DIFFERENCE")
    window = rounded_rect_prism("window", WINDOW_X, WINDOW_Y, WINDOW_R, -10, 10, cx=WINDOW_DX, cy=BOARD_CENTER_UP + WINDOW_DY, coll=coll)
    window.data.transform(SLOPE)
    boolean(plate, window, "DIFFERENCE")
    # corner screws: clearance hole + counterbore so the heads sit flush
    plate_screws = [(sx * PLATE_SCREW_X, y) for sx in (-1, 1) for y in PLATE_SCREW_Y]
    boolean(plate, cylinders("plate_holes", [(x, y, PLATE_Z - 1, 1, SCREW_D) for x, y in plate_screws], SLOPE, coll=coll), "DIFFERENCE")
    boolean(plate, cylinders("plate_cbores", [(x, y, -SCREW_HEAD_H, 1, SCREW_HEAD_D) for x, y in plate_screws], SLOPE, coll=coll), "DIFFERENCE")
    return plate


def build_reference(coll):
    """Non-printed placeholders for checking fit."""
    # OLED module: PCB + raised glass, on the bosses
    pcb_z1 = PLATE_Z - DISP_STANDOFF
    pcb = box("ref_oled_pcb", (-BOARD_X / 2, BOARD_X / 2), (BOARD_CENTER_UP - BOARD_Y / 2, BOARD_CENTER_UP + BOARD_Y / 2), (pcb_z1 - 1.6, pcb_z1), SLOPE, coll=coll)
    glass = box("ref_oled_glass", (-BOARD_X / 2 + 5, BOARD_X / 2 - 6), (BOARD_CENTER_UP - BOARD_Y / 2 + 1.5, BOARD_CENTER_UP + BOARD_Y / 2 - 1.5), (pcb_z1, pcb_z1 + 4.8), SLOPE, coll=coll)
    for hx in (-1, 1):  # the panel's corners are notched around the mounting holes
        for hy in (-1, 1):
            cx, cy = hx * HOLE_DX / 2, BOARD_CENTER_UP + hy * HOLE_DY / 2
            boolean(glass, box("notch", (cx - 3.5, cx + 3.5), (cy - 3.5, cy + 3.5), (pcb_z1 - 1, pcb_z1 + 6), SLOPE, coll=coll), "DIFFERENCE")
    lit = box("ref_oled_lit", (WINDOW_DX - LIT_X / 2, WINDOW_DX + LIT_X / 2), (BOARD_CENTER_UP + WINDOW_DY - LIT_Y / 2, BOARD_CENTER_UP + WINDOW_DY + LIT_Y / 2), (pcb_z1 + 4.8, pcb_z1 + 4.9), SLOPE, coll=coll)
    header = box("ref_oled_header", (BOARD_X / 2 - 8, BOARD_X / 2 - 2), (BOARD_CENTER_UP - 9, BOARD_CENTER_UP + 9), (pcb_z1 - 1.6 - 17.5, pcb_z1 - 1.6), SLOPE, coll=coll)
    # Pi Zero 2 W with its GPIO header + jumper housings
    pi_cy = INNER_REAR_Y - 1 - PI_W / 2
    pi_z0 = FLOOR_T + PI_POST_H
    pi = box("ref_pi", (-32.5, 32.5), (pi_cy - PI_W / 2, pi_cy + PI_W / 2), (pi_z0, pi_z0 + 1.6), coll=coll)
    pi_header = box("ref_pi_header", (-25.4, 25.4), (pi_cy - PI_W / 2 + 1, pi_cy - PI_W / 2 + 6), (pi_z0 + 1.6, pi_z0 + 1.6 + 2.5 + 14), coll=coll)
    # v1 top plate (with lip) for reference
    plate = box("ref_top_plate", (-OUTER_X / 2, OUTER_X / 2), (0, HINGE_Y), (BASE_H, HINGE_Z), coll=coll)
    front_cy = WALL + FRONT_INNER_Y / 2
    buttons = cylinders(
        "ref_buttons",
        [((cx - 1) * 36, front_cy + (cy - 0.5) * 36, HINGE_Z, HINGE_Z + 9, 28) for cx in range(3) for cy in range(2)],
        coll=coll,
    )
    return [pcb, glass, lit, header, pi, pi_header, plate, buttons]


def build():
    scene = bpy.context.scene
    scene.unit_settings.system = "METRIC"
    scene.unit_settings.scale_length = 0.001  # 1 Blender unit = 1 mm
    scene.unit_settings.length_unit = "MILLIMETERS"
    old = bpy.data.collections.get(COLLECTION)
    if old is not None:
        for o in list(old.all_objects):
            bpy.data.objects.remove(o, do_unlink=True)
        for c in list(old.children):
            bpy.data.collections.remove(c)
        bpy.data.collections.remove(old)
    coll = _collection(COLLECTION)
    ref = _collection("reference", coll)
    base = build_base(coll)
    plate = build_display_plate(coll)
    build_reference(ref)
    return base, plate


if __name__ == "__main__":
    build()

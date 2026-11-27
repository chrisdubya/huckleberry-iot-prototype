"""huckdeck v3 case — one-piece wedge for the ESP32-S3 build.

Blender generator script (Blender 4.2+/5.x). Run it from Blender's text
editor or `blender --python case/huckdeck_case_v3.py`; it rebuilds the
"huckdeck_v3" collection from the parameters below, in millimetres.

Parts:
  shell_v3   — walls and the sloped face in one piece: six button holes, the
               OLED window with its rails on the back (same mount as v2), the
               light duct down to the board's RGB LED, the USB-C slot in the
               left wall and four corner bosses for the bottom screws.
               Prints face down, no supports.
  bottom_v3  — flat plate recessed into the shell, with four posts for the
               ESP32 expansion base. Screws go in from underneath, so nothing
               shows on the outside. Prints as modelled.
  reference  — not printed: buttons, OLED, expansion base, dev board, jumper
               housings, USB plugs, for checking fit in the viewport.

The ESP32 stays on its expansion base (the base has the mounting holes and
the jumpers stay where they are). It lies flat under the display with the
USB-C ports at the left wall; the LED duct runs square to the face from the
strip between the buttons and the display straight down to the WS2812.

THE BOARD NUMBERS BELOW MARKED "est." WERE SCALED FROM PRODUCT PHOTOS, not
measured. Check them with calipers before printing; see docs/hardware.md.
"""

from __future__ import annotations

import math

import bmesh
import bpy
from mathutils import Matrix, Vector

# ---- shell --------------------------------------------------------------
WALL = 2.4
PLATE_T = 3            # the sloped face; snap-in buttons clamp onto 2-4mm
BOTTOM_T = 3.6
CORNER_R = 8
EDGE_CHAMFER = 1.2     # around the face (it prints on the bed, so 45°, not a round)
TILT_DEG = 10
INNER_X = 104          # as v1/v2: 3 buttons x 36 + 2 x 16; the OLED board is 100.7
FRONT_H = 35           # outer height at the front edge

# ---- buttons (24mm snap-in arcade, as v1) ---------------------------------
BUTTON_HOLE_D = 24.4
BUTTON_PITCH_X = 36
BUTTON_PITCH_S = 32    # row pitch along the slope (v1/v2: 36)
BUTTON_FRONT_S = 17    # front row centre, along the slope from the front edge
BUTTON_BEZEL_D = 28
BUTTON_BODY_D = 25     # keep-out below the plate
BUTTON_DEPTH = 32      # below the face incl. lugs and solder (v1 figure)

# ---- OLED module (measured, see docs/hardware.md; mount copied from v2) ---
BOARD_X, BOARD_Y = 100.7, 33.4
HOLE_D = 3.28
HOLE_DX, HOLE_DY = 98 - HOLE_D, 32 - HOLE_D
GLASS_FROM_LEFT, GLASS_FROM_RIGHT = 4.66, 6.0
RAIL_W = 4.0
RAIL_INSET = 0.3
DISP_PILOT_D = 2.6
DISP_PILOT_INTO_PLATE = 2.5
DISP_STANDOFF = 5.1
LIT_X, LIT_Y = 76.8, 19.2
LIT_FROM_LEFT, LIT_FROM_TOP = 11, 4
WINDOW_MARGIN = 0.5
WINDOW_R = 1.5
HEADER_SPAN = (5.9, 26.2)
HEADER_CLEAR = 1.0
DISP_BACK_CLEAR = 12.5  # face to the back of the M3 screw heads behind the PCB

# ---- ESP32-S3 dev board on its 2.54mm expansion base ----------------------
# est. = scaled from the Lonely Binary product photos (±1mm). Measure these.
BASE_L, BASE_W = 71.2, 47.1          # est. expansion base PCB
BASE_HOLE_DL, BASE_HOLE_DW = 63.6, 40.1   # est. mounting holes, centre to centre
BASE_PCB_T = 1.6
BASE_POST_H = 4.5
BASE_POST_D = 7
BASE_PILOT_D = 2.5     # M3 self-tap (the holes look like M3/M4 clearance)
STACK_H = 11.0         # est. base PCB top to dev PCB underside (socket + header plastic)
DEV_L, DEV_W = 60.9, 25.4            # est. dev board PCB
DEV_USB_INSET = 3.3    # est. dev PCB's USB edge, in from the base's USB edge
USB_PITCH = 11.3       # est. between the two USB-C ports
USB_Z_ABOVE_PCB = 1.6  # USB-C receptacle centre above the dev PCB
USB_SLOT_W, USB_SLOT_H = 25.5, 9.0   # one slot for both ports; plug overmoulds enter it
LED_FROM_USB_EDGE = 26.9   # est. WS2812 centre from the dev PCB's USB edge
LED_OFF_CENTRE = 2.3       # est. towards the front of the case (USB ports left)
LED_H = 1.6
JUMPER_ROW = 19.2      # est. base male pin rows, from the base centreline
JUMPER_H = 16.5        # pin header plastic + Dupont housing, above the base PCB
BASE_WALL_GAP = 0.5    # base USB edge to the inside of the left wall

# ---- LED light duct -------------------------------------------------------
LED_APERTURE_D = 4.0   # the hole in the face
LED_SKIN = 0.0         # >0 leaves this much face over the hole (0.4 = glow-through in light filament)
DUCT_BORE_D = 5.0
DUCT_WALL = 1.2
DUCT_GAP = 2.0         # duct end to the top of the LED
DUCT_FLARE_L = 6.0     # the last stretch opens out, to forgive a misplaced LED
DUCT_FLARE_ADD = 1.5   # extra radius at the mouth

# ---- screws ---------------------------------------------------------------
CASE_PILOT_D = 2.5     # M3 self-tap into the corner bosses
SCREW_D, SCREW_HEAD_D, SCREW_HEAD_H = 3.4, 6.4, 2.2   # M3 x 8 button head, flush
BOSS = 8.4             # corner boss block; screw centre BOSS/2 from each wall
BOSS_FLAT = 10         # full-section height, then it tapers into the corner
BOTTOM_FIT = 0.25      # gap around the bottom plate

# ---- derived --------------------------------------------------------------
THETA = math.radians(TILT_DEG)
SIN, COS = math.sin(THETA), math.cos(THETA)
OUTER_X = INNER_X + 2 * WALL
X_IN = INNER_X / 2
# slope frame: local x = world x, local y = s (up the slope from the front
# edge), local z = outward normal; the face is local z = 0
SLOPE = Matrix.Translation((0, 0, FRONT_H)) @ Matrix.Rotation(THETA, 4, "X")
SLOPE_INV = SLOPE.inverted()


def at_slope(s, d):
    """World (y, z) of the point s up the slope and d below the face."""
    return s * COS + d * SIN, FRONT_H + s * SIN - d * COS


BUTTON_REAR_S = BUTTON_FRONT_S + BUTTON_PITCH_S
BUTTONS = [((c - 1) * BUTTON_PITCH_X, s) for c in range(3) for s in (BUTTON_FRONT_S, BUTTON_REAR_S)]
# the base goes as far forward as the rear buttons allow: its front row of
# jumper housings clears the rear-most point of the button bodies
_btn_back_y, _ = at_slope(BUTTON_REAR_S + BUTTON_BODY_D / 2, BUTTON_DEPTH)
BASE_CY = _btn_back_y + 1.0 + JUMPER_ROW + 1.3
BASE_X0 = -X_IN + BASE_WALL_GAP              # the base's USB edge
BASE_CX = BASE_X0 + BASE_L / 2
BASE_Z0 = BOTTOM_T + BASE_POST_H             # underside of the base PCB
DEV_Z0 = BASE_Z0 + BASE_PCB_T + STACK_H      # underside of the dev PCB
DEV_Z1 = DEV_Z0 + 1.6
DEV_X0 = BASE_X0 + DEV_USB_INSET
USB_Z = DEV_Z1 + USB_Z_ABOVE_PCB
LED_POS = Vector((DEV_X0 + LED_FROM_USB_EDGE, BASE_CY - LED_OFF_CENTRE, DEV_Z1 + LED_H))
_led_local = SLOPE_INV @ LED_POS
LED_S, LED_D = _led_local.y, -_led_local.z   # where the duct meets the face, and how deep the LED is
DUCT_R = DUCT_BORE_D / 2 + DUCT_WALL
DUCT_END_D = LED_D - DUCT_GAP
# the OLED board starts just behind the duct
BOARD_S0 = LED_S + DUCT_R + 0.8
BOARD_S1 = BOARD_S0 + BOARD_Y
BOARD_SC = (BOARD_S0 + BOARD_S1) / 2
# rear wall: behind the display's screw heads, and leaving the rear-left
# corner boss clear of the base
INNER_REAR_Y = max(at_slope(BOARD_S1, DISP_BACK_CLEAR)[0] + 0.8, BASE_CY + BASE_W / 2 + 0.4 + BOSS)
DEPTH = INNER_REAR_Y + WALL
REAR_H = FRONT_H + DEPTH * math.tan(THETA)
WINDOW_DX = (LIT_FROM_LEFT + LIT_X / 2) - BOARD_X / 2
WINDOW_DS = BOARD_Y / 2 - (LIT_FROM_TOP + LIT_Y / 2)
WINDOW_X, WINDOW_Y = LIT_X + 2 * WINDOW_MARGIN, LIT_Y + 2 * WINDOW_MARGIN
BOSS_CORNERS = [(sx, front) for sx in (-1, 1) for front in (True, False)]

COLLECTION = "huckdeck_v3"


# ---- helpers (as v2) --------------------------------------------------------
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


def rounded_rect_prism(name, sx, sy, r, z0, z1, cx=0.0, cy=0.0, coll=None, seg=16):
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


def cylinders(name, specs, matrix=None, coll=None, seg=64):
    """One mesh holding several (cx, cy, z0, z1, d[, d_top]) cylinders or cones (non-overlapping)."""
    bm = bmesh.new()
    for cx, cy, z0, z1, d, *rest in specs:
        d1 = rest[0] if rest else d
        res = bmesh.ops.create_cone(
            bm, cap_ends=True, segments=seg, radius1=d / 2, radius2=d1 / 2, depth=z1 - z0
        )
        bmesh.ops.translate(bm, vec=(cx, cy, (z0 + z1) / 2), verts=res["verts"])
    if matrix is not None:
        bmesh.ops.transform(bm, matrix=matrix, verts=bm.verts)
    return _obj(name, bm, coll)


def lathe(name, cx, cy, profile, matrix=None, coll=None, seg=64):
    """Solid of revolution about the z axis through (cx, cy), from (radius, z) points, bottom to top."""
    bm = bmesh.new()
    rings = [
        [bm.verts.new((cx + r * math.cos(2 * math.pi * i / seg), cy + r * math.sin(2 * math.pi * i / seg), z)) for i in range(seg)]
        for r, z in profile
    ]
    for a, b in zip(rings, rings[1:]):
        for i in range(seg):
            j = (i + 1) % seg
            bm.faces.new((a[i], a[j], b[j], b[i]))
    bm.faces.new(rings[0])
    bm.faces.new(rings[-1])
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces)
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


def chamfer_face_edges(obj, normal, offset):
    """45° chamfer on the outline of the faces whose normal is `normal`."""
    bm = bmesh.new()
    bm.from_mesh(obj.data)
    edges = [
        e for e in bm.edges
        if len(e.link_faces) == 2 and sum(f.normal.dot(normal) > 0.999 for f in e.link_faces) == 1
    ]
    bmesh.ops.bevel(bm, geom=edges, offset=offset, segments=1, profile=0.5, affect="EDGES")
    bm.to_mesh(obj.data)
    bm.free()


# ---- build --------------------------------------------------------------
def build_shell(coll):
    shell = rounded_rect_prism("shell_v3", OUTER_X, DEPTH, CORNER_R, 0, REAR_H + 10, cy=DEPTH / 2, coll=coll)
    boolean(shell, box("cut_face", (-200, 200), (-100, 400), (0, 200), SLOPE, coll=coll), "DIFFERENCE")
    chamfer_face_edges(shell, SLOPE.to_3x3() @ Vector((0, 0, 1)), EDGE_CHAMFER)
    cavity = rounded_rect_prism(
        "cavity", INNER_X, INNER_REAR_Y - WALL, CORNER_R - WALL, -1, REAR_H + 10,
        cy=(WALL + INNER_REAR_Y) / 2, coll=coll,
    )
    boolean(cavity, box("cut_cavity", (-200, 200), (-100, 400), (-PLATE_T, 200), SLOPE, coll=coll), "DIFFERENCE")
    boolean(shell, cavity, "DIFFERENCE")

    # corner bosses for the bottom screws: blocks filling the corners, tapered
    # back into the walls above (so they print without support, and the rear
    # pair stays below the display)
    for sx, front in BOSS_CORNERS:
        cx, cy = sx * X_IN, (WALL if front else INNER_REAR_Y)
        iy = 1 if front else -1
        block = box(
            f"boss_{sx}_{front}", (min(cx, cx - sx * BOSS), max(cx, cx - sx * BOSS)),
            (min(cy, cy + iy * BOSS), max(cy, cy + iy * BOSS)), (BOTTOM_T + 0.05, BOTTOM_T + BOSS_FLAT + 2 * BOSS), coll=coll,
        )
        n = Vector((-sx, iy, 1)).normalized()
        taper = Matrix.Translation((cx, cy, BOTTOM_T + BOSS_FLAT + 2 * BOSS)) @ Vector((0, 0, 1)).rotation_difference(n).to_matrix().to_4x4()
        boolean(block, box("boss_taper", (-50, 50), (-50, 50), (0, 50), taper, coll=coll), "DIFFERENCE")
        # push it into the walls so the union is clean, but not out through
        # the rounded outside corner
        block.data.transform(Matrix.Translation((sx * 0.3, -iy * 0.3, 0)))
        boolean(block, rounded_rect_prism("boss_clip", OUTER_X - 1.2, DEPTH - 1.2, CORNER_R - 0.6, -1, REAR_H, cy=DEPTH / 2, coll=coll), "INTERSECT")
        boolean(shell, block, "UNION")
    boolean(shell, cylinders("boss_pilots", [(x, y, BOTTOM_T - 1, BOTTOM_T + 9, CASE_PILOT_D) for x, y in boss_screws()], coll=coll), "DIFFERENCE")

    # OLED rails on the back of the face (v2's mount): the board sits glass
    # down on rails along its short edges, M3 x 8 from behind into the face
    x_out, x_in = BOARD_X / 2 - RAIL_INSET, BOARD_X / 2 - RAIL_INSET - RAIL_W
    spans = {
        -1: [(BOARD_S0 + 0.2, BOARD_S1 - 0.2)],
        1: [(BOARD_S0 + 0.2, BOARD_S0 + HEADER_SPAN[0] - HEADER_CLEAR), (BOARD_S0 + HEADER_SPAN[1] + HEADER_CLEAR, BOARD_S1 - 0.2)],
    }
    for sx, ranges in spans.items():
        for i, (s0, s1) in enumerate(ranges):
            boolean(shell, box(f"disp_rail_{sx}_{i}", (min(sx * x_out, sx * x_in), max(sx * x_out, sx * x_in)), (s0, s1), (-PLATE_T - DISP_STANDOFF, -PLATE_T + 0.1), SLOPE, coll=coll), "UNION")
    holes = [(hx * HOLE_DX / 2, BOARD_SC + hy * HOLE_DY / 2) for hx in (-1, 1) for hy in (-1, 1)]
    boolean(shell, cylinders("disp_pilots", [(x, s, -PLATE_T - DISP_STANDOFF - 1, -PLATE_T + DISP_PILOT_INTO_PLATE, DISP_PILOT_D) for x, s in holes], SLOPE, coll=coll), "DIFFERENCE")
    window = rounded_rect_prism("window", WINDOW_X, WINDOW_Y, WINDOW_R, -10, 10, cx=WINDOW_DX, cy=BOARD_SC + WINDOW_DS, coll=coll)
    window.data.transform(SLOPE)
    boolean(shell, window, "DIFFERENCE")

    # LED light duct: a tube square to the face, from the face down to just
    # above the WS2812, opening out over the last few millimetres
    lx = LED_POS.x
    flare_z = -DUCT_END_D + DUCT_FLARE_L
    bore_r, ap_r = DUCT_BORE_D / 2, LED_APERTURE_D / 2
    boolean(shell, lathe("duct", lx, LED_S, [(DUCT_R + DUCT_FLARE_ADD, -DUCT_END_D), (DUCT_R, flare_z), (DUCT_R, -PLATE_T + 0.1)], SLOPE, coll=coll), "UNION")
    boolean(shell, lathe("duct_bore", lx, LED_S, [
        (bore_r + DUCT_FLARE_ADD, -DUCT_END_D - 0.1), (bore_r, flare_z), (bore_r, -PLATE_T),
        (ap_r, -PLATE_T), (ap_r, 1 if LED_SKIN <= 0 else -LED_SKIN),
    ], SLOPE, coll=coll), "DIFFERENCE")

    # buttons
    boolean(shell, cylinders("button_holes", [(x, s, -PLATE_T - 1, 1, BUTTON_HOLE_D) for x, s in BUTTONS], SLOPE, coll=coll), "DIFFERENCE")

    # USB-C slot in the left wall, rounded ends, both ports
    half = (USB_SLOT_W - USB_SLOT_H) / 2
    xr = (-OUTER_X / 2 - 1, -X_IN + 1)
    boolean(shell, box("usb_slot", xr, (BASE_CY - half, BASE_CY + half), (USB_Z - USB_SLOT_H / 2, USB_Z + USB_SLOT_H / 2), coll=coll), "DIFFERENCE")
    along_x = Matrix.Rotation(math.radians(90), 4, "Y")
    for i, y in enumerate((BASE_CY - half, BASE_CY + half)):
        end = cylinders(f"usb_slot_end_{i}", [(0, 0, xr[0], xr[1], USB_SLOT_H)], Matrix.Translation((0, y, USB_Z)) @ along_x, coll=coll)
        boolean(shell, end, "DIFFERENCE")
    return shell


def boss_screws():
    return [
        (sx * (X_IN - BOSS / 2), (WALL + BOSS / 2) if front else (INNER_REAR_Y - BOSS / 2))
        for sx, front in BOSS_CORNERS
    ]


def base_holes():
    return [(BASE_CX + hx * BASE_HOLE_DL / 2, BASE_CY + hy * BASE_HOLE_DW / 2) for hx in (-1, 1) for hy in (-1, 1)]


def build_bottom(coll):
    plate = rounded_rect_prism(
        "bottom_v3", INNER_X - 2 * BOTTOM_FIT, INNER_REAR_Y - WALL - 2 * BOTTOM_FIT, CORNER_R - WALL - BOTTOM_FIT,
        0, BOTTOM_T, cy=(WALL + INNER_REAR_Y) / 2, coll=coll,
    )
    boolean(plate, cylinders("base_posts", [(x, y, BOTTOM_T - 0.1, BASE_Z0, BASE_POST_D) for x, y in base_holes()], coll=coll), "UNION")
    boolean(plate, cylinders("base_pilots", [(x, y, 0.6, BASE_Z0 + 1, BASE_PILOT_D) for x, y in base_holes()], coll=coll), "DIFFERENCE")
    boolean(plate, cylinders("screw_holes", [(x, y, -1, BOTTOM_T + 1, SCREW_D) for x, y in boss_screws()], coll=coll), "DIFFERENCE")
    boolean(plate, cylinders("screw_cbores", [(x, y, -1, SCREW_HEAD_H, SCREW_HEAD_D) for x, y in boss_screws()], coll=coll), "DIFFERENCE")
    return plate


def build_reference(coll):
    """Non-printed placeholders for checking fit."""
    refs = []
    # buttons: bezel above the face, body and lugs below it
    refs.append(cylinders("ref_button_bezels", [(x, s, 0, 4, BUTTON_BEZEL_D) for x, s in BUTTONS], SLOPE, coll=coll))
    refs.append(cylinders("ref_button_bodies", [(x, s, -BUTTON_DEPTH, -PLATE_T, BUTTON_BODY_D) for x, s in BUTTONS], SLOPE, coll=coll))
    # OLED module on its rails
    pcb_z1 = -PLATE_T - DISP_STANDOFF
    bx = (-BOARD_X / 2, BOARD_X / 2)
    refs.append(box("ref_oled_pcb", bx, (BOARD_S0, BOARD_S1), (pcb_z1 - 1.6, pcb_z1), SLOPE, coll=coll))
    refs.append(box("ref_oled_glass", (bx[0] + GLASS_FROM_LEFT, bx[1] - GLASS_FROM_RIGHT), (BOARD_S0 + 1.5, BOARD_S1 - 1.5), (pcb_z1, pcb_z1 + 4.8), SLOPE, coll=coll))
    refs.append(box("ref_oled_lit", (WINDOW_DX - LIT_X / 2, WINDOW_DX + LIT_X / 2), (BOARD_SC + WINDOW_DS - LIT_Y / 2, BOARD_SC + WINDOW_DS + LIT_Y / 2), (pcb_z1 + 4.8, pcb_z1 + 4.9), SLOPE, coll=coll))
    hdr = ((bx[1] - 4.8, bx[1] - 0.2), (BOARD_S0 + HEADER_SPAN[0], BOARD_S0 + HEADER_SPAN[1]))
    refs.append(box("ref_oled_header_jumpers", *hdr, (pcb_z1 - 1.6 - 17.5, pcb_z1 - 1.6), SLOPE, coll=coll))
    refs.append(box("ref_oled_joints", *hdr, (pcb_z1, pcb_z1 + 1.2), SLOPE, coll=coll))
    refs.append(cylinders("ref_oled_screws", [(hx * HOLE_DX / 2, BOARD_SC + hy * HOLE_DY / 2, -DISP_BACK_CLEAR, pcb_z1 - 1.6, 5.7) for hx in (-1, 1) for hy in (-1, 1)], SLOPE, coll=coll))
    # expansion base, the dev board on its sockets, jumper housings on the base's pin rows
    by = (BASE_CY - BASE_W / 2, BASE_CY + BASE_W / 2)
    base_top = BASE_Z0 + BASE_PCB_T
    refs.append(box("ref_base_pcb", (BASE_X0, BASE_X0 + BASE_L), by, (BASE_Z0, base_top), coll=coll))
    dev_x = (DEV_X0, DEV_X0 + DEV_L)
    dev_y = (BASE_CY - DEV_W / 2, BASE_CY + DEV_W / 2)
    for i, sy in enumerate((-1, 1)):
        row = BASE_CY + sy * 11.43
        refs.append(box(f"ref_base_socket_{i}", (dev_x[0] + 4, dev_x[1]), (row - 1.3, row + 1.3), (base_top, DEV_Z0), coll=coll))
        jrow = BASE_CY + sy * JUMPER_ROW
        refs.append(box(f"ref_base_jumpers_{i}", (BASE_CX - 25, BASE_CX + 25), (jrow - 1.3, jrow + 1.3), (base_top, base_top + JUMPER_H), coll=coll))
    refs.append(box("ref_dev_pcb", dev_x, dev_y, (DEV_Z0, DEV_Z1), coll=coll))
    refs.append(box("ref_dev_module", (dev_x[1] - 20, dev_x[1] + 5.4), (BASE_CY - 9, BASE_CY + 9), (DEV_Z1, DEV_Z1 + 3.2), coll=coll))
    refs.append(box("ref_dev_led", (LED_POS.x - 2.5, LED_POS.x + 2.5), (LED_POS.y - 2.5, LED_POS.y + 2.5), (DEV_Z1, LED_POS.z), coll=coll))
    for i, sy in enumerate((-1, 1)):
        y = BASE_CY + sy * USB_PITCH / 2
        refs.append(box(f"ref_usb_port_{i}", (DEV_X0 - 1.5, DEV_X0 + 6), (y - 4.5, y + 4.5), (DEV_Z1, DEV_Z1 + 3.2), coll=coll))
    y = BASE_CY + USB_PITCH / 2
    refs.append(box("ref_usb_plug", (DEV_X0 - 1.5 - 22, DEV_X0 - 1.5), (y - 6.2, y + 6.2), (USB_Z - 3.3, USB_Z + 3.3), coll=coll))
    return refs


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
    shell = build_shell(coll)
    bottom = build_bottom(coll)
    build_reference(ref)
    return shell, bottom


def print_matrix_shell():
    """Lays the shell face down on the bed (for STL export)."""
    return Matrix.Rotation(math.radians(180), 4, "Y") @ SLOPE_INV


if __name__ == "__main__":
    build()

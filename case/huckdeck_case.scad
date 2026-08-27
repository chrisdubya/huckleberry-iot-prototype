// huckdeck case — parametric two-part enclosure
// 6x 24mm arcade buttons (2 rows x 3 cols), 5mm RGB LED, Pi Zero 2 W inside.
//
// Render:  openscad -o top.stl  -D part=\"top\"  huckdeck_case.scad
//          openscad -o base.stl -D part=\"base\" huckdeck_case.scad
// part="both" shows an exploded preview in the GUI.

part = "both"; // top | base | both

/* ---- tune these to your printer ---- */
button_hole_d   = 24.4;  // 24mm buttons; add clearance if snug
led_hole_d      = 5.2;   // 5mm LED press-fit
screw_d         = 3.4;   // M3 clearance (case corner screws, button/cap head)
screw_head_d    = 6.4;   // counterbore for M3 button (5.7) / cap (5.5) heads
screw_head_h    = 2.0;
screw_boss_d    = 7;
case_pilot_d    = 2.5;   // M3 self-taps into the boss (use ~4.6 for M3 heat-set inserts)
pi_screw_d      = 2.2;   // M2.5 self-tap into Pi posts (Pi holes are 2.75mm; M3 won't fit)

/* ---- layout ---- */
cols            = 3;
rows            = 2;
button_pitch    = 36;    // center-to-center
margin          = 16;    // button center to inner wall (clears 28mm bezels vs corner screws)
wall            = 2.4;
top_t           = 3;     // top plate thickness
base_h          = 42;    // inner depth: button body ~32 + wire clearance
corner_r        = 6;

/* ---- Pi Zero 2 W ---- */
pi_l = 65; pi_w = 30;
pi_hole_dx = 58; pi_hole_dy = 23;  // mounting hole pattern
pi_post_h = 5;
// power-port slot in the rear wall; test-fit against the real board and
// mirror usb_slot_x if the ports land on the other side
usb_slot_x = -15;
usb_slot_w = 26;
usb_slot_h = 9;

/* ---- derived ---- */
inner_x = (cols - 1) * button_pitch + 2 * margin;
inner_y = (rows - 1) * button_pitch + 2 * margin;
outer_x = inner_x + 2 * wall;
outer_y = inner_y + 2 * wall;

$fn = 64;

module rounded_box(x, y, h, r) {
    linear_extrude(h)
        offset(r) offset(-r)
            square([x, y], center = true);
}

module button_centers() {
    for (cx = [0 : cols - 1], cy = [0 : rows - 1])
        translate([(cx - (cols - 1) / 2) * button_pitch,
                   (cy - (rows - 1) / 2) * button_pitch])
            children();
}

module corner_screw_centers() {
    for (sx = [-1, 1], sy = [-1, 1])
        translate([sx * (inner_x / 2 - screw_boss_d / 2 + 1),
                   sy * (inner_y / 2 - screw_boss_d / 2 + 1)])
            children();
}

/* ================= top plate ================= */
module top_plate() {
    difference() {
        union() {
            rounded_box(outer_x, outer_y, top_t, corner_r);
            // lip that registers inside the base walls (overlaps plate for one manifold)
            translate([0, 0, -3])
                difference() {
                    rounded_box(inner_x - 0.4, inner_y - 0.4, 3.5, corner_r - wall);
                    rounded_box(inner_x - 0.4 - 2 * wall, inner_y - 0.4 - 2 * wall, 3.5, corner_r - wall);
                }
        }
        button_centers()
            translate([0, 0, -4]) cylinder(d = button_hole_d, h = top_t + 8);
        // LED front-center between the rows
        translate([0, 0, -4]) cylinder(d = led_hole_d, h = top_t + 8);
        corner_screw_centers()
            translate([0, 0, -4]) cylinder(d = screw_d, h = top_t + 8);
        // counterbore so button/cap heads sit flush
        corner_screw_centers()
            translate([0, 0, top_t - screw_head_h]) cylinder(d = screw_head_d, h = screw_head_h + 1);
    }
}

/* ================= base ================= */
module pi_posts() {
    for (px = [-1, 1], py = [-1, 1])
        translate([px * pi_hole_dx / 2, py * pi_hole_dy / 2])
            difference() {
                cylinder(d = 6, h = pi_post_h);
                translate([0, 0, 1]) cylinder(d = pi_screw_d, h = pi_post_h);
            }
}

module base() {
    difference() {
        rounded_box(outer_x, outer_y, base_h, corner_r);
        translate([0, 0, wall])
            rounded_box(inner_x, inner_y, base_h, corner_r - wall);
        // micro-USB power slot through the rear wall (overmold passes through)
        translate([usb_slot_x - usb_slot_w / 2, outer_y / 2 - wall - 1, wall + pi_post_h - 1])
            cube([usb_slot_w, wall + 2, usb_slot_h]);
    }
    // Pi centered along the rear wall (clear of the corner bosses),
    // long edge with the ports 1mm from the wall, facing the slot
    translate([0, inner_y / 2 - pi_w / 2 - 1, wall - 0.01])
        pi_posts();
    // screw bosses (M2.5 self-tap)
    corner_screw_centers()
        translate([0, 0, wall - 0.01])
            difference() {
                cylinder(d = screw_boss_d, h = base_h - wall - 3.2);
                translate([0, 0, base_h - wall - 3.2 - 10])
                    cylinder(d = case_pilot_d, h = 11);
            }
}

/* ================= output ================= */
if (part == "top")  top_plate();
if (part == "base") base();
if (part == "both") {
    base();
    translate([0, 0, base_h + 15]) top_plate();
}

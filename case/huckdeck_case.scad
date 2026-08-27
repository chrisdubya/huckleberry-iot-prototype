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
screw_d         = 2.7;   // M2.5 free fit (case corner screws)
screw_boss_d    = 7;
pi_screw_d      = 2.2;   // M2.5 self-tap into post (or 2.7 + heat-set insert)

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
        // countersink the screw heads
        corner_screw_centers()
            translate([0, 0, top_t - 1.6]) cylinder(d1 = screw_d, d2 = 5.6, h = 1.7);
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
        // micro-USB power cutout, rear wall, near the Pi
        translate([-outer_x / 2 - 1, -pi_w / 2 - 8, wall + pi_post_h])
            cube([wall + 2, 12, 8]);
    }
    // Pi sits along the rear wall, ports facing the cutout
    translate([-inner_x / 2 + pi_w / 2 + 2, -inner_y / 2 + pi_l / 2 + 2, wall - 0.01])
        rotate([0, 0, 90]) pi_posts();
    // screw bosses (M2.5 self-tap)
    corner_screw_centers()
        translate([0, 0, wall - 0.01])
            difference() {
                cylinder(d = screw_boss_d, h = base_h - wall - 3.2);
                translate([0, 0, base_h - wall - 3.2 - 10])
                    cylinder(d = pi_screw_d, h = 11);
            }
}

/* ================= output ================= */
if (part == "top")  top_plate();
if (part == "base") base();
if (part == "both") {
    base();
    translate([0, 0, base_h + 15]) top_plate();
}

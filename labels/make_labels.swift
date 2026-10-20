// Generates button-cap label art for the Nelko P21 (14x40mm die-cut labels).
//
//   swift labels/make_labels.swift
//
// Each 14x40mm label carries two 14x14mm squares separated by dashed cut
// lines, so three labels cover all six buttons. The P21 is 1-bit thermal, so
// the color emoji are flattened to black/white: dark pixels stay black, light
// pixels go white, and a ring along the silhouette is always kept so pale
// emoji (bottle, faces) still get an outline.

import AppKit

let pxPerMM = 16.0                 // 2x the P21's 203 dpi (8 dots/mm)
let labelW = 40.0, labelH = 14.0   // mm, landscape as shown in the Nelko app
let square = 14.0                  // mm, cut size (full label width: cross-cuts only)
let iconSize = 10.0                // mm, stays inside the ~12mm printable width
let outlinePx = 5                  // silhouette ring kept black
let outDir = URL(fileURLWithPath: CommandLine.arguments[0])
    .deletingLastPathComponent().appendingPathComponent("out")

struct Icon { let name: String; let emoji: [(String, threshold: Double)] }

// Luma below the threshold prints black; tuned per emoji by eye.
let pee = ("💧", threshold: 1.01), poop = ("💩", threshold: 0.62)

// Button order matches docs/hardware.md (1-6).
let icons = [
    Icon(name: "1-pee", emoji: [pee]),
    Icon(name: "2-poop", emoji: [poop]),
    Icon(name: "3-both", emoji: [pee, poop]),
    Icon(name: "4-bottle", emoji: [("🍼", threshold: 0.82)]),
    Icon(name: "5-sleep", emoji: [("😴", threshold: 0.50)]),
    Icon(name: "6-nursing", emoji: [("🤱", threshold: 0.45)]),
]

func mm(_ v: Double) -> Int { Int((v * pxPerMM).rounded()) }

struct Bitmap {
    let w: Int, h: Int
    var black: [Bool]
    init(w: Int, h: Int) { self.w = w; self.h = h; black = Array(repeating: false, count: w * h) }

    mutating func blit(_ src: Bitmap, x: Int, y: Int) {
        for sy in 0..<src.h { for sx in 0..<src.w where src.black[sy * src.w + sx] {
            let dx = x + sx, dy = y + sy
            if dx >= 0, dx < w, dy >= 0, dy < h { black[dy * w + dx] = true }
        } }
    }

    func cgImage() -> CGImage {
        let bytes = black.map { $0 ? UInt8(0) : UInt8(255) }
        let provider = CGDataProvider(data: Data(bytes) as CFData)!
        return CGImage(width: w, height: h, bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: w,
                       space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: 0),
                       provider: provider, decode: nil, shouldInterpolate: false, intent: .defaultIntent)!
    }
}

func writePNG(_ image: CGImage, _ name: String) {
    let rep = NSBitmapImageRep(cgImage: image)
    let dpi = pxPerMM * 25.4
    rep.size = NSSize(width: Double(image.width) * 72 / dpi, height: Double(image.height) * 72 / dpi)
    try! rep.representation(using: .png, properties: [:])!.write(to: outDir.appendingPathComponent(name))
}

/// Renders one emoji and flattens it to 1-bit, cropped to its silhouette.
func render(_ emoji: String, fontPx: Double, threshold: Double) -> Bitmap {
    let side = Int(fontPx * 1.6)
    var data = [UInt8](repeating: 0, count: side * side * 4)
    let ctx = CGContext(data: &data, width: side, height: side, bitsPerComponent: 8, bytesPerRow: side * 4,
                        space: CGColorSpaceCreateDeviceRGB(),
                        bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
    NSGraphicsContext.current = NSGraphicsContext(cgContext: ctx, flipped: false)
    let text = NSAttributedString(string: emoji, attributes: [.font: NSFont(name: "Apple Color Emoji", size: fontPx)!])
    text.draw(at: NSPoint(x: fontPx * 0.2, y: fontPx * 0.3))
    ctx.flush()

    var solid = [Bool](repeating: false, count: side * side)
    var dark = solid
    for i in 0..<(side * side) {
        let a = Double(data[i * 4 + 3]) / 255
        guard a > 0.5 else { continue }
        solid[i] = true
        let r = Double(data[i * 4]) / 255 / a, g = Double(data[i * 4 + 1]) / 255 / a, b = Double(data[i * 4 + 2]) / 255 / a
        dark[i] = 0.299 * r + 0.587 * g + 0.114 * b < threshold
    }

    var out = Bitmap(w: side, h: side)
    var minX = side, minY = side, maxX = 0, maxY = 0
    for y in 0..<side { for x in 0..<side where solid[y * side + x] {
        var nearEdge = false
        scan: for dy in -outlinePx...outlinePx { for dx in -outlinePx...outlinePx {
            guard dx * dx + dy * dy <= outlinePx * outlinePx else { continue }
            let nx = x + dx, ny = y + dy
            if nx < 0 || ny < 0 || nx >= side || ny >= side || !solid[ny * side + nx] { nearEdge = true; break scan }
        } }
        out.black[y * side + x] = dark[y * side + x] || nearEdge
        minX = min(minX, x); maxX = max(maxX, x); minY = min(minY, y); maxY = max(maxY, y)
    } }

    var cropped = Bitmap(w: maxX - minX + 1, h: maxY - minY + 1)
    for y in 0..<cropped.h { for x in 0..<cropped.w {
        cropped.black[y * cropped.w + x] = out.black[(y + minY) * side + x + minX]
    } }
    return cropped
}

/// Renders an icon (one emoji, or two side by side) fitted to `iconSize`.
func makeIcon(_ icon: Icon) -> Bitmap {
    let target = Double(mm(iconSize))
    let gap = icon.emoji.count > 1 ? mm(0.4) : 0
    var fontPx = target / Double(icon.emoji.count)
    var parts: [Bitmap] = []
    for _ in 0..<2 {  // second pass corrects for the glyph's real extent
        parts = icon.emoji.map { render($0.0, fontPx: fontPx, threshold: $0.threshold) }
        let w = Double(parts.map(\.w).reduce(0, +) + gap * (parts.count - 1))
        let h = Double(parts.map(\.h).max()!)
        fontPx *= target / max(w, h)
    }
    let w = parts.map(\.w).reduce(0, +) + gap * (parts.count - 1), h = parts.map(\.h).max()!
    var out = Bitmap(w: w, h: h)
    var x = 0
    for p in parts { out.blit(p, x: x, y: (h - p.h) / 2); x += p.w + gap }
    return out
}

func makeLabel(_ pair: [Bitmap]) -> Bitmap {
    var label = Bitmap(w: mm(labelW), h: mm(labelH))
    let x0 = (labelW - square * Double(pair.count)) / 2
    for (i, icon) in pair.enumerated() {
        let cx = mm(x0 + square * (Double(i) + 0.5)), cy = mm(labelH / 2)
        label.blit(icon, x: cx - icon.w / 2, y: cy - icon.h / 2)
    }
    // Dashed cut lines at every square boundary.
    let lineW = mm(0.25), dash = mm(1.5), period = mm(2.25)
    for i in 0...pair.count {
        let x = mm(x0 + square * Double(i)) - lineW / 2
        for y in 0..<label.h where y % period < dash {
            for dx in 0..<lineW { label.black[y * label.w + x + dx] = true }
        }
    }
    return label
}

try! FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)

let bitmaps = icons.map(makeIcon)
for (icon, bmp) in zip(icons, bitmaps) {
    var tile = Bitmap(w: mm(12), h: mm(12))  // standalone icon on a 12mm printable square
    tile.blit(bmp, x: (tile.w - bmp.w) / 2, y: (tile.h - bmp.h) / 2)
    writePNG(tile.cgImage(), "icon-\(icon.name).png")
}

var labels: [Bitmap] = []
for i in stride(from: 0, to: icons.count, by: 2) {
    let label = makeLabel(Array(bitmaps[i..<min(i + 2, icons.count)]))
    labels.append(label)
    writePNG(label.cgImage(), "label-\(i / 2 + 1)-\(icons[i].name)+\(icons[i + 1].name).png")
}

// Preview sheet: the three labels with their die-cut outline, for eyeballing only.
let pad = mm(3), sheetW = mm(labelW) + pad * 2, sheetH = (mm(labelH) + pad) * labels.count + pad
let sheet = CGContext(data: nil, width: sheetW, height: sheetH, bitsPerComponent: 8, bytesPerRow: 0,
                      space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)!
sheet.setFillColor(CGColor(gray: 0.85, alpha: 1))
sheet.fill(CGRect(x: 0, y: 0, width: sheetW, height: sheetH))
for (i, label) in labels.enumerated() {
    let rect = CGRect(x: pad, y: sheetH - (pad + mm(labelH)) * (i + 1), width: mm(labelW), height: mm(labelH))
    sheet.saveGState()
    sheet.addPath(CGPath(roundedRect: rect, cornerWidth: Double(mm(2)), cornerHeight: Double(mm(2)), transform: nil))
    sheet.clip()
    sheet.draw(label.cgImage(), in: rect)
    sheet.restoreGState()
}
writePNG(sheet.makeImage()!, "preview.png")
print("Wrote \(icons.count) icons, \(labels.count) labels and preview.png to \(outDir.path)")

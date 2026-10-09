import AppKit

/// Template menu bar glyph "•|•": Codex dot, mirror axis, Claude dot (+ count).
struct IconState: Equatable {
    var syncing = false
    var codexBlocked = false
    var claudeBlocked = false
    var count = 0
    var stale = false
    var error = false
    var paused = false
}

@MainActor
final class StatusIconAnimator {
    private weak var button: NSStatusBarButton?
    private var state = IconState()
    private var phase: Double = 0          // 0..<1 revolution
    private var timer: Timer?

    init(button: NSStatusBarButton) {
        self.button = button
        render()
    }

    func update(_ new: IconState) {
        guard new != state else { return }
        state = new
        if state.syncing { startTimer() }
        render()
    }

    private func startTimer() {
        guard timer == nil else { return }
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 20, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.tick() }
        }
    }

    private func tick() {
        phase += 1.0 / 24
        if phase >= 1 {
            phase = 0
            // finish the revolution before resting, never snap mid-orbit
            if !state.syncing { timer?.invalidate(); timer = nil }
        }
        render()
    }

    private func render() {
        guard let button else { return }
        button.image = Self.image(state, phase: timer == nil ? 0 : phase)
        button.appearsDisabled = state.paused
        var parts: [String] = []
        if state.count > 0 { parts.append("ждут \(state.count)") }
        if state.error { parts.append("ошибка") }
        if state.syncing { parts.append("синхронизация") }
        button.setAccessibilityLabel("Mirror" + (parts.isEmpty ? "" : ": " + parts.joined(separator: ", ")))
        button.toolTip = parts.isEmpty ? "Mirror · всё синхронизировано" : "Mirror · " + parts.joined(separator: " · ")
    }

    /// Debug: all states side by side, 4x, black on white.
    static func preview(to path: String) {
        let states: [(IconState, Double)] = [
            (IconState(), 0), (IconState(syncing: true), 0.12), (IconState(syncing: true), 0.3),
            (IconState(codexBlocked: true, count: 3), 0), (IconState(claudeBlocked: true, count: 1), 0),
            (IconState(codexBlocked: true, claudeBlocked: true, count: 12, stale: true), 0),
            (IconState(claudeBlocked: true, count: 2, error: true), 0), (IconState(paused: true), 0),
        ]
        let imgs = states.map { image($0.0, phase: $0.1) }
        let w = imgs.reduce(0) { $0 + $1.size.width + 14 } + 14
        let out = NSImage(size: NSSize(width: w * 4, height: 26 * 4), flipped: false) { r in
            NSColor.white.setFill(); r.fill()
            var x: CGFloat = 14
            for i in imgs {
                let tinted = NSImage(size: i.size, flipped: false) { rr in
                    i.draw(in: rr); NSColor.black.set(); rr.fill(using: .sourceAtop); return true
                }
                tinted.draw(in: NSRect(x: x * 4, y: 4 * 4, width: i.size.width * 4, height: i.size.height * 4))
                x += i.size.width + 14
            }
            return true
        }
        if let tiff = out.tiffRepresentation, let rep = NSBitmapImageRep(data: tiff),
           let png = rep.representation(using: .png, properties: [:]) {
            try? png.write(to: URL(fileURLWithPath: path))
        }
    }

    static func image(_ s: IconState, phase: Double) -> NSImage {
        let text = s.count > 0 ? "\(s.error ? "! " : "")\(s.count)" : (s.error ? "!" : "")
        let font = NSFont.monospacedDigitSystemFont(ofSize: 11, weight: .semibold)
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.black]
        let textSize = (text as NSString).size(withAttributes: attrs)
        let boxed = s.stale && s.count > 0
        let textW = text.isEmpty ? 0 : ceil(textSize.width) + (boxed ? 8 : 0) + 3
        let size = NSSize(width: 18 + textW, height: 18)

        let img = NSImage(size: size, flipped: true) { _ in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            ctx.setFillColor(NSColor.black.cgColor)
            ctx.setStrokeColor(NSColor.black.cgColor)

            // axis
            ctx.setLineWidth(1.5)
            ctx.setLineCap(.round)
            if s.error {
                ctx.move(to: CGPoint(x: 9, y: 3)); ctx.addLine(to: CGPoint(x: 9, y: 7.5))
                ctx.move(to: CGPoint(x: 9, y: 10.5)); ctx.addLine(to: CGPoint(x: 9, y: 15))
            } else {
                ctx.move(to: CGPoint(x: 9, y: 3)); ctx.addLine(to: CGPoint(x: 9, y: 15))
            }
            if s.paused { ctx.setLineDash(phase: 0, lengths: [2, 2]) }
            ctx.strokePath()
            ctx.setLineDash(phase: 0, lengths: [])

            // dots: at rest left/right; while syncing they orbit the axis on an ellipse, 180° apart
            let a = phase * 2 * .pi
            let dots: [(CGPoint, Bool, Double)] = [
                (CGPoint(x: 9 - 5 * cos(a), y: 9 - 2.2 * sin(a)), s.codexBlocked, sin(a) > 0.2 ? 0.45 : 1),
                (CGPoint(x: 9 + 5 * cos(a), y: 9 + 2.2 * sin(a)), s.claudeBlocked, sin(a) < -0.2 ? 0.45 : 1),
            ]
            for (c, hollow, alpha) in dots {
                ctx.setAlpha(alpha)
                let r = CGRect(x: c.x - 2.5, y: c.y - 2.5, width: 5, height: 5)
                if hollow {
                    ctx.setLineWidth(1.25)
                    ctx.strokeEllipse(in: r.insetBy(dx: 0.6, dy: 0.6))
                } else {
                    ctx.fillEllipse(in: r)
                }
            }
            ctx.setAlpha(1)

            // count, knocked out of a filled box once something waited past the threshold
            if !text.isEmpty {
                let x = 21.0
                if boxed {
                    let box = CGRect(x: x, y: 2, width: textW - 3, height: 14)
                    ctx.addPath(CGPath(roundedRect: box, cornerWidth: 4, cornerHeight: 4, transform: nil))
                    ctx.fillPath()
                    ctx.setBlendMode(.destinationOut)
                    (text as NSString).draw(at: CGPoint(x: x + 4, y: 9 - textSize.height / 2), withAttributes: attrs)
                    ctx.setBlendMode(.normal)
                } else {
                    (text as NSString).draw(at: CGPoint(x: x, y: 9 - textSize.height / 2), withAttributes: attrs)
                }
            }
            return true
        }
        img.isTemplate = true
        return img
    }
}

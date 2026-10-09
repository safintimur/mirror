import AppKit
import SwiftUI

enum Theme {
    static let width: CGFloat = 360
    static let height: CGFloat = 460
    static let radius: CGFloat = 24
    static let rowHeight: CGFloat = 56
    static let avatar: CGFloat = 40
    static let rail: CGFloat = 64          // text rail x
    static let gutter: CGFloat = 16
    static let staleAfter: TimeInterval = 30 * 60

    /// "Штиль": cold still-water palette — ice accent, monochrome primary, cool slate glass.
    static let accent = Color(light: 0x0A7CC2, dark: 0x6CCBFF)
    static let primaryFill = Color(light: 0x1C1C1E, dark: 0xF2F4F7)
    static let onPrimary = Color(light: 0xFFFFFF, dark: 0x16181C)
    static let tint = Color(light: 0xF4F7FA, dark: 0x262B33)
    static let avatarRadius: CGFloat = 0.3   // squircle, fraction of size
    static let chipFill = Color.primary.opacity(0.08)
    static let hoverFill = Color.primary.opacity(0.06)
    static let separator = Color.primary.opacity(0.08)

    /// Two-letter monogram gradients, picked by a stable hash of the project name.
    static let gradients: [(UInt32, UInt32)] = [
        (0x5E5CE6, 0x3A39B0), (0x40C8E0, 0x1E8FA8), (0xFF6482, 0xC23B5C), (0xD9A066, 0xA86F3A),
        (0x7DC87A, 0x3F8F4A), (0x8E9AAF, 0x586274), (0xBF5AF2, 0x7D2FB0), (0x64B5FF, 0x2F7BD6),
    ]

    static func gradient(for key: String) -> LinearGradient {
        var h: UInt32 = 2166136261
        for b in key.utf8 { h = (h ^ UInt32(b)) &* 16777619 }
        let (a, b) = gradients[Int(h % UInt32(gradients.count))]
        return LinearGradient(colors: [Color(hex: a), Color(hex: b)], startPoint: .topLeading, endPoint: .bottomTrailing)
    }

    static func monogram(_ project: String) -> String {
        let letters = project.filter { $0.isLetter || $0.isNumber }
        guard let first = letters.first else { return "·" }
        let second = letters.dropFirst().first.map { String($0).lowercased() } ?? ""
        return String(first).uppercased() + second
    }
}

extension Color {
    init(hex: UInt32, opacity: Double = 1) {
        self.init(.sRGB, red: Double((hex >> 16) & 0xFF) / 255, green: Double((hex >> 8) & 0xFF) / 255,
                  blue: Double(hex & 0xFF) / 255, opacity: opacity)
    }

    init(light: UInt32, dark: UInt32) {
        self.init(nsColor: NSColor(name: nil) { appearance in
            let hex = appearance.bestMatch(from: [.darkAqua, .aqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed: CGFloat((hex >> 16) & 0xFF) / 255, green: CGFloat((hex >> 8) & 0xFF) / 255,
                           blue: CGFloat(hex & 0xFF) / 255, alpha: 1)
        })
    }
}

enum Format {
    /// "12с", "42м", "1ч 5м", "2д".
    static func age(_ date: Date, now: Date = .now) -> String {
        let s = max(0, Int(now.timeIntervalSince(date)))
        switch s {
        case ..<60: return "\(s)с"
        case ..<3600: return "\(s / 60)м"
        case ..<86400:
            let m = (s % 3600) / 60
            return m == 0 ? "\(s / 3600)ч" : "\(s / 3600)ч \(m)м"
        default: return "\(s / 86400)д"
        }
    }

    static func threads(_ n: Int) -> String { "\(n) \(plural(n, "тред", "треда", "тредов"))" }
    static func turns(_ n: Int) -> String { "\(n) \(plural(n, "ход", "хода", "ходов"))" }
    static func sessions(_ n: Int) -> String { "\(n) \(plural(n, "сессия", "сессии", "сессий"))" }

    static func plural(_ n: Int, _ one: String, _ few: String, _ many: String) -> String {
        let m10 = n % 10, m100 = n % 100
        if m10 == 1 && m100 != 11 { return one }
        if (2...4).contains(m10) && !(12...14).contains(m100) { return few }
        return many
    }
}

/// Behind-window blur for the panel body (the glass itself is reserved for controls).
struct PanelBackground: NSViewRepresentable {
    func makeNSView(context: Context) -> NSVisualEffectView {
        let v = NSVisualEffectView()
        v.material = .hudWindow
        v.blendingMode = .behindWindow
        v.state = .active
        return v
    }

    func updateNSView(_ v: NSVisualEffectView, context: Context) {}
}

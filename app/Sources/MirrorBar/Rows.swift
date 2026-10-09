import AppKit
import SwiftUI

// MARK: Avatar + status dot

struct Avatar: View {
    let project: String
    var size: CGFloat = Theme.avatar

    var body: some View {
        let shape = RoundedRectangle(cornerRadius: size * Theme.avatarRadius, style: .continuous)
        shape
            .fill(Theme.gradient(for: project))
            .overlay(
                Text(Theme.monogram(project))
                    .font(.system(size: size * 0.37, weight: .semibold, design: .rounded))
                    .foregroundStyle(.white)
            )
            .frame(width: size, height: size)
            .overlay(shape.strokeBorder(.white.opacity(0.10), lineWidth: 0.5))
    }
}

enum DotStyle: Equatable { case none, ring, solid(stale: Bool), working, error }

struct StatusDot: View {
    let style: DotStyle
    @State private var halo = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack {
            switch style {
            case .none:
                EmptyView()
            case .ring:
                Circle().strokeBorder(Color.secondary.opacity(0.7), lineWidth: 1.5)
            case let .solid(stale):
                Circle().fill(stale ? Color.orange : Color(nsColor: .secondaryLabelColor))
            case .working:
                Circle().fill(Color.green.opacity(0.35))
                    .scaleEffect(halo ? 1.9 : 1).opacity(halo ? 0 : 1)
                Circle().fill(Color.green)
            case .error:
                Circle().fill(Color.red)
            }
        }
        .frame(width: 11, height: 11)
        .background(style == .none ? nil : Circle().fill(Theme.tint).padding(-2))
        .onAppear {
            guard style == .working, !reduceMotion else { return }
            withAnimation(.easeOut(duration: 1.6).repeatForever(autoreverses: false)) { halo = true }
        }
    }
}

// MARK: Chip

struct Chip: View {
    let symbol: String
    let text: String
    var tint: Color = .secondary
    var danger = false
    var spin = false
    var breathe = false

    var body: some View {
        HStack(spacing: 4) {
            Image(systemName: symbol)
                .font(.system(size: 9, weight: .semibold))
                .foregroundStyle(danger ? .red : tint)
                .symbolEffect(.rotate, options: .repeat(.continuous), isActive: spin)
                .symbolEffect(.breathe, options: .repeat(.continuous), isActive: breathe)
            Text(text)
                .font(.system(size: 11, weight: .semibold))
                .foregroundStyle(danger ? Color.red : Color.primary.opacity(0.9))
                .lineLimit(1)
        }
        .padding(.horizontal, 7)
        .frame(height: 18)
        .background(Capsule().fill(danger ? Color.red.opacity(0.16) : Theme.chipFill))
    }
}

extension SyncThread {
    var stale: Bool { Date.now.timeIntervalSince(updated) > Theme.staleAfter }

    var dot: DotStyle {
        switch state {
        case .synced, .archived, .unknown: .none
        case .pending: .ring
        case .waitingCodex, .waitingClaude: .solid(stale: stale)
        case .workingCodex, .workingClaude: .working
        case .error: .error
        }
    }

    @ViewBuilder var chip: some View {
        switch state {
        case .waitingCodex:
            Chip(symbol: "lock.fill", text: "→ Codex \(toCodex)")
                .help("Codex держит тред до выхода. Ничего не потеряно.")
        case .waitingClaude:
            Chip(symbol: "lock.fill", text: "→ Claude \(toClaudeTurns)")
                .help("Сессия открыта в Claude — перенесём после закрытия.")
        case .workingClaude:
            Chip(symbol: "asterisk", text: "идёт в Claude", tint: .green, spin: true)
        case .workingCodex:
            Chip(symbol: "terminal", text: "идёт в Codex", tint: .green, breathe: true)
        case .error:
            Chip(symbol: "exclamationmark.triangle.fill", text: "ошибка", danger: true)
        case .pending:
            if !paired {
                Chip(symbol: "plus", text: origin == .claude ? "новый → Codex" : "новый → Claude")
            } else {
                Chip(symbol: "arrow.triangle.2.circlepath", text: "в очереди")
            }
        default:
            EmptyView()
        }
    }

    /// Direction tokens next to the chip, only for queues that are not already named by the chip.
    var directions: String {
        var parts: [String] = []
        if toCodex > 0 && state != .waitingCodex { parts.append("← \(toCodex)") }
        if toClaudeTurns > 0 && state != .waitingClaude { parts.append("→ \(toClaudeTurns)") }
        return parts.joined(separator: "  ")
    }
}

// MARK: Thread row

struct ThreadRow: View {
    let thread: SyncThread
    var showSeparator = true
    @Environment(Store.self) private var store
    @State private var hover = false

    private var quiet: Bool { thread.state == .synced || thread.state == .archived }

    var body: some View {
        HStack(spacing: 0) {
            Avatar(project: thread.project)
                .overlay(alignment: .bottomTrailing) { StatusDot(style: thread.dot).offset(x: 1, y: 1) }
                .frame(width: Theme.rail - Theme.gutter, alignment: .leading)
            VStack(alignment: .leading, spacing: 3) {
                // Firstlight keeps "name · city" on one line; drop the meta when the name needs the room.
                ViewThatFits(in: .horizontal) {
                    HStack(spacing: 8) {
                        title.fixedSize()
                        Label(thread.project, systemImage: "folder").labelStyle(MetaLabelStyle()).lineLimit(1).fixedSize()
                    }
                    title
                }
                if !quiet {
                    HStack(spacing: 8) {
                        thread.chip
                        if thread.state == .error, let err = thread.error {
                            Text(err).font(.system(size: 12)).foregroundStyle(.red.opacity(0.85)).lineLimit(1)
                        } else if !thread.directions.isEmpty {
                            Text(thread.directions)
                                .font(.system(size: 11, weight: .medium).monospacedDigit())
                                .foregroundStyle(.secondary)
                        }
                    }
                }
            }
            Spacer(minLength: 8)
            trailing
        }
        .padding(.leading, Theme.gutter)
        .padding(.trailing, Theme.gutter)
        .frame(height: Theme.rowHeight)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
            .fill(hover ? Theme.hoverFill : .clear).padding(.horizontal, 6))
        .overlay(alignment: .bottom) {
            if showSeparator && !hover {
                Rectangle().fill(Theme.separator).frame(height: 0.5)
                    .padding(.leading, Theme.rail).padding(.trailing, Theme.gutter)
            }
        }
        .contentShape(Rectangle())
        .onHover { h in withAnimation(.easeOut(duration: 0.12)) { hover = h } }
        .onTapGesture { store.open(thread, in: thread.preferredApp) }
        .help(thread.title)
        .accessibilityElement(children: .combine)
    }

    private var title: some View {
        Text(thread.title)
            .font(.system(size: 13.5, weight: quiet ? .medium : .semibold))
            .lineLimit(1)
    }

    @ViewBuilder private var trailing: some View {
        if hover {
            HStack(spacing: 6) {
                OpenButton(provider: .codex, enabled: thread.threadId != nil && thread.codexVisible) {
                    store.open(thread, in: .codex)
                }
                OpenButton(provider: .claude, enabled: thread.claudeSession != nil) {
                    store.open(thread, in: .claude)
                }
            }
            .transition(.opacity.combined(with: .blurReplace))
        } else {
            TimelineView(.periodic(from: .now, by: 30)) { ctx in
                Text(Format.age(thread.updated, now: ctx.date))
                    .font(.system(size: quiet ? 13 : 15, weight: quiet ? .medium : .semibold).monospacedDigit())
                    .foregroundStyle(valueStyle)
                    .contentTransition(.numericText())
            }
            .transition(.opacity)
        }
    }

    private var valueStyle: Color {
        if thread.state == .error { return .red.opacity(0.8) }
        if (thread.state == .waitingCodex || thread.state == .waitingClaude) && thread.stale { return .orange }
        return quiet ? Color(nsColor: .tertiaryLabelColor) : Color(nsColor: .secondaryLabelColor)
    }
}

struct MetaLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 3) {
            configuration.icon.font(.system(size: 10))
            configuration.title.font(.system(size: 12))
        }
        .foregroundStyle(.secondary)
    }
}

struct OpenButton: View {
    let provider: Provider
    let enabled: Bool
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            Image(nsImage: AppIcons.icon(provider))
                .resizable()
                .frame(width: 16, height: 16)
                .frame(width: 26, height: 26)
        }
        .buttonStyle(.plain)
        .glassEffect(.regular.interactive(), in: .circle)
        .disabled(!enabled)
        .opacity(enabled ? 1 : 0.35)
        .help("Открыть в \(provider.displayName)")
    }
}

@MainActor
enum AppIcons {
    private static var cache: [Provider: NSImage] = [:]
    static func icon(_ p: Provider) -> NSImage {
        if let i = cache[p] { return i }
        let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: p.bundleId)
        let i = url.map { NSWorkspace.shared.icon(forFile: $0.path) } ?? NSImage()
        cache[p] = i
        return i
    }
}

// MARK: Section header, quiet line, "more" row

struct SectionHeader: View {
    let title: String
    let count: Int
    var action: (label: String, symbol: String, run: () -> Void)?

    var body: some View {
        HStack(spacing: 5) {
            Text(title).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
            Text("\(count)").font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundStyle(.tertiary)
                .contentTransition(.numericText())
            Spacer()
            if let action {
                Button(action: action.run) {
                    Label(action.label, systemImage: action.symbol)
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundStyle(Theme.accent)
                }
                .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, Theme.gutter)
        .frame(height: 30)
    }
}

struct QuietLine<Trailing: View>: View {
    let symbol: String
    let tint: Color
    var spin = false
    let text: Text
    @ViewBuilder var trailing: Trailing
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            HStack(spacing: 0) {
                Circle().fill(Color.primary.opacity(0.06))
                    .overlay(Image(systemName: symbol).font(.system(size: 12, weight: .semibold)).foregroundStyle(tint)
                        .symbolEffect(.rotate, options: .repeat(.continuous), isActive: spin))
                    .frame(width: 28, height: 28)
                    .frame(width: Theme.rail - Theme.gutter, alignment: .leading)
                    .padding(.leading, 6)
                text.font(.system(size: 13, weight: .medium)).foregroundStyle(.primary.opacity(0.85))
                Spacer(minLength: 8)
                trailing
            }
            .padding(.horizontal, Theme.gutter)
            .frame(height: 44)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(hover ? Theme.hoverFill : .clear).padding(.horizontal, 6))
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { h in withAnimation(.easeOut(duration: 0.12)) { hover = h } }
    }
}

struct AvatarStack: View {
    let projects: [String]
    var body: some View {
        HStack(spacing: -7) {
            ForEach(Array(projects.prefix(3).enumerated()), id: \.offset) { _, p in
                Avatar(project: p, size: 20)
                    .overlay(RoundedRectangle(cornerRadius: 6, style: .continuous).strokeBorder(Theme.tint, lineWidth: 1.5))
            }
        }
    }
}

extension SyncThread {
    /// Where a click opens the thread: the side that is working, else Codex when it knows the thread.
    var preferredApp: Provider {
        if state == .workingClaude || state == .waitingClaude { return .claude }
        return threadId != nil && codexVisible ? .codex : .claude
    }
}

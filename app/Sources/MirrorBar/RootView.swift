import SwiftUI

struct RootView: View {
    @Environment(Store.self) private var store

    var body: some View {
        if store.panelVisible {
            PanelContent()
        } else {
            // hidden panel: no views, no animations, no CPU
            Color.clear.frame(width: Theme.width, height: Theme.height)
        }
    }
}

private struct PanelContent: View {
    @Environment(Store.self) private var store
    @Namespace private var ns

    var body: some View {
        ZStack {
            Group {
                if store.showSettings {
                    SettingsView().transition(.blurReplace)
                } else {
                    ScrollView {
                        LazyVStack(spacing: 0) {
                            if store.searching && !store.query.isEmpty {
                                SearchResults()
                            } else if store.tab == .attention {
                                AttentionList()
                            } else {
                                AllList()
                            }
                        }
                        .padding(.vertical, 6)
                        .animation(.spring(response: 0.35, dampingFraction: 0.88), value: store.snapshot)
                    }
                    .scrollEdgeEffectStyle(.soft, for: [.top, .bottom])
                    .scrollBounceBehavior(.basedOnSize)
                    .transition(.blurReplace)
                }
            }
            .safeAreaBar(edge: .top) { Header(ns: ns) }
            .safeAreaBar(edge: .bottom) { StatusBar() }

            if let r = store.release, r.phase == .confirm {
                ReleaseSheet(release: r).transition(.opacity)
            }
        }
        .frame(width: Theme.width, height: Theme.height)
        .background {
            PanelBackground()
            Theme.tint.opacity(0.6)
        }
        .clipShape(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous))
        .overlay(RoundedRectangle(cornerRadius: Theme.radius, style: .continuous)
            .strokeBorder(.white.opacity(0.10), lineWidth: 1))
        .animation(.spring(response: 0.34, dampingFraction: 0.86), value: store.release)
        .animation(.smooth(duration: 0.22), value: store.showSettings)
        .background(Shortcuts())
        .focusEffectDisabled()
    }
}

// MARK: Header — segmented switch on the left, one glass capsule with search + settings on the right

private struct Header: View {
    @Environment(Store.self) private var store
    let ns: Namespace.ID
    @FocusState private var searchFocused: Bool

    var body: some View {
        @Bindable var store = store
        GlassEffectContainer(spacing: 10) {
            HStack(spacing: 8) {
                if store.showSettings {
                    Text("Настройки").font(.system(size: 14, weight: .semibold)).padding(.leading, 8)
                    Spacer()
                    toolCapsule(settingsOnly: true)
                } else if store.searching {
                    HStack(spacing: 6) {
                        Image(systemName: "magnifyingglass").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                        TextField("Найти тред", text: $store.query)
                            .textFieldStyle(.plain)
                            .font(.system(size: 13))
                            .focused($searchFocused)
                            .onExitCommand { closeSearch() }
                        Button { closeSearch() } label: {
                            Image(systemName: "xmark.circle.fill").foregroundStyle(.tertiary)
                        }
                        .buttonStyle(.plain)
                    }
                    .padding(.horizontal, 12)
                    .frame(height: 32)
                    .glassEffect(.regular, in: .capsule)
                    .glassEffectID("search", in: ns)
                    .onAppear { searchFocused = true }
                } else {
                    Segmented(ns: ns)
                    Spacer(minLength: 0)
                    toolCapsule(settingsOnly: false)
                }
            }
        }
        .padding(.horizontal, 10)
        .padding(.top, 10)
        .padding(.bottom, 4)
    }

    private func toolCapsule(settingsOnly: Bool) -> some View {
        HStack(spacing: 0) {
            if !settingsOnly {
                Button { withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) { store.searching = true } } label: {
                    Image(systemName: "magnifyingglass").font(.system(size: 12.5, weight: .medium)).frame(width: 30, height: 32)
                }
                .buttonStyle(.plain)
                .help("Найти тред (⌘F)")
            }
            Button { store.showSettings.toggle() } label: {
                Image(systemName: store.showSettings ? "xmark" : "slider.horizontal.3")
                    .font(.system(size: 12.5, weight: .medium))
                    .contentTransition(.symbolEffect(.replace))
                    .frame(width: 30, height: 32)
            }
            .buttonStyle(.plain)
            .help(store.showSettings ? "Закрыть настройки" : "Настройки (⌘,)")
        }
        .padding(.horizontal, 3)
        .glassEffect(.regular.interactive(), in: .capsule)
        .glassEffectID("tools", in: ns)
    }

    private func closeSearch() {
        withAnimation(.spring(response: 0.3, dampingFraction: 0.85)) {
            store.query = ""
            store.searching = false
        }
    }
}

/// One glass capsule with a sliding inner highlight (not Firstlight's floating text tabs).
private struct Segmented: View {
    @Environment(Store.self) private var store
    let ns: Namespace.ID

    var body: some View {
        HStack(spacing: 0) {
            segment(.attention, "Внимание", store.attentionCount, badge: true)
            segment(.all, "Все", store.threads.count, badge: false)
        }
        .padding(3)
        .glassEffect(.regular, in: .capsule)
        .glassEffectID("segmented", in: ns)
    }

    private func segment(_ tab: Store.Tab, _ title: String, _ count: Int, badge: Bool) -> some View {
        let selected = store.tab == tab
        return Button {
            withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) { store.tab = tab }
        } label: {
            HStack(spacing: 6) {
                Text(title)
                    .font(.system(size: 12.5, weight: selected ? .semibold : .medium))
                    .foregroundStyle(selected ? .primary : .secondary)
                if count > 0 {
                    Text("\(count)")
                        .font(.system(size: 11, weight: .semibold).monospacedDigit())
                        .foregroundStyle(badge ? Theme.onPrimary : Color.secondary)
                        .padding(.horizontal, badge ? 6 : 0)
                        .frame(minWidth: badge ? 18 : 0, minHeight: 16)
                        .background { if badge { Capsule().fill(Theme.accent) } }
                        .contentTransition(.numericText())
                        .transition(.blurReplace)
                }
            }
            .padding(.horizontal, 12)
            .frame(height: 26)
            .background {
                if selected {
                    Capsule().fill(Color.primary.opacity(0.12))
                        .matchedGeometryEffect(id: "segment", in: ns)
                }
            }
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
    }
}

// MARK: Status bar — one glass bar: live glyph + status on the left, the action inside it

private struct StatusBar: View {
    @Environment(Store.self) private var store

    var body: some View {
        HStack(spacing: 10) {
            LiveGlyph(active: spinning)
                .frame(width: 26, height: 18)
                .padding(.leading, 6)
            TimelineView(.periodic(from: .now, by: 1)) { ctx in
                let (top, bottom) = lines(now: ctx.date)
                VStack(alignment: .leading, spacing: 1) {
                    Text(top).font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(isFailure ? Color.red : Color.primary)
                    if let bottom {
                        Text(bottom).font(.system(size: 11).monospacedDigit()).foregroundStyle(.secondary)
                            .contentTransition(.numericText())
                    }
                }
                .lineLimit(1)
            }
            Spacer(minLength: 4)
            ActionPill()
        }
        .padding(5)
        .frame(height: 46)
        .glassEffect(.regular, in: .capsule)
        .padding(.horizontal, 10)
        .padding(.bottom, 10)
        .padding(.top, 4)
    }

    private var isFailure: Bool { if case .failed = store.release?.phase { true } else { false } }

    private var spinning: Bool {
        if store.syncing { return true }
        switch store.release?.phase {
        case .quitting?, .syncing?, .relaunching?: return true
        default: return false
        }
    }

    private func lines(now: Date) -> (String, String?) {
        if let r = store.release, r.phase != .confirm {
            let name = r.names
            switch r.phase {
            case .waiting:
                let n = r.working.values.reduce(0) { $0 + $1.count }
                return ("Жду \(Format.turns(max(1, n))) в \(name)", "потом перезапущу и перенесу")
            case let .countdown(n): return ("\(name) закроется через \(n) с", "можно отменить")
            case .quitting: return ("Закрываю \(name)…", nil)
            case .syncing: return ("Переношу очередь…", nil)
            case .relaunching: return ("Открываю \(name)…", nil)
            case let .done(text): return ("Готово", text)
            case let .failed(text): return (text, "можно завершить принудительно")
            case .confirm: break
            }
        }
        if store.syncing { return ("Синхронизирую…", nil) }
        let need = store.needsRelease
        if !need.isEmpty {
            let apps = store.snapshot.apps
            let parts = need.map { p -> String in
                let blocked = (p == .codex ? apps.codex.blocked : apps.claude.blocked) ?? 0
                return blocked > 0 ? "\(p.displayName) · \(Format.turns(blocked))" : "\(p.displayName) · обновить список"
            }
            return ("Ждёт перезапуска", parts.joined(separator: ", "))
        }
        let hints = store.restartHints
        if !hints.isEmpty && !store.syncing {
            let names = hints.map(\.displayName).joined(separator: " и ")
            return ("Обновлено · \(store.lastOk.map { Format.age($0, now: now) } ?? "—")",
                    "\(names): изменения при следующем запуске")
        }
        if !store.autoSync {
            return ("Автосинхронизация выкл.", store.lastOk.map { "обновлено \(Format.age($0, now: now)) назад" }
                ?? "первый запуск — по кнопке")
        }
        let last = store.lastOk.map { "Обновлено · \(Format.age($0, now: now))" } ?? "Ждёт первого цикла"
        let next = store.nextTickAt.map { max(0, Int($0.timeIntervalSince(now))) }
            .map { String(format: "далее через %d:%02d", $0 / 60, $0 % 60) }
        return (last, next)
    }
}

/// Monochrome primary inside the status bar; turns into «Отменить» / «Принудительно» during a release.
private struct ActionPill: View {
    @Environment(Store.self) private var store
    @State private var justDone = false

    var body: some View {
        Button(action: act) {
            HStack(spacing: 4) {
                Image(systemName: symbol)
                    .font(.system(size: 11, weight: .bold))
                    .contentTransition(.symbolEffect(.replace))
                Text(label).font(.system(size: 12, weight: .semibold))
                    .lineLimit(1)
                    .fixedSize()
            }
            .foregroundStyle(destructive ? Color.white : Theme.onPrimary)
            .padding(.horizontal, 10)
            .frame(height: 34)
            .background(Capsule().fill(destructive ? Color.red : Theme.primaryFill))
            .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .opacity(disabled ? 0.55 : 1)
        .disabled(disabled)
        .animation(.smooth(duration: 0.25), value: label)
        .onChange(of: store.syncing) { was, now in
            guard was, !now else { return }
            justDone = true
            Task { try? await Task.sleep(for: .seconds(1.2)); justDone = false }
        }
    }

    private var phase: Store.ReleasePhase? { store.release.flatMap { $0.phase == .confirm ? nil : $0.phase } }
    private var destructive: Bool { if case .failed = phase { true } else { false } }
    private var disabled: Bool {
        if let phase { return !phase.cancellable && !destructive }
        return store.syncing
    }

    private var symbol: String {
        if let phase {
            if destructive { return "bolt.fill" }
            return phase.cancellable ? "xmark" : "arrow.triangle.2.circlepath"
        }
        if !store.needsRelease.isEmpty { return "arrow.clockwise" }
        return justDone ? "checkmark" : "arrow.triangle.2.circlepath"
    }

    private var label: String {
        if let phase {
            if destructive { return "Принудительно" }
            return phase.cancellable ? "Отменить" : "Идёт…"
        }
        if !store.needsRelease.isEmpty { return "Перезапустить" }
        return store.lastOk == nil ? "Начать" : "Синхронизировать"
    }

    private func act() {
        if destructive { store.releaseNow(); return }
        if let phase, phase.cancellable { store.cancelRelease(); return }
        Task { await store.syncAll() }
    }
}

/// The brand glyph •|• — dots orbit the mirror axis while work is moving.
struct LiveGlyph: View {
    var active: Bool
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        TimelineView(.animation(paused: !active || reduceMotion)) { ctx in
            Canvas { g, size in
                let t = active ? ctx.date.timeIntervalSinceReferenceDate.truncatingRemainder(dividingBy: 1.2) / 1.2 : 0
                let a = t * 2 * .pi
                let c = CGPoint(x: size.width / 2, y: size.height / 2)
                var axis = Path()
                axis.move(to: CGPoint(x: c.x, y: 2)); axis.addLine(to: CGPoint(x: c.x, y: size.height - 2))
                g.stroke(axis, with: .color(.primary.opacity(0.85)), style: StrokeStyle(lineWidth: 1.6, lineCap: .round))
                let rx = size.width * 0.34, ry = size.height * 0.16, d: CGFloat = 5.5
                for (sign, color) in [(-1.0, Color.primary), (1.0, Theme.accent)] {
                    let p = CGPoint(x: c.x + sign * rx * cos(a), y: c.y + sign * ry * sin(a))
                    let behind = sign * sin(a) > 0.2
                    g.fill(Path(ellipseIn: CGRect(x: p.x - d / 2, y: p.y - d / 2, width: d, height: d)),
                           with: .color(color.opacity(behind ? 0.4 : 1)))
                }
            }
        }
        .accessibilityHidden(true)
    }
}

extension Store.ReleasePhase {
    var cancellable: Bool {
        switch self {
        case .waiting, .countdown, .failed: true
        default: false
        }
    }
}

// MARK: Keyboard

private struct Shortcuts: View {
    @Environment(Store.self) private var store

    var body: some View {
        ZStack {
            key("1", [.command]) { store.tab = .attention }
            key("2", [.command]) { store.tab = .all }
            key("f", [.command]) { store.searching = true }
            key("r", [.command]) { Task { await store.syncAll() } }
            key("p", [.command]) { store.autoSync.toggle() }
            key(",", [.command]) { store.showSettings.toggle() }
            key(.leftArrow, [.command, .option]) { Task { await store.requestRelease(.codex) } }
            key(.rightArrow, [.command, .option]) { Task { await store.requestRelease(.claude) } }
            key(.escape, []) {
                if store.release?.phase == .confirm { store.cancelRelease() }
                else if store.searching { store.query = ""; store.searching = false }
                else if store.showSettings { store.showSettings = false }
                else { WindowManager.shared.hide() }
            }
        }
        .opacity(0)
        .allowsHitTesting(false)
    }

    private func key(_ k: KeyEquivalent, _ m: EventModifiers, _ run: @escaping () -> Void) -> some View {
        Button("", action: run).keyboardShortcut(k, modifiers: m)
    }
}

import SwiftUI

// MARK: «Внимание»

struct AttentionList: View {
    @Environment(Store.self) private var store

    var body: some View {
        if !store.autoSync { AutoSyncBanner() }
        if !store.loaded {
            ForEach(0..<3, id: \.self) { _ in SkeletonRow() }
        } else {
            let problems = store.attentionCount > 0
            Hero()
            if !store.errors.isEmpty {
                Section { rows(store.errors, key: "errors") } header: {
                    SectionHeader(title: "Ошибки", count: store.errors.count)
                }
            }
            if !store.waitingCodex.isEmpty {
                Section { rows(store.waitingCodex, key: "codex") } header: {
                    SectionHeader(title: "Ждут Codex", count: store.waitingCodex.count,
                                  action: ("Перезапустить", "arrow.clockwise", { Task { await store.requestRelease(.codex) } }))
                        .help("Завершить Codex, перенести очередь и открыть снова")
                }
            }
            if !store.waitingClaude.isEmpty {
                Section { rows(store.waitingClaude, key: "claude") } header: {
                    SectionHeader(title: "Ждут Claude", count: store.waitingClaude.count,
                                  action: ("Перезапустить", "arrow.clockwise", { Task { await store.requestRelease(.claude) } }))
                        .help("Завершить Claude, перенести очередь и открыть снова")
                }
            }
            if !store.inFlight.isEmpty {
                Section { rows(store.inFlight, key: "pending") } header: {
                    SectionHeader(title: "В очереди", count: store.inFlight.count)
                }
            }
            if problems {
                Rectangle().fill(Theme.separator).frame(height: 0.5)
                    .padding(.horizontal, Theme.gutter).padding(.vertical, 6)
            }
            WorkingLine()
            if problems { SyncedLine() }
            recent
        }
    }

    @ViewBuilder private func rows(_ list: [SyncThread], key: String) -> some View {
        let open = store.expanded.contains(key)
        let shown = open ? list : Array(list.prefix(5))
        ForEach(Array(shown.enumerated()), id: \.element.id) { i, t in
            ThreadRow(thread: t, showSeparator: i < shown.count - 1)
                .transition(.opacity.combined(with: .blurReplace))
        }
        if list.count > 5 && !open {
            MoreRow(rest: Array(list.dropFirst(5))) { store.expanded.insert(key) }
        }
    }

    @ViewBuilder private var recent: some View {
        // Firstlight never looks empty: the latest quiet threads fill the rest of the window.
        let list = Array(store.synced.prefix(3))
        if !list.isEmpty {
            SectionHeader(title: "Недавно", count: list.count).padding(.top, 4)
            ForEach(Array(list.enumerated()), id: \.element.id) { i, t in
                ThreadRow(thread: t, showSeparator: i < list.count - 1)
            }
        }
    }
}

private struct WorkingLine: View {
    @Environment(Store.self) private var store

    var body: some View {
        let list = store.working
        if !list.isEmpty {
            let open = store.expanded.contains("working")
            QuietLine(symbol: "asterisk", tint: .green, spin: true,
                      text: Text("\(list.count) \(Format.plural(list.count, "работает", "работают", "работают"))")) {
                AvatarStack(projects: list.map(\.project))
                Image(systemName: "chevron.down")
                    .font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
                    .rotationEffect(.degrees(open ? 180 : 0))
                    .padding(.leading, 6)
            } action: {
                withAnimation(.spring(response: 0.35, dampingFraction: 0.88)) {
                    if open { store.expanded.remove("working") } else { store.expanded.insert("working") }
                }
            }
            if open {
                ForEach(Array(list.enumerated()), id: \.element.id) { i, t in
                    ThreadRow(thread: t, showSeparator: i < list.count - 1)
                        .transition(.opacity.combined(with: .blurReplace))
                }
            }
        }
    }
}

private struct SyncedLine: View {
    @Environment(Store.self) private var store

    var body: some View {
        let synced = store.synced.count, flight = store.inFlight.count
        let word = Format.plural(synced, "синхронизирован", "синхронизированы", "синхронизированы")
        let tail = Text(flight > 0 ? " · \(flight) \(store.autoSync ? "в пути" : "в очереди")" : "").foregroundStyle(.secondary)
        QuietLine(symbol: "checkmark", tint: .secondary, text: Text("\(synced) \(word)\(tail)")) {
            Image(systemName: "chevron.right").font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
        } action: {
            withAnimation(.spring(response: 0.32, dampingFraction: 0.82)) { store.tab = .all }
        }
    }
}

struct MoreRow: View {
    let rest: [SyncThread]
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 8) {
                AvatarStack(projects: rest.map(\.project))
                    .frame(width: Theme.rail - Theme.gutter, alignment: .leading)
                Text("Ещё \(rest.count)").font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
                Image(systemName: "chevron.down").font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
                Spacer()
            }
            .padding(.horizontal, Theme.gutter)
            .frame(height: 34)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
    }
}

// MARK: «Все»

struct AllList: View {
    @Environment(Store.self) private var store

    var body: some View {
        let cal = Calendar.current
        let all = store.threads
        let today = all.filter { cal.isDateInToday($0.updated) }
        let yesterday = all.filter { cal.isDateInYesterday($0.updated) }
        let earlier = all.filter { !cal.isDateInToday($0.updated) && !cal.isDateInYesterday($0.updated) }
        group("Сегодня", today, key: "today", defaultOpen: true)
        group("Вчера", yesterday, key: "yesterday", defaultOpen: true)
        group("Ранее", earlier, key: "earlier", defaultOpen: false)
        HStack(spacing: 4) {
            Text("Показаны треды за \(store.days) дн ·").foregroundStyle(.tertiary)
            Button("Изменить") { store.showSettings = true }.buttonStyle(.plain).foregroundStyle(Theme.accent)
        }
        .font(.system(size: 11))
        .padding(.horizontal, Theme.gutter)
        .padding(.vertical, 10)
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    @ViewBuilder private func group(_ title: String, _ list: [SyncThread], key: String, defaultOpen: Bool) -> some View {
        if !list.isEmpty {
            // expanded holds the groups the user flipped away from their default
            let open = store.expanded.contains("all." + key) != defaultOpen
            Button {
                withAnimation(.spring(response: 0.35, dampingFraction: 0.88)) {
                    if store.expanded.contains("all." + key) { store.expanded.remove("all." + key) }
                    else { store.expanded.insert("all." + key) }
                }
            } label: {
                HStack(spacing: 5) {
                    Text(title).font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                    Text("\(list.count)").font(.system(size: 12, weight: .medium).monospacedDigit()).foregroundStyle(.tertiary)
                    Spacer()
                    Image(systemName: open ? "chevron.down" : "chevron.right")
                        .font(.system(size: 10, weight: .semibold)).foregroundStyle(.tertiary)
                }
                .padding(.horizontal, Theme.gutter)
                .frame(height: 30)
                .contentShape(Rectangle())
            }
            .buttonStyle(.plain)
            if open {
                ForEach(Array(list.enumerated()), id: \.element.id) { i, t in
                    ThreadRow(thread: t, showSeparator: i < list.count - 1)
                }
            }
        }
    }
}

// MARK: Search

struct SearchResults: View {
    @Environment(Store.self) private var store

    var body: some View {
        let list = store.threads.sorted { ($0.state.severity, $1.updatedMs) > ($1.state.severity, $0.updatedMs) }
        if list.isEmpty {
            Text("Ничего не найдено")
                .font(.system(size: 13)).foregroundStyle(.secondary)
                .frame(maxWidth: .infinity, minHeight: 80)
        } else {
            ForEach(Array(list.enumerated()), id: \.element.id) { i, t in
                ThreadRow(thread: t, showSeparator: i < list.count - 1)
            }
        }
    }
}

extension SyncState {
    var isWorking: Bool { self == .workingCodex || self == .workingClaude }
    var needsUser: Bool { self == .error || self == .waitingCodex || self == .waitingClaude }
    var severity: Int {
        switch self {
        case .error: 5
        case .waitingCodex, .waitingClaude: 4
        case .workingCodex, .workingClaude: 3
        case .pending: 2
        default: 1
        }
    }
}

// MARK: Hero, banner, skeleton

private struct Hero: View {
    @Environment(Store.self) private var store

    var body: some View {
        VStack(spacing: 8) {
            LiveGlyph(active: store.syncing)
                .frame(width: 44, height: 28)
                .opacity(0.8)
            Text(store.summaryTitle).font(.system(size: 15, weight: .semibold))
            if let queue = store.queueSummary {
                Text(queue).font(.system(size: 12).monospacedDigit())
                    .foregroundStyle(.secondary)
            }
            TimelineView(.periodic(from: .now, by: 10)) { ctx in
                Text(subtitle(now: ctx.date))
                    .font(.system(size: 12).monospacedDigit()).foregroundStyle(.secondary)
                    .contentTransition(.numericText())
            }
        }
        .frame(maxWidth: .infinity)
        .padding(.vertical, 26)
    }

    private func subtitle(now: Date) -> String {
        if store.lastError != nil { return "Показаны последние известные данные" }
        let n = Format.threads(store.snapshot.threads.filter { !$0.archived }.count)
        guard store.snapshot.generatedMs > 0 else { return n }
        let at = Date(timeIntervalSince1970: Double(store.snapshot.generatedMs) / 1000)
        return "\(n) · проверено \(Format.age(at, now: now)) назад"
    }
}

/// "•|•" — Codex dot, mirror axis, Claude dot.
struct MirrorGlyph: Shape {
    func path(in r: CGRect) -> Path {
        var p = Path()
        let d = r.height * 0.34
        p.move(to: CGPoint(x: r.midX, y: r.minY + 1))
        p.addLine(to: CGPoint(x: r.midX, y: r.maxY - 1))
        p.addEllipse(in: CGRect(x: r.minX + 1, y: r.midY - d / 2, width: d, height: d))
        p.addEllipse(in: CGRect(x: r.maxX - 1 - d, y: r.midY - d / 2, width: d, height: d))
        return p
    }
}

private struct AutoSyncBanner: View {
    @Environment(Store.self) private var store

    var body: some View {
        let first = store.lastOk == nil
        let (toCodex, toClaude) = store.firstRun
        HStack(alignment: .center, spacing: 10) {
            Image(systemName: "pause.fill").font(.system(size: 12, weight: .semibold)).foregroundStyle(.secondary)
                .frame(width: 28, height: 28).background(Circle().fill(Color.primary.opacity(0.06)))
            VStack(alignment: .leading, spacing: 3) {
                Text("Автосинхронизация выключена").font(.system(size: 13, weight: .semibold))
                Text(first && (toCodex + toClaude) > 0
                     ? "Первый запуск создаст \(Format.threads(toCodex)) в Codex и \(Format.sessions(toClaude)) в Claude."
                     : "Синхронизирую только по кнопке.")
                    .font(.system(size: 12)).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            Spacer(minLength: 0)
            Button("Включить") { store.autoSync = true }
                .buttonStyle(.plain)
                .font(.system(size: 12, weight: .semibold))
                .foregroundStyle(Theme.accent)
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(Color.primary.opacity(0.06)))
        .padding(.horizontal, 10)
        .padding(.bottom, 6)
    }
}

private struct SkeletonRow: View {
    @State private var dim = false
    var body: some View {
        HStack(spacing: 0) {
            Circle().fill(Color.primary.opacity(0.1)).frame(width: Theme.avatar, height: Theme.avatar)
                .frame(width: Theme.rail - Theme.gutter, alignment: .leading)
            VStack(alignment: .leading, spacing: 6) {
                Capsule().fill(Color.primary.opacity(0.1)).frame(width: 150, height: 10)
                Capsule().fill(Color.primary.opacity(0.07)).frame(width: 90, height: 8)
            }
            Spacer()
        }
        .padding(.horizontal, Theme.gutter)
        .frame(height: Theme.rowHeight)
        .opacity(dim ? 0.45 : 1)
        .onAppear { withAnimation(.easeInOut(duration: 1.2).repeatForever()) { dim = true } }
    }
}

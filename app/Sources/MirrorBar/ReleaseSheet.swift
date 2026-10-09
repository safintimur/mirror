import SwiftUI

/// In-window warning before quitting Codex and/or Claude (a native sheet does not fit a borderless panel).
/// Both apps get the same treatment: quit → sync → relaunch.
struct ReleaseSheet: View {
    @Environment(Store.self) private var store
    let release: Store.Release
    @State private var shown = false

    var body: some View {
        ZStack(alignment: .bottom) {
            Color.black.opacity(0.28)
                .contentShape(Rectangle())
                .onTapGesture { store.cancelRelease() }
            card
                .padding(10)
                .offset(y: shown ? 0 : 40)
                .opacity(shown ? 1 : 0)
        }
        .onAppear { withAnimation(.spring(response: 0.34, dampingFraction: 0.86)) { shown = true } }
    }

    private var card: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack(alignment: .top, spacing: 10) {
                HStack(spacing: -8) {
                    ForEach(release.providers) { p in
                        Image(nsImage: AppIcons.icon(p)).resizable().frame(width: 28, height: 28)
                    }
                }
                VStack(alignment: .leading, spacing: 3) {
                    Text("Перезапустить \(release.names)?").font(.system(size: 15, weight: .semibold))
                    Text(release.providers.count > 1
                         ? "Оба закроются, очередь перенесётся, затем они откроются снова."
                         : "\(release.names) закроется, очередь перенесётся, затем он откроется снова.")
                        .font(.system(size: 12)).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            ForEach(release.providers) { p in
                let busy = release.working[p] ?? []
                if !busy.isEmpty {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("Прервутся в \(p.displayName) · \(busy.count)")
                            .font(.system(size: 11, weight: .semibold)).foregroundStyle(.secondary)
                        ScrollView {
                            VStack(spacing: 0) { ForEach(busy) { w in workingRow(w, p) } }
                        }
                        .frame(maxHeight: 36 * 3)
                        .fixedSize(horizontal: false, vertical: true)
                    }
                }
            }
            VStack(alignment: .leading, spacing: 6) {
                ForEach(release.providers) { p in
                    let idle = store.idleOpen(p)
                    if idle > 0 {
                        Label(p == .claude ? "Claude: закроются ещё \(Format.sessions(idle)) без активного хода"
                                           : "Codex: закроются ещё \(Format.threads(idle))",
                              systemImage: "rectangle.stack")
                    }
                }
                let freed = release.providers.reduce(0) { $0 + store.pending($1) }
                if freed > 0 {
                    Label("Перенесётся \(Format.turns(freed)) из очереди", systemImage: "lock.open")
                }
                if release.providers.contains(.claude) {
                    Label("Неотправленный текст в поле ввода Claude может пропасть.", systemImage: "text.cursor")
                }
            }
            .font(.system(size: 12))
            .foregroundStyle(.secondary)

            VStack(spacing: 8) {
                Button { store.releaseWait() } label: {
                    Text(release.anyWorking ? "Дождаться и перезапустить" : "Перезапустить и синхронизировать")
                        .font(.system(size: 13, weight: .semibold)).foregroundStyle(Theme.onPrimary)
                        .frame(maxWidth: .infinity).frame(height: 22)
                }
                .buttonStyle(.glassProminent)
                .tint(Theme.primaryFill)
                .controlSize(.large)
                .keyboardShortcut(.defaultAction)

                if release.anyWorking {
                    Button(role: .destructive) { store.releaseNow() } label: {
                        Text("Перезапустить сейчас")
                            .font(.system(size: 13, weight: .semibold)).foregroundStyle(.red)
                            .frame(maxWidth: .infinity).frame(height: 22)
                    }
                    .buttonStyle(.glass)
                    .controlSize(.large)
                    .keyboardShortcut(.delete, modifiers: .command)
                }

                Button("Отмена") { store.cancelRelease() }
                    .buttonStyle(.plain)
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
                    .keyboardShortcut(.cancelAction)
                    .padding(.top, 2)
            }
        }
        .padding(16)
        .glassEffect(.regular, in: .rect(cornerRadius: 16, style: .continuous))
    }

    private func workingRow(_ w: WorkRef, _ p: Provider) -> some View {
        let thread = store.snapshot.threads.first {
            (w.id != nil && $0.threadId == w.id) || (w.sessionId != nil && $0.claudeSession == w.sessionId)
        }
        return HStack(spacing: 8) {
            Avatar(project: thread?.project ?? w.title ?? "?", size: 22)
                .overlay(alignment: .bottomTrailing) {
                    Circle().fill(.green).frame(width: 7, height: 7)
                        .background(Circle().fill(Theme.tint).padding(-1.5))
                }
            Text(w.title ?? thread?.title ?? "Без названия")
                .font(.system(size: 13, weight: .medium)).lineLimit(1)
            Spacer()
            Chip(symbol: p == .claude ? "asterisk" : "terminal",
                 text: thread.map { Format.age($0.updated) } ?? "идёт", tint: .green)
        }
        .frame(height: 36)
    }
}

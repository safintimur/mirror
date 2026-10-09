import AppKit
import Observation
import ServiceManagement

/// App state: engine snapshots, the background sync loop and the release-and-sync flow.
@MainActor
@Observable
final class Store {
    // MARK: Snapshot

    private(set) var snapshot: Snapshot = .empty
    private(set) var loaded = false
    private(set) var syncing = false
    private(set) var lastError: String?
    private(set) var lastSyncAt: Date?
    private(set) var nextTickAt: Date?

    // MARK: Settings (persisted)

    /// Background sync every `interval` seconds. Manual «Синхронизировать» works either way.
    var autoSync: Bool {
        didSet {
            UserDefaults.standard.set(autoSync, forKey: "autoSync")
            if autoSync { start() }
        }
    }
    var days: Int {
        didSet { UserDefaults.standard.set(days, forKey: "days"); Task { await refresh() } }
    }
    var interval: Int {
        didSet { UserDefaults.standard.set(interval, forKey: "interval") }
    }
    /// With auto-sync: release (quit → sync → relaunch) apps holding work once the user is away ≥ 5 min.
    var autoRelease: Bool {
        didSet { UserDefaults.standard.set(autoRelease, forKey: "autoRelease") }
    }

    // MARK: UI state

    enum Tab: Hashable { case attention, all }
    var tab: Tab = .attention
    var query = ""
    var searching = false
    var showSettings = false
    var expanded: Set<String> = []
    var confirmingPause = false
    /// The panel is hidden most of the time: nothing inside it should render or animate then.
    var panelVisible = false
    var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            do { newValue ? try SMAppService.mainApp.register() : try SMAppService.mainApp.unregister() }
            catch { lastError = error.localizedDescription }
        }
    }

    // MARK: Release flow

    enum ReleasePhase: Equatable {
        case confirm            // working threads found: ask wait / quit now / cancel
        case waiting            // waiting for running turns to finish
        case countdown(Int)     // target app is frontmost: last chance to cancel
        case quitting
        case syncing
        case relaunching
        case done(String)
        case failed(String)
    }

    struct Release: Equatable {
        var providers: [Provider]       // Codex first, then Claude: the same quit → sync → relaunch for both
        var phase: ReleasePhase
        var working: [Provider: [WorkRef]]

        var names: String { providers.map(\.displayName).joined(separator: " и ") }
        var anyWorking: Bool { working.values.contains { !$0.isEmpty } }
    }

    private(set) var release: Release?

    private let engine = Engine()
    private var loop: Task<Void, Never>?
    private var releaseTask: Task<Void, Never>?

    init() {
        let d = UserDefaults.standard
        // Off on a fresh install: the first real sync creates many threads and is the user's call.
        autoSync = d.object(forKey: "autoSync") as? Bool ?? !(d.object(forKey: "paused") as? Bool ?? true)
        days = d.object(forKey: "days") as? Int ?? 7
        interval = d.object(forKey: "interval") as? Int ?? 60
        autoRelease = d.bool(forKey: "autoRelease")
    }

    // MARK: Loop

    func start() {
        loop?.cancel()
        loop = Task { [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                if self.autoSync && self.release == nil { await self.sync(manual: false) } else { await self.refresh() }
                if self.autoSync && self.autoRelease && self.release == nil && Self.userIdleSeconds() >= 300 {
                    let ps = self.needsRelease.filter { self.working($0).isEmpty }
                    if !ps.isEmpty { self.run(ps, force: false, waitFirst: false) }
                }
                self.nextTickAt = .now.addingTimeInterval(Double(self.interval))
                try? await Task.sleep(for: .seconds(self.interval))
            }
        }
    }

    /// Cheap (≈0.2 s) status refresh; called when the window opens and after actions.
    func refresh() async {
        do {
            snapshot = try await engine.status(days: days)
            loaded = true
            lastError = nil
        } catch {
            lastError = error.localizedDescription
        }
    }

    func sync(only: [String] = [], manual: Bool = true) async {
        guard !syncing else { return }
        syncing = true
        defer { syncing = false }
        do {
            try await engine.sync(days: days, only: only, manual: manual)
            lastSyncAt = .now
        } catch {
            lastError = error.localizedDescription
        }
        await refresh()
    }

    // MARK: Derived lists

    var threads: [SyncThread] {
        let all = snapshot.threads.filter { !$0.archived }
        let q = query.trimmingCharacters(in: .whitespaces)
        guard !q.isEmpty else { return all }
        return all.filter {
            $0.title.range(of: q, options: [.caseInsensitive, .diacriticInsensitive]) != nil
                || $0.project.range(of: q, options: [.caseInsensitive, .diacriticInsensitive]) != nil
        }
    }

    private func waitSort(_ a: SyncThread, _ b: SyncThread) -> Bool { a.updatedMs < b.updatedMs }

    var errors: [SyncThread] { threads.filter { $0.state == .error } }
    var waitingCodex: [SyncThread] { threads.filter { $0.state == .waitingCodex }.sorted(by: waitSort) }
    var waitingClaude: [SyncThread] { threads.filter { $0.state == .waitingClaude }.sorted(by: waitSort) }
    var working: [SyncThread] { threads.filter { $0.state == .workingCodex || $0.state == .workingClaude } }
    var inFlight: [SyncThread] { threads.filter { $0.state == .pending } }
    var synced: [SyncThread] { threads.filter { $0.state == .synced } }
    /// Last successful sync, also when it was run outside the app (engine's per-thread last_ok_ms).
    var lastOk: Date? {
        let ms = snapshot.threads.compactMap(\.lastOkMs).max()
        let engine = ms.map { Date(timeIntervalSince1970: Double($0) / 1000) }
        return [lastSyncAt, engine].compactMap { $0 }.max()
    }

    var attentionCount: Int { errors.count + waitingCodex.count + waitingClaude.count + inFlight.count }

    // Use the whole snapshot: a search must not turn a hidden queue into a success state.
    var summaryTitle: String {
        if lastError != nil { return "Не удалось проверить статус" }
        if syncing { return "Синхронизирую…" }
        let live = snapshot.threads.filter { !$0.archived }
        if live.contains(where: { $0.state == .error || $0.state == .unknown }) { return "Нужна проверка" }
        if live.contains(where: { $0.toClaude > 0 || $0.toCodex > 0 || $0.state == .pending }) {
            return "Есть очередь синхронизации"
        }
        if live.contains(where: { $0.state != .synced }) { return "Чаты ещё работают" }
        if snapshot.apps.codex.restartNeeded == true || snapshot.apps.claude.restartNeeded == true {
            return "Изменения ждут перезапуска"
        }
        if live.isEmpty { return "Нет чатов за выбранный период" }
        return "Всё отражено"
    }

    var queueSummary: String? {
        let totals = snapshot.totals
        var parts: [String] = []
        if totals.toClaude > 0 { parts.append("\(Format.turns(totals.toClaudeTurns)) → Claude") }
        if totals.toCodex > 0 { parts.append("\(Format.turns(totals.toCodex)) → Codex") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    /// First real sync: Claude-born sessions that become new Codex threads, and Codex threads that get a Claude mirror.
    var firstRun: (codex: Int, claude: Int) {
        let unpaired = snapshot.threads.filter { !$0.paired && !$0.archived }
        return (unpaired.filter { $0.origin == .claude }.count, unpaired.filter { $0.origin == .codex }.count)
    }

    // MARK: Open

    func open(_ t: SyncThread, in p: Provider) {
        var url: URL?
        switch p {
        case .codex: if let id = t.threadId, t.codexVisible { url = URL(string: "codex://threads/\(id)") }
        case .claude: if let sid = t.claudeSession { url = URL(string: "claude://code/continue?session=\(sid)") }
        }
        if let url { NSWorkspace.shared.open(url) } else { AppControl.activate(p) }
        WindowManager.shared.hide()
    }

    // MARK: Release & sync

    func working(_ p: Provider) -> [WorkRef] {
        p == .codex ? snapshot.apps.codex.working : snapshot.apps.claude.working
    }

    /// Running apps that hold turns: only these are worth a restart.
    var needsRelease: [Provider] {
        let a = snapshot.apps
        var res: [Provider] = []
        if a.codex.running && (a.codex.blocked ?? 0) > 0 { res.append(.codex) }
        if a.claude.running && (a.claude.blocked ?? 0) > 0 { res.append(.claude) }
        return res
    }

    /// Cosmetic changes (archive, names, sidebar refresh) that show up on the app's next start anyway.
    var restartHints: [Provider] {
        let a = snapshot.apps
        var res: [Provider] = []
        if a.codex.running && a.codex.restartNeeded == true && !needsRelease.contains(.codex) { res.append(.codex) }
        if a.claude.running && a.claude.restartNeeded == true && !needsRelease.contains(.claude) { res.append(.claude) }
        return res
    }

    /// The main button: plain sync when nothing waits, otherwise release what blocks, sync, relaunch.
    func syncAll() async {
        await refresh()
        let ps = needsRelease
        if ps.isEmpty { await sync() } else { await requestRelease(ps) }
    }

    func requestRelease(_ p: Provider) async { await requestRelease([p]) }

    /// Confirm if something is running; Claude always asks (quitting it closes the chat you may be typing in).
    func requestRelease(_ ps: [Provider]) async {
        guard release == nil || release?.phase.isFinal == true else { return }
        await refresh()
        let order = Provider.allCases.filter(ps.contains)
        let busy = Dictionary(uniqueKeysWithValues: order.map { ($0, working($0)) })
        if !order.contains(.claude) && busy.values.allSatisfy(\.isEmpty) {
            run(order, force: false, waitFirst: false)
        } else {
            release = Release(providers: order, phase: .confirm, working: busy)
        }
    }

    func releaseWait() { if let r = release { run(r.providers, force: false, waitFirst: true) } }
    func releaseNow() { if let r = release { run(r.providers, force: true, waitFirst: false) } }

    func cancelRelease() {
        releaseTask?.cancel()
        release = nil
    }

    /// Quit -> sync -> relaunch, one app at a time: an app with nothing running goes first, so waiting for a
    /// busy one never holds the other back. Waiting is bounded; a busy app past the limit is left untouched.
    private func run(_ ps: [Provider], force: Bool, waitFirst: Bool) {
        releaseTask?.cancel()
        releaseTask = Task { [weak self] in
            guard let self else { return }
            await self.refresh()
            let order = ps.sorted { self.working($0).isEmpty && !self.working($1).isEmpty }
            var r = Release(providers: order, phase: .quitting,
                            working: Dictionary(uniqueKeysWithValues: order.map { ($0, self.working($0)) }))
            var moved = 0, skipped: [String] = []
            for p in order {
                if Task.isCancelled { return }
                if waitFirst && !self.working(p).isEmpty {
                    let deadline = Date.now.addingTimeInterval(15 * 60)
                    r.phase = .waiting; self.release = r
                    while !Task.isCancelled && !self.working(p).isEmpty && Date.now < deadline {
                        try? await Task.sleep(for: .seconds(15))
                        await self.refresh()
                        r.working[p] = self.working(p); self.release = r
                    }
                    if Task.isCancelled { return }
                    if !self.working(p).isEmpty {
                        skipped.append("в \(p.displayName) ещё идёт работа")
                        continue
                    }
                }
                if !force, NSWorkspace.shared.frontmostApplication?.bundleIdentifier == p.bundleId {
                    for n in stride(from: 5, through: 1, by: -1) {
                        r.phase = .countdown(n); self.release = r
                        try? await Task.sleep(for: .seconds(1))
                        if Task.isCancelled { return }
                    }
                }
                r.phase = .quitting; self.release = r
                guard await AppControl.quit(p, force: force) else {
                    skipped.append("\(p.displayName) не закрылся")
                    continue
                }
                r.phase = .syncing; self.release = r
                let before = self.pending(p)
                for _ in 0..<3 where !Task.isCancelled {
                    await self.sync()
                    if self.pending(p) == 0 { break }
                    try? await Task.sleep(for: .seconds(2))
                }
                moved += max(0, before - self.pending(p))
                r.phase = .relaunching; self.release = r
                await AppControl.relaunch(p)
            }
            if skipped.isEmpty {
                r.phase = .done(moved > 0 ? "Перенесено: \(moved)" : "Всё синхронизировано")
            } else {
                r.phase = .failed(skipped.joined(separator: ", "))
            }
            self.release = r
            await self.refresh()
            try? await Task.sleep(for: .seconds(skipped.isEmpty ? 4 : 8))
            if self.release == r { self.release = nil }
        }
    }

    static func userIdleSeconds() -> Double {
        CGEventSource.secondsSinceLastEventType(.hidSystemState, eventType: CGEventType(rawValue: ~0)!)
    }

    /// Open but idle sessions (Claude) / threads held open (Codex) that the quit will also close.
    func idleOpen(_ p: Provider) -> Int {
        switch p {
        case .claude: snapshot.apps.claude.open.filter { $0.status != "busy" }.count
        case .codex: max(0, snapshot.apps.codex.locked.count - snapshot.apps.codex.working.count)
        }
    }

    /// Work that was blocked by this provider's locks.
    func pending(_ p: Provider) -> Int {
        switch p {
        case .codex: snapshot.threads.filter(\.codexLocked).reduce(0) { $0 + $1.toCodex }
        case .claude: snapshot.threads.filter(\.claudeOpen).reduce(0) { $0 + $1.toClaudeTurns }
        }
    }

}

extension Store.ReleasePhase {
    var isFinal: Bool {
        switch self {
        case .done, .failed: true
        default: false
        }
    }
}

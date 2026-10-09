import AppKit

/// Quits / relaunches Codex and Claude to release their locks on threads.
@MainActor
enum AppControl {
    static func running(_ p: Provider) -> [NSRunningApplication] {
        NSRunningApplication.runningApplications(withBundleIdentifier: p.bundleId)
    }

    static func isRunning(_ p: Provider) -> Bool { !running(p).isEmpty }

    /// Graceful quit (like ⌘Q); `force` escalates to forceTerminate if the app does not exit in time.
    /// Returns true once no instance is left.
    static func quit(_ p: Provider, force: Bool, timeout: Duration = .seconds(20)) async -> Bool {
        let apps = running(p)
        guard !apps.isEmpty else { return true }
        apps.forEach { $0.terminate() }
        if await waitGone(p, timeout: timeout) { return true }
        guard force else { return false }
        running(p).forEach { $0.forceTerminate() }
        return await waitGone(p, timeout: .seconds(5))
    }

    static func relaunch(_ p: Provider) async {
        guard let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: p.bundleId) else { return }
        let cfg = NSWorkspace.OpenConfiguration()
        cfg.activates = false
        _ = try? await NSWorkspace.shared.openApplication(at: url, configuration: cfg)
    }

    static func activate(_ p: Provider) {
        if let app = running(p).first {
            app.activate()
        } else if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: p.bundleId) {
            NSWorkspace.shared.openApplication(at: url, configuration: .init())
        }
    }

    private static func waitGone(_ p: Provider, timeout: Duration) async -> Bool {
        let clock = ContinuousClock()
        let deadline = clock.now + timeout
        while clock.now < deadline {
            if running(p).allSatisfy(\.isTerminated) || running(p).isEmpty { return true }
            try? await Task.sleep(for: .milliseconds(250))
        }
        return running(p).isEmpty
    }
}

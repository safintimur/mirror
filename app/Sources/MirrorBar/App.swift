import AppKit
import SwiftUI

@main
struct MirrorBarApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene { Settings { EmptyView() } }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let store = Store()
    private var statusItem: NSStatusItem!
    private var icon: StatusIconAnimator!

    func applicationDidFinishLaunching(_ note: Notification) {
        if let out = ProcessInfo.processInfo.environment["MIRROR_ICON_PREVIEW"] {
            StatusIconAnimator.preview(to: out)
            exit(0)
        }
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        let button = statusItem.button!
        button.target = self
        button.action = #selector(click)
        button.sendAction(on: [.leftMouseUp, .rightMouseUp])
        icon = StatusIconAnimator(button: button)

        WindowManager.shared.configure(root: RootView().environment(store), anchor: button)
        WindowManager.shared.onVisibilityChange = { [weak self] visible in
            button.highlight(visible)
            self?.store.panelVisible = visible
            if visible { Task { await self?.store.refresh() } }
        }
        observeIcon()
        store.start()
        // Debug/screenshots: MIRROR_SHOW=1 opens the window on launch, MIRROR_TAB=all|settings picks the view.
        let env = ProcessInfo.processInfo.environment
        if env["MIRROR_ABOUT"] == "1" { Task { try? await Task.sleep(for: .seconds(1)); About.show() } }
        if env["MIRROR_SHOW"] == "1" {
            if env["MIRROR_TAB"] == "all" { store.tab = .all }
            if env["MIRROR_TAB"] == "settings" { store.showSettings = true }
            // Claude always asks first, so this only opens the confirmation sheet.
            if env["MIRROR_TAB"] == "release-all" { Task { try? await Task.sleep(for: .seconds(2)); await store.requestRelease([.codex, .claude]) } }
            if env["MIRROR_TAB"] == "release-claude" { Task { try? await Task.sleep(for: .seconds(2)); await store.requestRelease(.claude) } }
            Task { try? await Task.sleep(for: .seconds(1)); WindowManager.shared.show() }
        }
    }

    @objc private func click() {
        if NSApp.currentEvent?.type == .rightMouseUp || NSApp.currentEvent?.modifierFlags.contains(.control) == true {
            WindowManager.shared.hide()
            statusItem.menu = menu()
            statusItem.button?.performClick(nil)
            statusItem.menu = nil
        } else if NSApp.currentEvent?.modifierFlags.contains(.option) == true {
            store.autoSync.toggle()
        } else {
            WindowManager.shared.toggle()
        }
    }

    private func menu() -> NSMenu {
        let m = NSMenu()
        m.addItem(item("Синхронизировать", "r") { [store] in Task { await store.sync() } })
        m.addItem(item("Перезапустить всё и синхронизировать…", "") { [store] in
            WindowManager.shared.show(); Task { await store.requestRelease(Provider.allCases.filter { AppControl.isRunning($0) }) }
        })
        m.addItem(item("Перезапустить Codex и синхронизировать…", "") { [store] in
            WindowManager.shared.show(); Task { await store.requestRelease(.codex) }
        })
        m.addItem(item("Перезапустить Claude и синхронизировать…", "") { [store] in
            WindowManager.shared.show(); Task { await store.requestRelease(.claude) }
        })
        m.addItem(.separator())
        let auto = item("Автосинхронизация", "p") { [store] in store.autoSync.toggle() }
        auto.state = store.autoSync ? .on : .off
        m.addItem(auto)
        m.addItem(item("Настройки…", ",") { [store] in store.showSettings = true; WindowManager.shared.show() })
        m.addItem(item("О программе Mirror", "") { About.show() })
        m.addItem(.separator())
        m.addItem(item("Завершить Mirror", "q") { NSApp.terminate(nil) })
        return m
    }

    private func item(_ title: String, _ key: String, _ run: @escaping () -> Void) -> NSMenuItem {
        let i = ClosureMenuItem(title: title, keyEquivalent: key, run: run)
        return i
    }

    /// Re-render the menu bar glyph whenever the observed store state changes.
    private func observeIcon() {
        withObservationTracking {
            icon.update(iconState())
        } onChange: { [weak self] in
            Task { @MainActor in self?.observeIcon() }
        }
    }

    private func iconState() -> IconState {
        let s = store
        let phase = s.release?.phase
        let moving = s.syncing || phase == .quitting || phase == .syncing || phase == .relaunching
        let waiting = s.waitingCodex + s.waitingClaude
        return IconState(
            syncing: moving,
            codexBlocked: !s.waitingCodex.isEmpty,
            claudeBlocked: !s.waitingClaude.isEmpty,
            count: waiting.count + s.errors.count,
            stale: waiting.contains { $0.stale },
            error: !s.errors.isEmpty || s.lastError != nil,
            paused: !s.autoSync)
    }
}

final class ClosureMenuItem: NSMenuItem {
    private let run: () -> Void
    init(title: String, keyEquivalent: String, run: @escaping () -> Void) {
        self.run = run
        super.init(title: title, action: #selector(fire), keyEquivalent: keyEquivalent)
        target = self
    }
    required init(coder: NSCoder) { fatalError() }
    @objc private func fire() { run() }
}

// Window placement and click-outside dismissal adapted from Pulso (github.com/get-pulso/mac, MIT),
// the open-source predecessor of Firstlight.
import AppKit
import SwiftUI

/// Borderless, transparent panel that hosts the SwiftUI root; the glass is drawn by SwiftUI.
final class MirrorPanel: NSPanel {
    init<Content: View>(root: Content) {
        super.init(contentRect: .zero, styleMask: [.borderless, .nonactivatingPanel, .fullSizeContentView],
                   backing: .buffered, defer: false)
        isOpaque = false
        backgroundColor = .clear
        hasShadow = true
        level = .mainMenu
        isReleasedWhenClosed = false
        animationBehavior = .none
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
        let host = NSHostingView(rootView: root)
        host.sizingOptions = [.intrinsicContentSize]
        contentView = host
    }

    override var canBecomeKey: Bool { true }

    override func cancelOperation(_ sender: Any?) { WindowManager.shared.hide() }
}

@MainActor
final class WindowManager {
    static let shared = WindowManager()

    private(set) var isVisible = false
    private var panel: MirrorPanel?
    private var monitor: Any?
    private weak var anchor: NSStatusBarButton?
    var onVisibilityChange: ((Bool) -> Void)?

    func configure<Content: View>(root: Content, anchor: NSStatusBarButton) {
        self.anchor = anchor
        panel = MirrorPanel(root: root)
        monitor = NSEvent.addGlobalMonitorForEvents(matching: [.leftMouseDown, .rightMouseDown]) { [weak self] _ in
            Task { @MainActor in self?.hide() }
        }
    }

    func toggle() { isVisible ? hide() : show() }

    func show() {
        guard let panel, let origin = topLeft(width: panel.frame.width > 0 ? panel.frame.width : 360) else { return }
        panel.setFrameTopLeftPoint(origin)
        panel.makeKeyAndOrderFront(nil)
        isVisible = true
        onVisibilityChange?(true)
    }

    func hide() {
        guard isVisible else { return }
        panel?.orderOut(nil)
        isVisible = false
        onVisibilityChange?(false)
    }

    /// Keeps the top edge pinned under the status item when SwiftUI changes the content height.
    func relayout() {
        guard isVisible, let panel, let origin = topLeft(width: panel.frame.width) else { return }
        panel.setFrameTopLeftPoint(origin)
    }

    private func topLeft(width: CGFloat) -> NSPoint? {
        guard let button = anchor, let win = button.window, let screen = win.screen else { return nil }
        let frame = win.convertToScreen(button.convert(button.bounds, to: nil))
        let visible = screen.visibleFrame
        let inset: CGFloat = 8
        var x = frame.midX - width / 2
        x = min(max(x, visible.minX + inset), visible.maxX - width - inset)
        return NSPoint(x: x, y: frame.minY - 6)
    }
}

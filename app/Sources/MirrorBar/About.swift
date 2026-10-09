import AppKit

/// The standard AppKit About panel: icon, name, version and build come from Info.plist.
enum About {
    static var version: String {
        Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "dev"
    }

    @MainActor static func show() {
        WindowManager.shared.hide()
        let style = NSMutableParagraphStyle()
        style.alignment = .center
        let credits = NSAttributedString(
            string: "Синхронизирует чаты\nCodex и Claude Code в обе стороны.\n"
                + "Неофициальное приложение,\nне связано с OpenAI и Anthropic.",
            attributes: [.font: NSFont.systemFont(ofSize: 11), .foregroundColor: NSColor.secondaryLabelColor,
                         .paragraphStyle: style])
        // a menu bar app is never active on its own: bring the panel to the front
        NSApp.activate()
        NSApp.orderFrontStandardAboutPanel(options: [.credits: credits])
    }
}

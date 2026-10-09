import SwiftUI

struct SettingsView: View {
    @Environment(Store.self) private var store

    var body: some View {
        @Bindable var store = store
        Form {
            Section("Синхронизация") {
                Toggle(isOn: $store.autoSync) {
                    Text("Автосинхронизация")
                    Text("Выкл. — только по кнопке «Синхронизировать»")
                }
                Picker("Интервал", selection: $store.interval) {
                    Text("30 с").tag(30)
                    Text("1 мин").tag(60)
                    Text("5 мин").tag(300)
                }
                .pickerStyle(.segmented)
                .disabled(!store.autoSync)
                Toggle(isOn: $store.autoRelease) {
                    Text("Перезапускать при простое")
                    Text("Если что-то ждёт и 5 мин нет ввода — перезапустить Codex/Claude без идущих ходов")
                }
                .disabled(!store.autoSync)
                Picker("Окно тредов", selection: $store.days) {
                    ForEach([1, 3, 7, 14, 30], id: \.self) { Text("\($0) дн").tag($0) }
                }
            }
            Section("Система") {
                Toggle("Запускать при входе", isOn: Binding(get: { store.launchAtLogin },
                                                             set: { store.launchAtLogin = $0 }))
                Button("Папка данных движка") {
                    NSWorkspace.shared.open(URL(fileURLWithPath: NSHomeDirectory() + "/.local/share/codex-claude-mirror"))
                }
            }
            Section {
                HStack {
                    Button("Mirror \(About.version) · \(store.lastError == nil ? "движок в норме" : "ошибка движка")") {
                        About.show()
                    }
                    .buttonStyle(.plain).font(.system(size: 11)).foregroundStyle(.tertiary)
                    .help("О программе Mirror")
                    Spacer()
                    Button("Завершить Mirror") { NSApp.terminate(nil) }
                        .buttonStyle(.plain).foregroundStyle(.red)
                }
                if let err = store.lastError {
                    Text(err).font(.system(size: 11)).foregroundStyle(.red.opacity(0.85)).textSelection(.enabled)
                }
            }
        }
        .formStyle(.grouped)
        .tint(Theme.accent)
        .scrollContentBackground(.hidden)
        .controlSize(.small)
    }
}

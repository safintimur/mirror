import Foundation

/// `codex_mirror.py status` JSON (snake_case keys).
struct Snapshot: Decodable, Equatable {
    var generatedMs: Int64
    var days: Int
    var apps: Apps
    var totals: Totals
    var threads: [SyncThread]

    static let empty = Snapshot(
        generatedMs: 0, days: 7,
        apps: Apps(codex: .init(running: false, locked: [], working: []),
                   claude: .init(running: false, open: [], working: [])),
        totals: Totals(), threads: [])
}

struct Apps: Decodable, Equatable {
    var codex: CodexApp
    var claude: ClaudeApp
}

struct CodexApp: Decodable, Equatable {
    var running: Bool
    var locked: [String]
    var working: [WorkRef]
    /// Turns only a restart can deliver; sidebar changes waiting for the app's next start.
    var blocked: Int? = 0
    var restartNeeded: Bool? = false
}

struct ClaudeApp: Decodable, Equatable {
    var running: Bool
    var open: [OpenSession]
    var working: [WorkRef]
    var blocked: Int? = 0
    var restartNeeded: Bool? = false
}

struct OpenSession: Decodable, Equatable {
    var sessionId: String
    var title: String?
    var status: String
    var pid: Int?
}

struct WorkRef: Decodable, Equatable, Identifiable {
    var id: String?
    var sessionId: String?
    var title: String?
    var key: String { id ?? sessionId ?? title ?? "?" }
}

struct Totals: Decodable, Equatable {
    var threads = 0, synced = 0, pending = 0, waiting = 0, working = 0, errors = 0
    var toClaude = 0, toClaudeTurns = 0, toCodex = 0
}

enum SyncState: String, Decodable {
    case synced, pending, archived, error
    case waitingCodex = "waiting_codex", waitingClaude = "waiting_claude"
    case workingCodex = "working_codex", workingClaude = "working_claude"
    case unknown

    init(from decoder: Decoder) throws {
        self = SyncState(rawValue: try decoder.singleValueContainer().decode(String.self)) ?? .unknown
    }

    var needsAttention: Bool { self != .synced && self != .archived }
}

enum Origin: String, Decodable { case codex, claude }

struct SyncThread: Decodable, Equatable, Identifiable {
    var id: String { threadId ?? claudeSession ?? title }
    var threadId: String?
    var claudeSession: String?
    var origin: Origin
    var title: String
    var project: String
    var cwd: String
    var updatedMs: Int64
    var toClaude: Int
    var toClaudeTurns: Int
    var toCodex: Int
    var codexLocked: Bool
    var claudeOpen: Bool
    var codexWorking: Bool
    var claudeWorking: Bool
    var codexVisible: Bool
    var archived: Bool
    var paired: Bool
    var lastOkMs: Int64?
    var error: String?
    var state: SyncState

    var updated: Date { Date(timeIntervalSince1970: Double(updatedMs) / 1000) }

    enum CodingKeys: String, CodingKey {
        case threadId = "id"
        case claudeSession, origin, title, project, cwd, updatedMs, toClaude, toClaudeTurns, toCodex
        case codexLocked, claudeOpen, codexWorking, claudeWorking, codexVisible, archived, paired
        case lastOkMs, error, state
    }
}

/// One of the two synced desktop apps.
enum Provider: String, CaseIterable, Identifiable {
    case codex, claude
    var id: String { rawValue }
    var bundleId: String { self == .codex ? "com.openai.codex" : "com.anthropic.claudefordesktop" }
    var displayName: String { self == .codex ? "Codex" : "Claude" }
}

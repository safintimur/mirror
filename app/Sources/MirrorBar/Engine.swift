import Foundation

/// Runs the Python engine (`codex_mirror.py`) bundled in the app's Resources.
actor Engine {
    enum Failure: Error, LocalizedError {
        case exit(Int32, String)
        var errorDescription: String? {
            if case let .exit(code, err) = self { return "engine exit \(code): \(err.suffix(400))" }
            return nil
        }
    }

    private let python = "/usr/bin/python3"
    private let script: String
    private let decoder: JSONDecoder = {
        let d = JSONDecoder()
        d.keyDecodingStrategy = .convertFromSnakeCase
        return d
    }()

    init() {
        let bundled = Bundle.main.url(forResource: "codex_mirror", withExtension: "py")?.path
        // `swift run` during development: fall back to the repo copy next to the package.
        script = bundled ?? URL(fileURLWithPath: #filePath)
            .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
            .deletingLastPathComponent().appendingPathComponent("codex_mirror.py").path
    }

    func status(days: Int) async throws -> Snapshot {
        let out = try await run(["status", "--days", String(days)])
        return try decoder.decode(Snapshot.self, from: out)
    }

    /// One sync tick; `only` limits it to thread ids / Claude session ids.
    /// `manual`: the user asked for it, so new mirrors may be imported into Claude right away (brings it to front).
    func sync(days: Int, only: [String] = [], manual: Bool = false) async throws {
        var args = ["sync", "--days", String(days), "--json"]
        if manual { args.append("--manual") }
        for id in only { args += ["--thread", id] }
        _ = try await run(args)
    }

    /// Runs the engine and returns its stdout. No `waitUntilExit()`: from Swift concurrency it can miss the
    /// termination and spin forever. Exit comes from `terminationHandler`, pipes are drained on GCD threads
    /// (not the cooperative pool), and a hard timeout kills a stuck engine.
    private func run(_ args: [String], timeout: TimeInterval = 180) async throws -> Data {
        let p = Process()
        p.executableURL = URL(fileURLWithPath: python)
        p.arguments = [script] + args
        let out = Pipe(), err = Pipe()
        p.standardOutput = out
        p.standardError = err
        let group = DispatchGroup()
        final class Box: @unchecked Sendable { var out = Data(), err = Data() }
        let box = Box()
        group.enter(); group.enter(); group.enter()
        p.terminationHandler = { _ in group.leave() }
        try p.run()
        DispatchQueue.global(qos: .utility).async { box.out = out.fileHandleForReading.readDataToEndOfFile(); group.leave() }
        DispatchQueue.global(qos: .utility).async { box.err = err.fileHandleForReading.readDataToEndOfFile(); group.leave() }
        let finished: Bool = await withCheckedContinuation { cont in
            DispatchQueue.global(qos: .utility).async {
                cont.resume(returning: group.wait(timeout: .now() + timeout) == .success)
            }
        }
        guard finished else {
            p.terminate()
            throw Failure.exit(-1, "движок не ответил за \(Int(timeout)) с: \(args.first ?? "")")
        }
        guard p.terminationStatus == 0 else {
            throw Failure.exit(p.terminationStatus, String(decoding: box.err, as: UTF8.self))
        }
        return box.out
    }
}

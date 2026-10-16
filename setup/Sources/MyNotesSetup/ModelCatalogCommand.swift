import Foundation

private final class CatalogProcess: @unchecked Sendable {
    private let lock = NSLock()
    private var process: Process?
    private var cancelled = false
    func start(_ value: Process) throws {
        lock.lock(); defer { lock.unlock() }
        if cancelled { throw CancellationError() }
        process = value; try value.run()
    }
    func cancel() {
        lock.lock(); defer { lock.unlock() }
        cancelled = true
        if let process, process.isRunning { process.terminate() }
    }
    var wasCancelled: Bool { lock.lock(); defer { lock.unlock() }; return cancelled }
}
private final class CatalogBytes: @unchecked Sendable {
    var data = Data()
    var tooLarge = false
    func drain(_ reader: FileHandle) {
        while let bytes = try? reader.read(upToCount: 65536), !bytes.isEmpty {
            if data.count + bytes.count <= 2_000_000 { data.append(bytes) }
            else { tooLarge = true }
        }
    }
}

enum ModelCatalogCommand {
    static func read(_ executable: URL, arguments: [String], environment: [String: String]? = nil, timeout: Double = 22) async throws -> Data {
        let owner = CatalogProcess()
        return try await withTaskCancellationHandler(operation: {
            try await Task.detached {
                try execute(executable, arguments, environment, timeout, owner)
            }.value
        }, onCancel: { owner.cancel() })
    }
    private static func execute(_ executable: URL, _ arguments: [String], _ environment: [String: String]?, _ timeout: Double, _ owner: CatalogProcess) throws -> Data {
    let process = Process(), stdout = Pipe(), stderr = Pipe()
    process.executableURL = executable; process.arguments = arguments
    process.environment = environment ?? ProcessInfo.processInfo.environment
    process.standardOutput = stdout; process.standardError = stderr
    let output = CatalogBytes(), errors = CatalogBytes(), readers = DispatchGroup()
    readers.enter(); DispatchQueue.global().async { output.drain(stdout.fileHandleForReading); readers.leave() }
    readers.enter(); DispatchQueue.global().async { errors.drain(stderr.fileHandleForReading); readers.leave() }
    do { try owner.start(process) }
    catch { stdout.fileHandleForWriting.closeFile(); stderr.fileHandleForWriting.closeFile(); readers.wait(); throw error }
    stdout.fileHandleForWriting.closeFile(); stderr.fileHandleForWriting.closeFile()
    let expiry = DispatchWorkItem { owner.cancel() }
    DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: expiry)
    process.waitUntilExit(); readers.wait(); expiry.cancel()
    if owner.wasCancelled { throw CancellationError() }
    guard process.terminationStatus == 0, !output.tooLarge else {
        throw NSError(domain: "MyNotes", code: Int(process.terminationStatus), userInfo: [NSLocalizedDescriptionKey: "Il catalogo non è leggibile. Controlla l’installazione MyNotes e riprova."])
    }
    return output.data
    }

}

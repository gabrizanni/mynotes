import Foundation
import Darwin

struct CalendarSource: Codable { var sourceID: String }

struct CaptureConfiguration: Codable {
    var schemaVersion = 1
    var revision: Int
    var enabled: Bool
    var paused: Bool
    var refreshHours: Double
    var recordBeforeSeconds: Int
    var recordAfterSeconds: Int
    var outputDirectory: String
    var stageDirectory: String
    var pythonExecutable: String
    var courseCodes: [String]
    var sources: [CalendarSource] = []
}

struct Lecture: Codable, Identifiable, Equatable {
    var id: String
    var sourceID: String
    var courseCode: String?
    var lessonNumber: Int?
    var startsAt: Date
    var endsAt: Date
    var recordAt: Date
    var stopAt: Date
    var timezone: String
    var title: String
    var eligibility: String
    var reason: String
    var recordingID: String? = nil
    var transcriptionLanguage: String? = nil
    var lectureID: String {
        if let recordingID { return recordingID }
        return String(format: "%@-L%02d", courseCode ?? "", lessonNumber ?? 0)
    }
    var filename: String {
        if let recordingID {
            let slug = String(title.unicodeScalars.map { $0.isASCII && CharacterSet.alphanumerics.contains($0) ? String($0) : "-" }.joined().prefix(64))
            return "\(Int(startsAt.timeIntervalSince1970))-\(slug)-\(recordingID).m4a"
        }
        return String(format: "%@_L%02d.m4a", courseCode ?? "", lessonNumber ?? 0)
    }
    func validTarget(_ courses: [String]) -> Bool {
        if let recordingID {
            return recordingID.range(of: "^[a-f0-9]{32}$", options: .regularExpression) != nil
                && (courseCode == nil || courses.contains(courseCode!))
                && (lessonNumber == nil || (1...999).contains(lessonNumber!)) && !title.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                && title.count <= 8192
        }
        guard let code = courseCode, let number = lessonNumber else { return false }
        return courses.contains(code) && (1...999).contains(number)
    }
    func isCurrent(_ now: Date) -> Bool { recordAt <= now && now < stopAt && eligibility == "SCHEDULED" }
}

struct Schedule: Codable {
    var schemaVersion: Int
    var revision: Int
    var events: [Lecture]
}

struct Session: Codable {
    var schemaVersion = 1
    var id: String
    var event: Lecture
    var directory: String
    var actualStart: Date
    var actualStop: Date?
    var status: String
    var partial: Bool
    var reason: String
    var segments: [String]
    var outputPath: String?
    var destinationDirectory: String?
    var durationSeconds: Double?
    var requestID: String?
    var isTest: Bool

    func finalReason(_ stopReason: String) -> String {
        // A scheduled end must not erase the gap/recovery explanation.
        if partial && stopReason == "Scheduled end" && !reason.isEmpty && reason != stopReason {
            return "\(reason); \(stopReason)"
        }
        return stopReason
    }
}

struct ControlPayload: Codable { var event: Lecture?; var sessionID: String? }
struct ControlRequest: Codable {
    var schemaVersion: Int
    var id: String
    var action: String
    var createdAt: Date
    var expiresAt: Date
    var payload: ControlPayload
}
struct ControlResponse: Codable {
    var status: String
    var requestID: String
    var message: String
    var sessionID: String?
    var outputPath: String?
}
struct RuntimeStatus: Codable {
    var schemaVersion = 1
    var pid: Int32
    var updatedAt: Date
    var recording: Bool
    var exporting: Bool
    var microphonePermission: String
    var title: String
    var detail: String
    var currentSession: Session?
    var sessions: [Session]
    var capabilities: [String]? = ["standalone-recordings-v1", "calendar-recordings-v1"]
}

enum CaptureFailure: LocalizedError {
    case message(String)
    var errorDescription: String? { if case let .message(value) = self { return value }; return "Capture failed" }
}

enum CaptureJSON {
    static var encoder: JSONEncoder {
        let encoder = JSONEncoder()
        encoder.dateEncodingStrategy = .iso8601
        encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
        return encoder
    }
    static var decoder: JSONDecoder {
        let decoder = JSONDecoder()
        decoder.dateDecodingStrategy = .custom { input in
            let string = try input.singleValueContainer().decode(String.self)
            let formatter = ISO8601DateFormatter()
            if let date = formatter.date(from: string) { return date }
            formatter.formatOptions.insert(.withFractionalSeconds)
            if let date = formatter.date(from: string) { return date }
            throw CaptureFailure.message("Invalid date in capture state")
        }
        return decoder
    }
    static func load<T: Decodable>(_ type: T.Type, from url: URL) throws -> T {
        try decoder.decode(type, from: Data(contentsOf: url))
    }
    static func save<T: Encodable>(_ value: T, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try encoder.encode(value).write(to: url, options: .atomic)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
    }
}

/// The app, including recovery, is the sole writer of runtime and session state.
final class InstanceLock {
    private let descriptor: Int32
    init(url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        descriptor = open(url.path, O_CREAT | O_RDWR, 0o600)
        guard descriptor >= 0 else { throw CaptureFailure.message("Cannot create instance lock") }
        if flock(descriptor, LOCK_EX | LOCK_NB) != 0 {
            close(descriptor)
            throw CaptureFailure.message("Capture is already running")
        }
    }
    deinit { flock(descriptor, LOCK_UN); close(descriptor) }
}

enum RecoveryPolicy {
    static func candidates(_ sessions: [Session], now: Date) -> [Session] {
        sessions.filter { !["RECORDING", "COMPLETED", "EXISTING"].contains($0.status) && !$0.isTest &&
            !$0.segments.isEmpty && ($0.actualStop ?? now) <= now.addingTimeInterval(-15) }
    }
    static func unambiguous(_ sessions: [Session]) -> [Session] {
        let groups = Dictionary(grouping: sessions, by: { $0.event.lectureID })
        return groups.values.filter { $0.count == 1 }.compactMap { $0.first }
    }
}

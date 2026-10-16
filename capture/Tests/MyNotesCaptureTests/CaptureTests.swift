import XCTest
import AVFoundation
@testable import MyNotesCapture

final class CaptureTests: XCTestCase {
    private func root() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent("capture-test-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }
    private func lecture(_ now: Date) -> Lecture {
        Lecture(id: "one", sourceID: "static", courseCode: "QNT9", lessonNumber: 1,
                startsAt: now.addingTimeInterval(-20), endsAt: now.addingTimeInterval(60),
                recordAt: now.addingTimeInterval(-20), stopAt: now.addingTimeInterval(180),
                timezone: "UTC", title: "Quantitative Methods", eligibility: "SCHEDULED", reason: "")
    }
    private func configuration(_ root: URL, enabled: Bool = true) throws {
        let value = CaptureConfiguration(revision: 1, enabled: enabled, paused: false, refreshHours: 6,
                                         recordBeforeSeconds: 0, recordAfterSeconds: 120,
                                         outputDirectory: root.appendingPathComponent("recordings").path,
                                         stageDirectory: root.appendingPathComponent("staging").path,
                                         pythonExecutable: "/usr/bin/false", courseCodes: ["QNT9"])
        try CaptureJSON.save(value, to: root.appendingPathComponent("config/capture.json"))
    }
    @MainActor
    func testDeniedMicrophoneNeverClaimsRecording() throws {
        let directory = try root()
        defer { try? FileManager.default.removeItem(at: directory) }
        try configuration(directory)
        try CaptureJSON.save(Schedule(schemaVersion: 1, revision: 1, events: [lecture(Date())]), to: directory.appendingPathComponent("state/capture/schedule.json"))
        let engine = try CaptureEngine(workspace: directory, authorization: { false })
        engine.evaluate(now: Date())
        XCTAssertFalse(engine.isRecording)
        let runtime = try CaptureJSON.load(RuntimeStatus.self, from: directory.appendingPathComponent("state/capture/runtime.json"))
        XCTAssertFalse(runtime.recording)
        engine.shutdown()
    }
    @MainActor
    func testRestartTurnsStaleRecordingIntoInterruptedSession() throws {
        let directory = try root()
        defer { try? FileManager.default.removeItem(at: directory) }
        try configuration(directory, enabled: false)
        let event = lecture(Date())
        try CaptureJSON.save(Schedule(schemaVersion: 1, revision: 1, events: [event]), to: directory.appendingPathComponent("state/capture/schedule.json"))
        let session = Session(id: UUID().uuidString, event: event, directory: "staging", actualStart: Date(), status: "RECORDING", partial: false, reason: "", segments: ["segment-001.m4a"], isTest: false)
        try CaptureJSON.save(session, to: directory.appendingPathComponent("state/capture/sessions/\(session.id).json"))
        let engine = try CaptureEngine(workspace: directory, authorization: { false })
        XCTAssertEqual(engine.sessions.first?.status, "INTERRUPTED")
        XCTAssertEqual(engine.sessions.first?.partial, true)
        engine.shutdown()
    }
    func testRecoveryDoesNotSelectLargestAttempt() {
        let now = Date()
        let event = lecture(now)
        let first = Session(id: "first", event: event, directory: "one", actualStart: now, actualStop: now.addingTimeInterval(-60), status: "INTERRUPTED", partial: true, reason: "", segments: ["one"], isTest: false)
        let second = Session(id: "second", event: event, directory: "two", actualStart: now, actualStop: now.addingTimeInterval(-60), status: "FAILED", partial: true, reason: "", segments: ["one", "two"], isTest: false)
        XCTAssertEqual(RecoveryPolicy.candidates([first, second], now: now).count, 2)
        XCTAssertTrue(RecoveryPolicy.unambiguous([first, second]).isEmpty)
    }
    func testSingleInstanceLock() throws {
        let directory = try root()
        defer { try? FileManager.default.removeItem(at: directory) }
        let lock = try InstanceLock(url: directory.appendingPathComponent("instance.lock"))
        withExtendedLifetime(lock) {
            XCTAssertThrowsError(try InstanceLock(url: directory.appendingPathComponent("instance.lock")))
        }
    }
    @MainActor
    func testValidAudioAssemblyAndExistingFileProtection() async throws {
        let directory = try root()
        defer { try? FileManager.default.removeItem(at: directory) }
        let format = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 44_100)!
        buffer.frameLength = 44_100
        for i in 0..<44_100 { buffer.floatChannelData![0][i] = Float(sin(Double(i) * 2 * .pi * 440 / 44_100) * 0.1) }
        var segments: [URL] = []
        for i in 1...3 {
            let url = directory.appendingPathComponent("segment-\(i).caf")
            try AVAudioFile(forWriting: url, settings: format.settings).write(from: buffer)
            segments.append(url)
        }
        let output = directory.appendingPathComponent("QNT9_L01.m4a")
        let duration = try await SegmentAssembler.assemble(segments, to: output)
        XCTAssertEqual(duration, 3, accuracy: 0.1)
        let sentinel = try Data(contentsOf: output)
        do { _ = try await SegmentAssembler.assemble(segments, to: output); XCTFail("Must retain existing audio") }
        catch { XCTAssertEqual(try Data(contentsOf: output), sentinel) }
    }
    @MainActor
    func testCorruptSegmentCannotProduceApparentlyCompleteAudio() async throws {
        let directory = try root()
        defer { try? FileManager.default.removeItem(at: directory) }
        let corrupt = directory.appendingPathComponent("segment-001.m4a")
        try Data("not audio".utf8).write(to: corrupt)
        let output = directory.appendingPathComponent("QNT9_L01.m4a")
        do { _ = try await SegmentAssembler.assemble([corrupt], to: output); XCTFail("Corrupt segment accepted") }
        catch { XCTAssertFalse(FileManager.default.fileExists(atPath: output.path)) }
    }
}

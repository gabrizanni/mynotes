import Foundation
import AVFoundation

/// Standalone regression checks for machines with Command Line Tools but no XCTest.
@main
struct Regression {
    @MainActor static func main() async throws {
        let root = FileManager.default.temporaryDirectory.appendingPathComponent("capture-regression-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let now = Date()
        let standalone = Lecture(id: "free", sourceID: "manual", courseCode: nil, lessonNumber: nil,
                                 startsAt: now, endsAt: now.addingTimeInterval(60), recordAt: now, stopAt: now.addingTimeInterval(60),
                                 timezone: "UTC", title: "A conversation / with a title", eligibility: "SCHEDULED", reason: "",
                                 recordingID: String(repeating: "a", count: 32))
        precondition(standalone.validTarget([]), "Standalone capture must work without any courses")
        var classified = standalone
        classified.courseCode = "DEMO"
        precondition(classified.validTarget(["DEMO"]), "A classified recording does not require a lesson number")
        classified.lessonNumber = 17
        precondition(classified.validTarget(["DEMO"]) && classified.lectureID == standalone.lectureID, "Association must not change recording identity")
        classified.title = String(repeating: "Long calendar title ", count: 40)
        precondition(classified.validTarget(["DEMO"]), "Full calendar titles are preserved")
        precondition(!standalone.filename.contains("/") && standalone.filename.contains(standalone.recordingID!), "Filename must be safe and unique")
        precondition(classified.filename == "DEMO_L17.m4a", "A catalogue UUID must not replace course/lesson filenames")
        var scheduled = standalone
        scheduled.title = "QNT 14"; scheduled.courseCode = "QUANTITATIVE"; scheduled.lessonNumber = 14
        precondition(scheduled.filename == "QNT_L14.m4a", "Keep the user's short calendar course label")
        scheduled.title = "STAT 12"; scheduled.courseCode = nil; scheduled.lessonNumber = nil
        precondition(scheduled.filename == "STAT_L12.m4a" && scheduled.courseCode == nil, "Naming must not invent a course association")
        scheduled.title = "MATH_L03"; scheduled.courseCode = "MATHEMATICS"; scheduled.lessonNumber = 3
        precondition(scheduled.filename == "MATH_L03.m4a", "Canonical titles remain canonical")
        scheduled.lessonNumber = 4
        precondition(scheduled.filename == "MATHEMATICS_L04.m4a", "An explicit lesson number wins over an old title")
        let encodedStandalone = try CaptureJSON.encoder.encode(standalone)
        let decodedStandalone = try CaptureJSON.decoder.decode(Lecture.self, from: encodedStandalone)
        precondition(decodedStandalone.courseCode == nil && decodedStandalone.lessonNumber == nil && decodedStandalone.recordingID == standalone.recordingID)
        let event = Lecture(id: "one", sourceID: "static", courseCode: "QNT9", lessonNumber: 1,
                            startsAt: now.addingTimeInterval(-20), endsAt: now.addingTimeInterval(60),
                            recordAt: now.addingTimeInterval(-20), stopAt: now.addingTimeInterval(180),
                            timezone: "UTC", title: "Quantitative Methods", eligibility: "SCHEDULED", reason: "")
        let config = CaptureConfiguration(revision: 1, enabled: true, paused: false, refreshHours: 6,
                                          recordBeforeSeconds: 0, recordAfterSeconds: 120,
                                          outputDirectory: root.appendingPathComponent("recordings").path,
                                          stageDirectory: root.appendingPathComponent("staging").path,
                                          pythonExecutable: "/usr/bin/false", courseCodes: ["QNT9"])
        try CaptureJSON.save(config, to: root.appendingPathComponent("config/capture.json"))
        try CaptureJSON.save(Schedule(schemaVersion: 1, revision: 1, events: [event]), to: root.appendingPathComponent("state/capture/schedule.json"))
        var stale = Session(id: UUID().uuidString, event: event, directory: root.appendingPathComponent("attempt").path,
                            actualStart: now, status: "RECORDING", partial: false, reason: "", segments: ["one"], isTest: false)
        let replayID = UUID().uuidString
        stale.requestID = replayID
        try CaptureJSON.save(stale, to: root.appendingPathComponent("state/capture/sessions/\(stale.id).json"))
        let engine = try CaptureEngine(workspace: root, authorization: { false })
        precondition(engine.sessions.first?.status == "INTERRUPTED", "Restart must preserve interrupted session")
        let requestDirectory = root.appendingPathComponent("state/capture/requests")
        let responseDirectory = root.appendingPathComponent("state/capture/responses")
        let expiredID = UUID().uuidString
        let expired = ControlRequest(schemaVersion: 1, id: expiredID, action: "start", createdAt: now.addingTimeInterval(-120),
                                     expiresAt: now.addingTimeInterval(-60), payload: ControlPayload(event: event))
        try CaptureJSON.save(expired, to: requestDirectory.appendingPathComponent("\(expiredID).json"))
        let replay = ControlRequest(schemaVersion: 1, id: replayID, action: "start", createdAt: now,
                                    expiresAt: now.addingTimeInterval(60), payload: ControlPayload(event: event))
        try CaptureJSON.save(replay, to: requestDirectory.appendingPathComponent("\(replayID).json"))
        engine.evaluate(now: now)
        let expiredResponse = try CaptureJSON.load(ControlResponse.self, from: responseDirectory.appendingPathComponent("\(expiredID).json"))
        let replayResponse = try CaptureJSON.load(ControlResponse.self, from: responseDirectory.appendingPathComponent("\(replayID).json"))
        precondition(expiredResponse.status == "REJECTED", "Expired commands must not execute")
        precondition(replayResponse.sessionID == stale.id && replayResponse.status == "INTERRUPTED", "Persisted request identity must prevent replay")
        try CaptureJSON.save(replay, to: requestDirectory.appendingPathComponent("\(replayID).json"))
        engine.evaluate(now: now)
        let unchangedResponse = try CaptureJSON.load(ControlResponse.self, from: responseDirectory.appendingPathComponent("\(replayID).json"))
        precondition(unchangedResponse.sessionID == stale.id && !FileManager.default.fileExists(atPath: requestDirectory.appendingPathComponent("\(replayID).json").path), "Duplicate request must retain acknowledgement")
        precondition(!engine.isRecording, "Denied microphone must not claim REC")
        let deniedRuntime = try CaptureJSON.load(RuntimeStatus.self, from: root.appendingPathComponent("state/capture/runtime.json"))
        precondition(deniedRuntime.title == "Recording failed" && deniedRuntime.detail.contains("microphone"), "Failed recording must remain visible instead of showing the next lecture")
        engine.interrupt()
        engine.evaluate(now: now)
        precondition(!engine.isRecording, "Sleeping app must not record")
        engine.shutdown()
        let first = Session(id: "one", event: event, directory: "one", actualStart: now, actualStop: now.addingTimeInterval(-60), status: "INTERRUPTED", partial: true, reason: "", segments: ["one"], isTest: false)
        var second = first; second.id = "two"; second.segments = ["one", "two"]
        second.reason = "Resumed after interruption; audio contains a gap"
        precondition(second.finalReason("Scheduled end").contains("audio contains a gap"), "Scheduled completion must retain partial coverage explanation")
        precondition(second.finalReason("Stopped manually") == "Stopped manually", "Explicit stop reasons remain actionable")
        precondition(RecoveryPolicy.unambiguous([first, second]).isEmpty, "Multiple attempts require explicit selection")
        let lockURL = root.appendingPathComponent("lock")
        let lock = try InstanceLock(url: lockURL)
        var collision = false
        do { _ = try InstanceLock(url: lockURL) } catch { collision = true }
        withExtendedLifetime(lock) { precondition(collision, "Single-instance lock must be exclusive") }

        let format = AVAudioFormat(standardFormatWithSampleRate: 44_100, channels: 1)!
        let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: 44_100)!
        buffer.frameLength = 44_100
        for i in 0..<44_100 { buffer.floatChannelData![0][i] = Float(sin(Double(i) * 2 * .pi * 440 / 44_100) * 0.1) }
        var segments: [URL] = []
        for i in 1...3 {
            let url = root.appendingPathComponent("segment-\(i).caf")
            try AVAudioFile(forWriting: url, settings: format.settings).write(from: buffer)
            segments.append(url)
        }
        let output = root.appendingPathComponent("QNT9_L01.m4a")
        let duration = try await SegmentAssembler.assemble(segments, to: output)
        precondition(abs(duration - 3) < 0.1, "Audio assembly must preserve segment duration")
        let original = try Data(contentsOf: output)
        var protected = false
        do { _ = try await SegmentAssembler.assemble(segments, to: output) } catch { protected = true }
        let unchanged = try Data(contentsOf: output)
        precondition(protected && unchanged == original, "Existing audio must never be overwritten")
        let corrupt = root.appendingPathComponent("corrupt.m4a")
        try Data("not audio".utf8).write(to: corrupt)
        let invalidOutput = root.appendingPathComponent("invalid.m4a")
        var rejected = false
        do { _ = try await SegmentAssembler.assemble([corrupt], to: invalidOutput) } catch { rejected = true }
        precondition(rejected && !FileManager.default.fileExists(atPath: invalidOutput.path), "Corrupt segments must fail export")
        print("PASS: stale-state recovery, expired commands, persisted command replay, duplicate acknowledgement, denied microphone, sleep guard, attempt selection, single instance, synthetic audio assembly, existing-file protection, corrupt-segment rejection")
    }
}

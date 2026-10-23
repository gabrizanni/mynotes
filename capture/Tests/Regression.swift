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
        precondition(event.isCurrent(event.recordAt) && !event.isCurrent(event.endsAt), "Fresh starts must end at lesson end, not post-roll")
        precondition(!event.isCurrent(event.recordAt.addingTimeInterval(-1)), "Recording must not start before its window")
        var updated = event; updated.title = "A descriptive title change"; updated.timezone = "Asia/Kolkata"
        precondition(event.canResume(with: updated), "Cosmetic calendar changes must not strand an interrupted recording")
        updated.endsAt = event.endsAt.addingTimeInterval(60)
        precondition(!event.canResume(with: updated), "Changed deadlines must not reuse an obsolete session snapshot")
        var obsolete = event
        obsolete.id = "old-review"; obsolete.courseCode = "OLDCOURSE"; obsolete.eligibility = "NEEDS_REVIEW"
        var invalid = event
        invalid.id = "invalid-scheduled"; invalid.courseCode = "OLDCOURSE"; invalid.title = "Invalid future lesson"
        let checked = Schedule(schemaVersion: 1, revision: 1, events: [obsolete, invalid, event]).recordingEvents(["QNT9"])
        precondition(checked.issues.count == 1 && checked.events.first(where: { $0.id == event.id })?.eligibility == "SCHEDULED", "An obsolete blocked event and a quarantined invalid event must not stop valid lessons")
        var duplicate = event; duplicate.id = "two"
        let duplicates = Schedule(schemaVersion: 1, revision: 1, events: [event, duplicate]).recordingEvents(["QNT9"])
        precondition(duplicates.events.allSatisfy { $0.eligibility == "NEEDS_REVIEW" }, "Duplicate output names must be explicit conflicts")
        duplicate.title = "Distinct title"; duplicate.lessonNumber = 2; duplicate.id = event.id
        precondition(Schedule(schemaVersion: 1, revision: 1, events: [event, duplicate]).recordingEvents(["QNT9"]).issues.count == 2, "Duplicate event identity must block both entries")
        duplicate.courseCode = "OLDCOURSE"
        precondition(Schedule(schemaVersion: 1, revision: 1, events: [event, duplicate]).recordingEvents(["QNT9"]).issues.count == 2, "An invalid duplicate identity must not leave an ambiguous valid entry runnable")
        let config = CaptureConfiguration(revision: 1, enabled: true, paused: false, refreshHours: 6,
                                          recordBeforeSeconds: 0, recordAfterSeconds: 120,
                                          outputDirectory: root.appendingPathComponent("recordings").path,
                                          stageDirectory: root.appendingPathComponent("staging").path,
                                          pythonExecutable: "/usr/bin/false", courseCodes: ["QNT9"])
        try CaptureJSON.save(config, to: root.appendingPathComponent("config/capture.json"))
        try CaptureJSON.save(Schedule(schemaVersion: 1, revision: 1, events: [event, obsolete, invalid]), to: root.appendingPathComponent("state/capture/schedule.json"))
        var stale = Session(id: UUID().uuidString, event: event, directory: root.appendingPathComponent("attempt").path,
                            actualStart: now, status: "RECORDING", partial: false, reason: "", segments: ["one"], isTest: false)
        let replayID = UUID().uuidString
        stale.requestID = replayID
        try CaptureJSON.save(stale, to: root.appendingPathComponent("state/capture/sessions/\(stale.id).json"))
        do {
        var authorizationChecks = 0
        var authorizeOnce = false
        let engine = try CaptureEngine(workspace: root, authorization: {
            authorizationChecks += 1
            if authorizeOnce { authorizeOnce = false; return true }
            return false
        })
        var named = scheduled
        named.title = "QNT 03"; named.courseCode = "QNT9"; named.lessonNumber = 3
        let legacyOutput = URL(fileURLWithPath: config.outputDirectory).appendingPathComponent(named.legacyFilename)
        try FileManager.default.createDirectory(at: legacyOutput.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data("previous original".utf8).write(to: legacyOutput)
        precondition(engine.outputExists(named), "Existing UUID-named outputs must prevent new recordings after a naming upgrade")
        try FileManager.default.removeItem(at: legacyOutput)
        let canonicalOutput = legacyOutput.deletingLastPathComponent().appendingPathComponent(named.filename)
        try Data("renamed original".utf8).write(to: canonicalOutput)
        precondition(engine.outputExists(named), "Canonical outputs must also prevent duplicate acquisition")
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
        precondition(deniedRuntime.scheduleIssues?.count == 1, "Native quarantine must be disclosed to the dashboard")
        let previousAuthorizationChecks = authorizationChecks
        authorizeOnce = true
        engine.evaluate(now: now)
        precondition(authorizationChecks == previousAuthorizationChecks + 2, "An authorization transition must retry the blocked event; this fixture denies again before microphone acquisition")
        let sentinel = root.appendingPathComponent("segments")
        try FileManager.default.createDirectory(at: sentinel, withIntermediateDirectories: true)
        let unjournalled = sentinel.appendingPathComponent("segment-001.m4a")
        let protectedSegment = Data("unjournalled audio must survive".utf8)
        try protectedSegment.write(to: unjournalled)
        let reserved = try SegmentedRecorder.reserveSegment(in: sentinel, startingAt: 1)
        precondition(reserved.lastPathComponent == "segment-002.m4a", "Crash leftovers must not be reused")
        let unchangedSegment = try Data(contentsOf: unjournalled)
        precondition(unchangedSegment == protectedSegment, "Unjournalled segments must remain byte-identical")
        engine.interrupt()
        engine.evaluate(now: now)
        precondition(!engine.isRecording, "Sleeping app must not record")
        engine.shutdown()
        }
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

        if CommandLine.arguments.contains("--scheduler-only") {
            print("PASS: calendar quarantine, duplicate identity/output protection, start deadlines, permission-transition retry without microphone, crash segment protection, stale-state recovery, command replay, sleep guard, attempt selection, single instance")
            return
        }

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
        // Restart inside the recovery settling period, then advance the scheduler.
        // The input is a generated tone, never microphone audio.
        let recoveryRoot = root.appendingPathComponent("recovery-workspace")
        var recoveryConfig = config
        recoveryConfig.outputDirectory = recoveryRoot.appendingPathComponent("recordings").path
        recoveryConfig.stageDirectory = recoveryRoot.appendingPathComponent("staging").path
        try CaptureJSON.save(recoveryConfig, to: recoveryRoot.appendingPathComponent("config/capture.json"))
        var expiredEvent = event
        expiredEvent.startsAt = now.addingTimeInterval(-600)
        expiredEvent.recordAt = expiredEvent.startsAt
        expiredEvent.endsAt = now.addingTimeInterval(-300)
        expiredEvent.stopAt = now.addingTimeInterval(-180)
        try CaptureJSON.save(Schedule(schemaVersion: 1, revision: 1, events: [expiredEvent]), to: recoveryRoot.appendingPathComponent("state/capture/schedule.json"))
        var recoverable = stale
        recoverable.event = expiredEvent; recoverable.requestID = nil
        recoverable.actualStop = nil; recoverable.segments = segments.map(\.path)
        try CaptureJSON.save(recoverable, to: recoveryRoot.appendingPathComponent("state/capture/sessions/\(recoverable.id).json"))
        let recoveryEngine = try CaptureEngine(workspace: recoveryRoot, authorization: { false })
        recoveryEngine.evaluate(now: Date())
        precondition(recoveryEngine.sessions.first?.status == "INTERRUPTED", "Fresh restart must wait for the settling period")
        recoveryEngine.evaluate(now: Date().addingTimeInterval(16))
        for _ in 0..<100 where recoveryEngine.sessions.first?.status == "INTERRUPTED" {
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        precondition(recoveryEngine.sessions.first?.status == "PARTIAL", "Periodic evaluation must recover staged audio after settling, without another restart or wake")
        let recoveredPath = recoveryEngine.sessions.first?.outputPath ?? ""
        precondition(FileManager.default.fileExists(atPath: recoveredPath), "Recovered audio must actually exist")
        let recoveredBytes = try Data(contentsOf: URL(fileURLWithPath: recoveredPath))
        recoveryEngine.evaluate(now: Date().addingTimeInterval(32))
        let retainedRecoveredBytes = try Data(contentsOf: URL(fileURLWithPath: recoveredPath))
        precondition(recoveredBytes == retainedRecoveredBytes, "Repeated evaluation must not overwrite recovered output")
        recoveryEngine.shutdown()
        print("PASS: calendar quarantine, deadline and permission-transition guards, crash segment protection, stale-state recovery, expired commands, replay, sleep guard, attempt selection, single instance, synthetic audio assembly, original-file protection, corrupt-segment rejection, periodic staged recovery with real synthetic output")
    }
}

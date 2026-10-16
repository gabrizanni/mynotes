@preconcurrency import AVFoundation
import Foundation
import Darwin

@MainActor
final class SegmentedRecorder: NSObject, AVAudioRecorderDelegate {
    private var recorder: AVAudioRecorder?
    private var timer: Timer?
    private let directory: URL
    private let segmentSeconds: TimeInterval
    private(set) var segments: [URL] = []
    var onSegment: (([URL]) -> Void)?
    var onError: ((Error) -> Void)?
    var isRecording: Bool { recorder?.isRecording == true }

    init(directory: URL, segmentSeconds: TimeInterval = 300, initialSegments: [URL] = []) {
        self.directory = directory
        self.segmentSeconds = segmentSeconds
        self.segments = initialSegments
    }
    func start() throws {
        try FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
        try startSegment()
        timer = Timer.scheduledTimer(withTimeInterval: segmentSeconds, repeats: true) { [weak self] _ in
            Task { @MainActor in
                guard let self else { return }
                self.recorder?.stop()
                self.recorder = nil
                do { try self.startSegment() }
                catch { self.timer?.invalidate(); self.onError?(error) }
            }
        }
    }
    private func startSegment() throws {
        let url = directory.appendingPathComponent(String(format: "segment-%03d.m4a", segments.count + 1))
        let next = try AVAudioRecorder(url: url, settings: [
            AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: 44_100,
            AVNumberOfChannelsKey: 1, AVEncoderBitRateKey: 128_000,
            AVEncoderAudioQualityKey: AVAudioQuality.high.rawValue,
        ])
        next.delegate = self
        guard next.prepareToRecord(), next.record(), next.isRecording else {
            throw CaptureFailure.message("The microphone recorder did not start")
        }
        recorder = next
        segments.append(url)
        onSegment?(segments)
    }
    func stop() -> [URL] {
        timer?.invalidate(); timer = nil
        recorder?.stop(); recorder = nil
        return segments
    }
    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor [weak self] in
            guard let self, self.recorder === recorder else { return }
            self.onError?(error ?? CaptureFailure.message("Audio encoding failed"))
        }
    }
    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor [weak self] in
            guard let self, self.recorder === recorder else { return }
            self.onError?(CaptureFailure.message(flag ? "The recorder stopped unexpectedly" : "Audio recording failed"))
        }
    }
}

@MainActor
enum SegmentAssembler {
    static func assemble(_ segments: [URL], to destination: URL) async throws -> Double {
        guard !FileManager.default.fileExists(atPath: destination.path) else {
            throw CaptureFailure.message("Existing recording retained")
        }
        guard !segments.isEmpty else { throw CaptureFailure.message("No recorded segments") }
        let composition = AVMutableComposition()
        guard let target = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) else {
            throw CaptureFailure.message("Could not create output track")
        }
        var cursor = CMTime.zero
        for url in segments {
            let asset = AVURLAsset(url: url)
            let tracks = try await asset.loadTracks(withMediaType: .audio)
            let duration = try await asset.load(.duration)
            guard let track = tracks.first, duration.isNumeric, duration.seconds > 0 else {
                throw CaptureFailure.message("Missing or unreadable segment: \(url.lastPathComponent)")
            }
            try target.insertTimeRange(CMTimeRange(start: .zero, duration: duration), of: track, at: cursor)
            cursor = cursor + duration
        }
        try FileManager.default.createDirectory(at: destination.deletingLastPathComponent(), withIntermediateDirectories: true)
        let temporary = destination.deletingLastPathComponent().appendingPathComponent(".\(UUID().uuidString).partial.m4a")
        defer { try? FileManager.default.removeItem(at: temporary) }
        guard let exporter = AVAssetExportSession(asset: composition, presetName: AVAssetExportPresetAppleM4A) else {
            throw CaptureFailure.message("Could not initialize audio export")
        }
        exporter.outputURL = temporary
        exporter.outputFileType = .m4a
        try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<Void, Error>) in
            exporter.exportAsynchronously {
                if exporter.status == .completed { continuation.resume() }
                else { continuation.resume(throwing: exporter.error ?? CaptureFailure.message("Audio export failed")) }
            }
        }
        let duration = try await AVURLAsset(url: temporary).load(.duration).seconds
        guard duration.isFinite, duration > 0, abs(duration - cursor.seconds) < 1 else {
            throw CaptureFailure.message("Exported audio duration did not match its segments")
        }
        // An exclusive same-filesystem rename publishes the complete file without
        // a check-then-overwrite race, including when another writer creates it.
        if renameatx_np(AT_FDCWD, temporary.path, AT_FDCWD, destination.path, UInt32(RENAME_EXCL)) != 0 {
            throw CaptureFailure.message("Cannot publish audio without overwriting: \(String(cString: strerror(errno)))")
        }
        return duration
    }
}

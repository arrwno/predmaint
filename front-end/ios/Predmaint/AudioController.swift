import AVFoundation
import Combine
import Foundation
import RecordingCore
import UIKit

@MainActor
final class AudioController: NSObject, ObservableObject, AVAudioRecorderDelegate, AVAudioPlayerDelegate {
    enum Phase: Equatable {
        case ready, permission, recording, saving, playing(UUID), unavailable
    }

    @Published private(set) var phase: Phase = .unavailable
    @Published private(set) var recordings: [Recording] = []
    @Published private(set) var issues: [String] = []
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var permissionDenied = false
    @Published var message: String?

    private var store: RecordingStore?
    private var recorder: AVAudioRecorder?
    private var player: AVAudioPlayer?
    private var currentID: UUID?
    private var startedAt: Date?
    private var currentURL: URL?
    private var timer: Timer?
    private var observers: [NSObjectProtocol] = []
    private var active = true
    private var permissionRequest = UUID()
    private var permissionGrantedPending = false

    override init() {
        super.init()
        observe(AVAudioSession.interruptionNotification) { controller, notification in
            guard let raw = notification.userInfo?[AVAudioSessionInterruptionTypeKey] as? UInt,
                  AVAudioSession.InterruptionType(rawValue: raw) == .began else { return }
            controller.stopForInterruption("Lydøkten ble avbrutt.")
        }
        observe(AVAudioSession.routeChangeNotification) { controller, notification in
            guard let raw = notification.userInfo?[AVAudioSessionRouteChangeReasonKey] as? UInt,
                  let reason = AVAudioSession.RouteChangeReason(rawValue: raw),
                  reason == .newDeviceAvailable || reason == .oldDeviceUnavailable ||
                    reason == .noSuitableRouteForCategory || reason == .routeConfigurationChange else { return }
            controller.stopForInterruption("Mikrofontilkoblingen ble endret.")
        }
        observe(AVAudioSession.mediaServicesWereResetNotification) { controller, _ in
            controller.stopForInterruption("Lydtjenesten ble startet på nytt.")
        }
        observe(UIApplication.protectedDataWillBecomeUnavailableNotification) { controller, _ in
            controller.setActive(false, cancelPermission: true)
            controller.stopForInterruption("Telefonen ble låst.")
        }
        configureStorage()
    }

    func setActive(_ value: Bool, cancelPermission: Bool = false) {
        active = value
        if value {
            permissionDenied = AVAudioApplication.shared.recordPermission == .denied
            if phase == .permission, permissionGrantedPending {
                permissionGrantedPending = false
                if permissionDenied {
                    phase = .ready
                    message = "Mikrofontilgang er ikke lenger tilgjengelig."
                } else {
                    beginRecording()
                }
                return
            }
            if recorder == nil, player == nil, phase != .permission {
                configureStorage()
            }
        } else {
            stopForInterruption("Appen forlot forgrunnen.")
            if cancelPermission {
                permissionRequest = UUID()
                permissionGrantedPending = false
                if phase == .permission { phase = .ready }
            }
        }
    }

    func startRecording() {
        guard active, phase == .ready, store != nil else { return }
        phase = .permission
        let request = UUID()
        permissionRequest = request
        AVAudioApplication.requestRecordPermission { [weak self] allowed in
            Task { @MainActor in
                guard let self, self.permissionRequest == request, self.phase == .permission else { return }
                self.permissionDenied = !allowed
                guard allowed else {
                    self.phase = .ready
                    self.message = "Mikrofontilgang mangler. Tillat tilgang i Innstillinger for å ta opp."
                    return
                }
                guard self.active else {
                    self.permissionGrantedPending = true
                    return
                }
                self.beginRecording()
            }
        }
    }

    func stopRecording(reason: String? = nil) {
        guard phase == .recording, let recorder else { return }
        phase = .saving
        stopTimer()
        recorder.stop()
        self.recorder = nil
        finishRecording(reason: reason)
    }

    func play(_ recording: Recording) {
        guard active, phase == .ready, let store else { return }
        do {
            let url = try store.audioURL(filename: recording.filename)
            _ = try validatedDuration(url)
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playback, mode: .default)
            try session.setActive(true)
            let next = try AVAudioPlayer(contentsOf: url)
            next.delegate = self
            guard next.prepareToPlay(), next.play() else { throw AudioFailure.playback }
            player = next
            phase = .playing(recording.id)
        } catch {
            player?.stop()
            player = nil
            phase = .ready
            report(error, prefix: "Kunne ikke spille av.")
            deactivateSession()
        }
    }

    func stopPlayback() {
        guard player != nil else { return }
        player?.stop()
        player = nil
        phase = .ready
        deactivateSession()
    }

    private func configureStorage() {
        do {
            let support = try FileManager.default.url(
                for: .applicationSupportDirectory, in: .userDomainMask,
                appropriateFor: nil, create: true
            )
            let next = try RecordingStore(root: support.appendingPathComponent("Recordings").resolvingSymlinksInPath(),
                                          protect: Self.protect)
            let snapshot = try next.load()
            store = next
            recordings = snapshot.recordings
            issues = snapshot.issues
            phase = .ready
        } catch {
            store = nil
            phase = .unavailable
            report(error, prefix: "Opptakslagring er utilgjengelig. Ingen filer er slettet.")
        }
    }

    private func beginRecording() {
        permissionGrantedPending = false
        guard let store else { phase = .unavailable; return }
        do {
            let session = AVAudioSession.sharedInstance()
            try session.setCategory(.playAndRecord, mode: .measurement, options: [.defaultToSpeaker])
            try session.setPreferredSampleRate(48_000)
            try session.setActive(true)
            let id = UUID()
            let url = try store.prepare(id: id)
            currentID = id
            currentURL = url
            startedAt = Date()
            let settings: [String: Any] = [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: 48_000,
                AVNumberOfChannelsKey: 1,
                AVLinearPCMBitDepthKey: 16,
                AVLinearPCMIsFloatKey: false,
                AVLinearPCMIsBigEndianKey: false
            ]
            let next = try AVAudioRecorder(url: url, settings: settings)
            next.delegate = self
            guard next.prepareToRecord() else { throw AudioFailure.recording }
            try store.applyProtection(to: url)
            guard next.record(forDuration: RecordingStore.maximumDuration) else {
                throw AudioFailure.recording
            }
            recorder = next
            elapsed = 0
            phase = .recording
            timer = Timer.scheduledTimer(withTimeInterval: 0.1, repeats: true) { [weak self] _ in
                Task { @MainActor in
                    guard let self, self.phase == .recording else { return }
                    self.elapsed = self.recorder?.currentTime ?? self.elapsed
                }
            }
        } catch {
            recorder?.stop()
            recorder = nil
            clearCurrent()
            phase = .ready
            report(error, prefix: "Kunne ikke starte opptaket.")
            refresh()
            deactivateSession()
        }
    }

    private func finishRecording(reason: String?) {
        defer {
            clearCurrent()
            deactivateSession()
        }
        guard let store, let id = currentID, let url = currentURL, let created = startedAt else {
            phase = .unavailable
            message = "Opptakstilstanden er ugyldig. Filene er bevart."
            return
        }
        do {
            let duration = try validatedDuration(url)
            try store.applyProtection(to: url)
            try store.save(Recording(id: id, createdAt: created, duration: duration, interruption: reason))
            phase = .ready
            refresh()
            if let reason { message = "\(reason) Et delopptak er lagret og merket som ufullstendig." }
        } catch {
            phase = .ready
            report(error, prefix: "Opptaket kunne ikke registreres. Lydfilen er bevart for kontroll.")
            refresh()
        }
    }

    private func refresh() {
        guard let store else { return }
        do {
            let snapshot = try store.load()
            recordings = snapshot.recordings
            issues = snapshot.issues
        } catch {
            phase = .unavailable
            self.store = nil
            report(error, prefix: "Opptaksregisteret kunne ikke leses.")
        }
    }

    private func validatedDuration(_ url: URL) throws -> TimeInterval {
        let audio = try AVAudioFile(forReading: url)
        let format = audio.fileFormat.streamDescription.pointee
        guard format.mFormatID == kAudioFormatLinearPCM, format.mBitsPerChannel == 16,
              format.mChannelsPerFrame == 1, format.mSampleRate == 48_000 else {
            throw AudioFailure.format
        }
        let duration = Double(audio.length) / format.mSampleRate
        guard duration > 0, duration <= RecordingStore.maximumDuration else {
            throw AudioFailure.duration
        }
        return duration
    }

    nonisolated private static func protect(_ url: URL) throws {
        try FileManager.default.setAttributes([.protectionKey: FileProtectionType.complete],
                                             ofItemAtPath: url.path)
        var protected = url
        var values = URLResourceValues()
        values.isExcludedFromBackup = true
        try protected.setResourceValues(values)
        let attributes = try FileManager.default.attributesOfItem(atPath: url.path)
        let resource = try protected.resourceValues(forKeys: [.isExcludedFromBackupKey])
        guard attributes[.protectionKey] as? FileProtectionType == .complete,
              resource.isExcludedFromBackup == true else {
            throw AudioFailure.protection
        }
    }

    private func stopForInterruption(_ reason: String) {
        if phase == .recording { stopRecording(reason: reason) }
        if player != nil {
            stopPlayback()
            message = "\(reason) Avspillingen er stoppet."
        }
    }

    private func observe(_ name: Notification.Name,
                         action: @escaping @MainActor (AudioController, Notification) -> Void) {
        let observer = NotificationCenter.default.addObserver(forName: name, object: nil, queue: .main) {
            [weak self] notification in
            Task { @MainActor in
                if let self { action(self, notification) }
            }
        }
        observers.append(observer)
    }

    private func stopTimer() {
        timer?.invalidate()
        timer = nil
    }

    private func clearCurrent() {
        stopTimer()
        currentID = nil
        currentURL = nil
        startedAt = nil
        elapsed = 0
    }

    private func deactivateSession() {
        do {
            try AVAudioSession.sharedInstance().setActive(false, options: .notifyOthersOnDeactivation)
        } catch {
            let previous = message.map { "\($0)\n" } ?? ""
            message = previous + "Lydøkten kunne ikke frigjøres. Prøv å åpne appen igjen."
        }
    }

    private func report(_ error: Error, prefix: String) {
        if let known = error as? RecordingStoreError {
            message = "\(prefix) \(known.localizedDescription)"
        } else if let known = error as? AudioFailure {
            message = "\(prefix) \(known.localizedDescription)"
        } else {
            message = "\(prefix) En lyd-, fil- eller tilgangsfeil oppstod. Prøv igjen når telefonen er ulåst."
        }
    }

    nonisolated func audioRecorderDidFinishRecording(_ recorder: AVAudioRecorder, successfully flag: Bool) {
        Task { @MainActor in
            guard self.recorder === recorder, self.phase == .recording else { return }
            self.phase = .saving
            self.stopTimer()
            self.recorder = nil
            if flag {
                self.finishRecording(reason: nil)
            } else {
                self.finishRecording(reason: "Opptaket ble avbrutt av lydsystemet.")
            }
        }
    }

    nonisolated func audioRecorderEncodeErrorDidOccur(_ recorder: AVAudioRecorder, error: Error?) {
        Task { @MainActor in
            guard self.recorder === recorder else { return }
            self.stopRecording(reason: "Det oppstod en feil under lydopptaket.")
        }
    }

    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer, successfully flag: Bool) {
        Task { @MainActor in
            guard self.player === player else { return }
            self.stopPlayback()
            if !flag { self.message = "Avspillingen ble ikke fullført." }
        }
    }

    nonisolated func audioPlayerDecodeErrorDidOccur(_ player: AVAudioPlayer, error: Error?) {
        Task { @MainActor in
            guard self.player === player else { return }
            self.stopPlayback()
            self.message = "Lydfilen kunne ikke dekodes. Filen er ikke slettet."
        }
    }

    deinit {
        timer?.invalidate()
        for observer in observers { NotificationCenter.default.removeObserver(observer) }
    }
}

private enum AudioFailure: LocalizedError {
    case recording, playback, format, duration, protection

    var errorDescription: String? {
        switch self {
        case .recording: return "Mikrofonen kunne ikke starte opptaket."
        case .playback: return "Lydavspillingen kunne ikke starte."
        case .format: return "Lydformatet er ikke WAV/PCM med 48 kHz, mono og 16 bit."
        case .duration: return "Opptaket er tomt eller overskrider 120 sekunder."
        case .protection: return "Filbeskyttelse eller backup-ekskludering kunne ikke bekreftes."
        }
    }
}

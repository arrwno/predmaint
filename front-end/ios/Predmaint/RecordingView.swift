import SwiftUI
import RecordingCore
import UIKit

@MainActor
struct RecordingView: View {
    @ObservedObject var controller: AudioController
    @Environment(\.scenePhase) private var scenePhase

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Label("Bare lokal lagring — ingen opplasting", systemImage: "iphone")
                    Text("Bruk ufølsomme testopptak. Avinstallering kan slette opptakene. Ingen skybackup eller eksport.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                    if controller.phase == .recording {
                        Text("Tar opp: \(controller.elapsed, specifier: "%.1f") / 120 sekunder")
                            .monospacedDigit()
                            .accessibilityLabel("Opptak pågår")
                        Button("Stopp og lagre", systemImage: "stop.circle.fill") {
                            controller.stopRecording()
                        }
                        .tint(.red)
                    } else {
                        Button("Start opptak", systemImage: "mic.circle.fill") {
                            controller.startRecording()
                        }
                        .disabled(controller.phase != .ready)
                    }
                    if controller.phase == .permission { Text("Venter på mikrofontillatelse …") }
                    if controller.phase == .saving { ProgressView("Lagrer opptaket …") }
                    if controller.phase == .unavailable {
                        Text("Lagring er utilgjengelig. Åpne appen igjen når telefonen er ulåst.")
                            .foregroundStyle(.red)
                    }
                    if controller.permissionDenied {
                        Button("Åpne mikrofoninnstillinger", systemImage: "gear") {
                            guard let url = URL(string: UIApplication.openSettingsURLString) else { return }
                            UIApplication.shared.open(url)
                        }
                    }
                } header: {
                    Text("Lydopptak")
                }

                if !controller.issues.isEmpty {
                    Section("Lagringsvarsler") {
                        ForEach(controller.issues, id: \.self) { issue in
                            Label(issue, systemImage: "exclamationmark.triangle")
                                .foregroundStyle(.orange)
                        }
                    }
                }

                Section("Lagrede opptak") {
                    if controller.recordings.isEmpty {
                        Text("Ingen registrerte opptak").foregroundStyle(.secondary)
                    }
                    ForEach(controller.recordings) { recording in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(recording.createdAt, format: .dateTime.day().month().year().hour().minute().second())
                                .font(.headline)
                            Text("\(recording.duration, specifier: "%.1f") sekunder · WAV")
                                .font(.subheadline).foregroundStyle(.secondary)
                            if let reason = recording.interruption {
                                Label("Ufullstendig: \(reason)", systemImage: "exclamationmark.circle")
                                    .font(.footnote).foregroundStyle(.orange)
                            }
                            if controller.phase == .playing(recording.id) {
                                Button("Stopp avspilling", systemImage: "stop.fill") {
                                    controller.stopPlayback()
                                }
                            } else {
                                Button("Spill av", systemImage: "play.fill") {
                                    controller.play(recording)
                                }
                                .disabled(controller.phase != .ready)
                            }
                        }
                        .padding(.vertical, 4)
                    }
                }
            }
            .navigationTitle("Predmaint")
            .alert("Melding", isPresented: Binding(
                get: { controller.message != nil },
                set: { if !$0 { controller.message = nil } }
            )) {
                Button("OK") { controller.message = nil }
            } message: {
                Text(controller.message ?? "")
            }
            .onChange(of: scenePhase, initial: true) { _, phase in
                controller.setActive(phase == .active, cancelPermission: phase == .background)
            }
        }
    }
}

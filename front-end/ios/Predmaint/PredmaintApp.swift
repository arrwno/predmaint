import SwiftUI

@main
@MainActor
struct PredmaintApp: App {
    @StateObject private var controller = AudioController()

    var body: some Scene {
        WindowGroup {
            RecordingView(controller: controller)
        }
    }
}

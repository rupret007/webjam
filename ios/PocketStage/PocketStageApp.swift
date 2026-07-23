import SwiftUI

@main
struct PocketStageApp: App {
    @Environment(\.scenePhase) private var scenePhase
    @StateObject private var connection = StageConnectionModel()

    var body: some Scene {
        WindowGroup {
            PocketStageTabView()
                .environmentObject(connection)
        }
        .onChange(of: scenePhase) { _, phase in
            if phase == .background { connection.interruptForBackground() }
        }
    }
}

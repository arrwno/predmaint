// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "RecordingCore",
    platforms: [.iOS(.v17), .macOS(.v13)],
    products: [
        .library(name: "RecordingCore", targets: ["RecordingCore"]),
        .executable(name: "RecordingCoreChecks", targets: ["RecordingCoreChecks"])
    ],
    targets: [
        .target(name: "RecordingCore"),
        .executableTarget(name: "RecordingCoreChecks", dependencies: ["RecordingCore"],
                          path: "Tests/RecordingCoreTests")
    ]
)

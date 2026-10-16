// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "MyNotesCapture",
    platforms: [.macOS(.v14)],
    products: [.executable(name: "MyNotesCapture", targets: ["MyNotesCapture"])],
    targets: [
        .executableTarget(name: "MyNotesCapture"),
        .testTarget(name: "MyNotesCaptureTests", dependencies: ["MyNotesCapture"]),
    ],
    swiftLanguageModes: [.v5]
)

// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "MyNotesSetup", platforms: [.macOS(.v14)],
    products: [.executable(name: "MyNotesSetup", targets: ["MyNotesSetup"])],
    targets: [.executableTarget(name: "MyNotesSetup")], swiftLanguageModes: [.v5])

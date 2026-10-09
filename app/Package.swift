// swift-tools-version: 6.2
import PackageDescription

let package = Package(
    name: "MirrorBar",
    platforms: [.macOS(.v26)],
    targets: [
        .executableTarget(name: "MirrorBar", path: "Sources/MirrorBar")
    ]
)

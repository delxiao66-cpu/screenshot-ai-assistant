// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "ScreenPilot",
    platforms: [.macOS("14.2")],
    products: [.executable(name: "ScreenPilot", targets: ["ScreenPilot"])],
    targets: [.executableTarget(name: "ScreenPilot")]
)

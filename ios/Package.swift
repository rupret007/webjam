// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "PocketStageProtocol",
    platforms: [
        .iOS(.v17),
        .macOS(.v14)
    ],
    products: [
        .library(name: "PocketStageProtocol", targets: ["PocketStageProtocol"]),
        .library(name: "PocketStageTransport", targets: ["PocketStageTransport"])
    ],
    targets: [
        .target(name: "PocketStageProtocol"),
        .target(
            name: "PocketStageTransport",
            dependencies: ["PocketStageProtocol"],
            path: "PocketStage",
            exclude: [
                "Assets.xcassets",
                "Info.plist",
                "Info.plist.template",
                "PairingQRScanner.swift",
                "PocketStageApp.swift",
                "PocketStageTabView.swift",
                "WebJamBrandHeader.swift"
            ],
            sources: ["StageSocket.swift", "StageConnectionModel.swift"]
        ),
        .testTarget(
            name: "PocketStageProtocolTests",
            dependencies: ["PocketStageProtocol"]
        ),
        .testTarget(
            name: "PocketStageTransportTests",
            dependencies: ["PocketStageProtocol", "PocketStageTransport"]
        )
    ]
)

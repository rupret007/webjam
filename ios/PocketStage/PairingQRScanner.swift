import SwiftUI
import UIKit
import VisionKit

/// Scans QR text only. Invalid Pocket Stage payloads remain visible in the
/// Pair screen and scanning continues; the scanner stops after one payload is
/// accepted by the shared strict PairingPayload parser.
struct PairingQRScanner: View {
    @Environment(\.dismiss) private var dismiss
    @State private var scannerError: String?
    @State private var retryID = UUID()
    let accept: (String) -> Bool

    var body: some View {
        NavigationStack {
            Group {
                if DataScannerViewController.isSupported && DataScannerViewController.isAvailable {
                    DataScannerRepresentable(
                        retryID: retryID,
                        accept: { payload in
                            let accepted = accept(payload)
                            if accepted { dismiss() }
                            return accepted
                        },
                        onFailure: { scannerError = $0 }
                    )
                    .ignoresSafeArea(edges: .bottom)
                } else {
                    ContentUnavailableView(
                        "Scanner unavailable",
                        systemImage: "qrcode.viewfinder",
                        description: Text(
                            "Enable Camera access for Pocket Stage in Settings, "
                            + "then return and scan a fresh desktop code."
                        )
                    )
                }
            }
            .navigationTitle("Scan Pairing QR")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .cancellationAction) { Button("Close") { dismiss() } } }
            .alert("Scanner couldn't start", isPresented: Binding(
                get: { scannerError != nil },
                set: { if !$0 { scannerError = nil } }
            )) {
                Button("Try Again") {
                    scannerError = nil
                    retryID = UUID()
                }
                Button("Close", role: .cancel) { dismiss() }
            } message: {
                Text(scannerError ?? "Check Camera access and try again.")
            }
        }
    }
}

private struct DataScannerRepresentable: UIViewControllerRepresentable {
    let retryID: UUID
    let accept: (String) -> Bool
    let onFailure: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(accept: accept) }

    func makeUIViewController(context: Context) -> ScannerHostViewController {
        let scanner = DataScannerViewController(
            recognizedDataTypes: [.barcode(symbologies: [.qr])],
            qualityLevel: .balanced,
            recognizesMultipleItems: false,
            isHighFrameRateTrackingEnabled: false,
            isPinchToZoomEnabled: true,
            isGuidanceEnabled: true,
            isHighlightingEnabled: true
        )
        scanner.delegate = context.coordinator
        return ScannerHostViewController(
            scanner: scanner,
            retryID: retryID,
            onFailure: onFailure
        )
    }

    func updateUIViewController(_ controller: ScannerHostViewController, context _: Context) {
        controller.retryIfNeeded(retryID)
    }
    func dismantleUIViewController(_ controller: ScannerHostViewController, coordinator _: Coordinator) {
        controller.stop()
    }

    final class Coordinator: NSObject, DataScannerViewControllerDelegate {
        let accept: (String) -> Bool
        private var delivered = false

        init(accept: @escaping (String) -> Bool) { self.accept = accept }

        func dataScanner(_ dataScanner: DataScannerViewController, didAdd addedItems: [RecognizedItem], allItems _: [RecognizedItem]) {
            guard !delivered else { return }
            for item in addedItems {
                guard case let .barcode(barcode) = item, let payload = barcode.payloadStringValue else { continue }
                if accept(payload) {
                    delivered = true
                    dataScanner.stopScanning()
                    return
                }
            }
        }
    }
}

private final class ScannerHostViewController: UIViewController {
    let scanner: DataScannerViewController
    private var retryID: UUID
    private let onFailure: (String) -> Void

    init(
        scanner: DataScannerViewController,
        retryID: UUID,
        onFailure: @escaping (String) -> Void
    ) {
        self.scanner = scanner
        self.retryID = retryID
        self.onFailure = onFailure
        super.init(nibName: nil, bundle: nil)
    }

    @available(*, unavailable)
    required init?(coder: NSCoder) { fatalError("init(coder:) is unavailable") }

    override func viewDidLoad() {
        super.viewDidLoad()
        addChild(scanner)
        scanner.view.translatesAutoresizingMaskIntoConstraints = false
        view.addSubview(scanner.view)
        NSLayoutConstraint.activate([
            scanner.view.leadingAnchor.constraint(equalTo: view.leadingAnchor),
            scanner.view.trailingAnchor.constraint(equalTo: view.trailingAnchor),
            scanner.view.topAnchor.constraint(equalTo: view.topAnchor),
            scanner.view.bottomAnchor.constraint(equalTo: view.bottomAnchor),
        ])
        scanner.didMove(toParent: self)
    }

    override func viewDidAppear(_ animated: Bool) {
        super.viewDidAppear(animated)
        start()
    }

    override func viewWillDisappear(_ animated: Bool) {
        stop()
        super.viewWillDisappear(animated)
    }

    func retryIfNeeded(_ value: UUID) {
        guard value != retryID else { return }
        retryID = value
        if viewIfLoaded?.window != nil { start() }
    }

    func stop() {
        if scanner.isScanning { scanner.stopScanning() }
    }

    private func start() {
        guard !scanner.isScanning else { return }
        do {
            try scanner.startScanning()
        } catch {
            onFailure(
                "Camera scanning is unavailable. Enable Camera access for "
                + "Pocket Stage in Settings, then scan a fresh code."
            )
        }
    }
}

import AudioToolbox
import AVFoundation
import SwiftUI

// The QR's meaning lives in PairingPayload.swift (Foundation only, so the
// Linux harness can test every encoding); this file is just the camera.

/// A live camera preview that reports QR payloads. AVFoundation directly —
/// a scanner is about 60 lines and does not justify a dependency.
struct QRScannerView: UIViewControllerRepresentable {
    var onScan: (String) -> Void
    var onError: (String) -> Void

    func makeCoordinator() -> Coordinator { Coordinator(self) }

    func makeUIViewController(context: Context) -> ScannerController {
        let controller = ScannerController()
        controller.coordinator = context.coordinator
        return controller
    }

    func updateUIViewController(_ controller: ScannerController, context: Context) {}

    final class Coordinator: NSObject, AVCaptureMetadataOutputObjectsDelegate {
        private let parent: QRScannerView
        /// A QR code in frame fires this delegate many times a second; without
        /// a latch the app would claim the same one-time pairing code
        /// repeatedly and every claim after the first would fail.
        private var hasScanned = false

        init(_ parent: QRScannerView) { self.parent = parent }

        func metadataOutput(_ output: AVCaptureMetadataOutput,
                            didOutput objects: [AVMetadataObject],
                            from connection: AVCaptureConnection) {
            guard !hasScanned,
                  let object = objects.first as? AVMetadataMachineReadableCodeObject,
                  object.type == .qr,
                  let value = object.stringValue else { return }
            hasScanned = true
            AudioServicesPlaySystemSound(1108)
            parent.onScan(value)
        }

        func report(_ message: String) { parent.onError(message) }
    }

    final class ScannerController: UIViewController {
        weak var coordinator: Coordinator?
        private let session = AVCaptureSession()
        private var preview: AVCaptureVideoPreviewLayer?

        override func viewDidLoad() {
            super.viewDidLoad()
            view.backgroundColor = .black
            AVCaptureDevice.requestAccess(for: .video) { [weak self] granted in
                DispatchQueue.main.async {
                    guard let self else { return }
                    guard granted else {
                        self.coordinator?.report("Camera access is off for PocketADM. Turn it on in Settings to scan a pairing code.")
                        return
                    }
                    self.configure()
                }
            }
        }

        private func configure() {
            guard let device = AVCaptureDevice.default(for: .video),
                  let input = try? AVCaptureDeviceInput(device: device),
                  session.canAddInput(input) else {
                coordinator?.report("No usable camera on this device.")
                return
            }
            session.addInput(input)

            let output = AVCaptureMetadataOutput()
            guard session.canAddOutput(output) else {
                coordinator?.report("Could not start the camera.")
                return
            }
            session.addOutput(output)
            output.setMetadataObjectsDelegate(coordinator, queue: .main)
            // Must be set *after* the output joins the session — before that
            // `.qr` is not yet an available type and the assignment traps.
            output.metadataObjectTypes = [.qr]

            let layer = AVCaptureVideoPreviewLayer(session: session)
            layer.videoGravity = .resizeAspectFill
            layer.frame = view.bounds
            view.layer.addSublayer(layer)
            preview = layer

            // startRunning blocks; off the main thread or the UI hitches.
            Task.detached { [session] in session.startRunning() }
        }

        override func viewDidLayoutSubviews() {
            super.viewDidLayoutSubviews()
            preview?.frame = view.bounds
        }

        override func viewWillDisappear(_ animated: Bool) {
            super.viewWillDisappear(animated)
            let session = self.session
            Task.detached { session.stopRunning() }
        }
    }
}

import Foundation
import CryptoKit
import Security
import PocketStageProtocol

enum StageSocketEvent: Sendable {
    case opened
    case message(IncomingMessage)
    case closed
    case failed(String)
}

protocol StageSocketClient: AnyObject, Sendable {
    @MainActor var onEvent: (@MainActor @Sendable (StageSocketEvent) -> Void)? { get set }
    @MainActor func connect()
    @MainActor func disconnect()
    @MainActor func send(text: String) async throws
}

/// A pinned WebSocket transport for the desktop's intentionally self-signed
/// leaf certificate. There is no unpinned or public-CA fallback.
final class StageSocket: NSObject, URLSessionWebSocketDelegate, StageSocketClient, @unchecked Sendable {
    typealias Event = StageSocketEvent

    @MainActor var onEvent: (@MainActor @Sendable (Event) -> Void)?
    private let url: URL
    private let pinningDelegate: CertificatePinningDelegate
    private let stateLock = NSLock()
    private let deliveryQueue = DispatchQueue(label: "org.webjam.pocket-stage.events")
    private var session: URLSession?
    private var task: URLSessionWebSocketTask?

    init(url: URL, certificatePin: Data) {
        self.url = url
        self.pinningDelegate = CertificatePinningDelegate(certificatePin: certificatePin)
    }

    @MainActor func connect() {
        guard pinningDelegate.hasValidPin else {
            emit(.failed("The pairing code has no valid certificate fingerprint."))
            return
        }
        let configuration = URLSessionConfiguration.ephemeral
        configuration.waitsForConnectivity = false
        let delegateQueue = OperationQueue()
        delegateQueue.name = "org.webjam.pocket-stage.url-session"
        delegateQueue.maxConcurrentOperationCount = 1
        let session = URLSession(
            configuration: configuration,
            delegate: self,
            delegateQueue: delegateQueue
        )
        let task = session.webSocketTask(with: url)
        stateLock.lock()
        let oldTask = self.task
        let oldSession = self.session
        self.session = session
        self.task = task
        stateLock.unlock()
        oldTask?.cancel(with: .goingAway, reason: nil)
        oldSession?.invalidateAndCancel()
        task.resume()
        receiveNext(for: task)
    }

    @MainActor func disconnect() {
        stateLock.lock()
        let task = self.task
        let session = self.session
        self.task = nil
        self.session = nil
        stateLock.unlock()
        task?.cancel(with: .normalClosure, reason: nil)
        session?.invalidateAndCancel()
    }

    @MainActor func send(text: String) async throws {
        let task = stateLock.withLock { self.task }
        guard let task else { throw URLError(.notConnectedToInternet) }
        try await task.send(.string(text))
    }

    private func receiveNext(for task: URLSessionWebSocketTask) {
        guard isCurrent(task) else { return }
        task.receive { [weak self] result in
            guard let self else { return }
            guard self.isCurrent(task) else { return }
            switch result {
            case let .success(.data(data)):
                do {
                    self.emit(.message(try WireCodec.decodeIncomingMessage(data)))
                    self.receiveNext(for: task)
                } catch {
                    self.fail(task, message: "Invalid server message")
                }
            case let .success(.string(text)):
                do {
                    self.emit(.message(try WireCodec.decodeIncomingMessage(Data(text.utf8))))
                    self.receiveNext(for: task)
                } catch {
                    self.fail(task, message: "Invalid server message")
                }
            case .success:
                self.receiveNext(for: task)
            case let .failure(error):
                self.fail(task, message: error.localizedDescription)
            }
        }
    }

    func urlSession(_: URLSession, webSocketTask: URLSessionWebSocketTask, didOpenWithProtocol _: String?) {
        guard isCurrent(webSocketTask) else { return }
        emit(.opened)
    }

    func urlSession(_: URLSession, webSocketTask: URLSessionWebSocketTask, didCloseWith _: URLSessionWebSocketTask.CloseCode, reason _: Data?) {
        guard retire(webSocketTask) else { return }
        emit(.closed)
    }

    func urlSession(_: URLSession, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        // Foundation delivers a WebSocket server-trust challenge at the
        // session level on Apple platforms. Without this delegate callback a
        // correctly pinned self-signed desktop certificate is rejected before
        // the WebSocket can open.
        pinningDelegate.evaluate(challenge, completionHandler: completionHandler)
    }

    func urlSession(_: URLSession, task: URLSessionTask, didReceive challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        pinningDelegate.evaluate(challenge, completionHandler: completionHandler)
    }

    private func isCurrent(_ candidate: URLSessionWebSocketTask) -> Bool {
        stateLock.lock()
        defer { stateLock.unlock() }
        return task === candidate
    }

    @discardableResult
    private func retire(_ candidate: URLSessionWebSocketTask) -> Bool {
        stateLock.lock()
        guard task === candidate else {
            stateLock.unlock()
            return false
        }
        let session = self.session
        task = nil
        self.session = nil
        stateLock.unlock()
        session?.finishTasksAndInvalidate()
        return true
    }

    private func fail(_ candidate: URLSessionWebSocketTask, message: String) {
        guard retire(candidate) else { return }
        candidate.cancel(with: .goingAway, reason: nil)
        emit(.failed(message))
    }

    private func emit(_ event: Event) {
        deliveryQueue.async { [weak self] in
            DispatchQueue.main.async { [weak self] in
                self?.onEvent?(event)
            }
        }
    }
}

private final class CertificatePinningDelegate: @unchecked Sendable {
    private let certificatePin: Data
    var hasValidPin: Bool { certificatePin.count == SHA256.byteCount }

    init(certificatePin: Data) {
        self.certificatePin = certificatePin
    }

    func evaluate(_ challenge: URLAuthenticationChallenge, completionHandler: @escaping (URLSession.AuthChallengeDisposition, URLCredential?) -> Void) {
        guard challenge.protectionSpace.authenticationMethod == NSURLAuthenticationMethodServerTrust,
              let trust = challenge.protectionSpace.serverTrust,
              hasValidPin,
              let chain = SecTrustCopyCertificateChain(trust) as? [SecCertificate],
              let leaf = chain.first else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let leafDER = SecCertificateCopyData(leaf) as Data
        let observed = Data(SHA256.hash(data: leafDER))
        guard constantTimeEqual(observed, certificatePin) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        let host = challenge.protectionSpace.host as CFString
        let policy = SecPolicyCreateSSL(true, host)
        guard SecTrustSetPolicies(trust, policy) == errSecSuccess,
              SecTrustSetAnchorCertificates(trust, [leaf] as CFArray) == errSecSuccess,
              SecTrustSetAnchorCertificatesOnly(trust, true) == errSecSuccess,
              SecTrustEvaluateWithError(trust, nil) else {
            completionHandler(.cancelAuthenticationChallenge, nil)
            return
        }
        completionHandler(.useCredential, URLCredential(trust: trust))
    }

    private func constantTimeEqual(_ lhs: Data, _ rhs: Data) -> Bool {
        guard lhs.count == rhs.count else { return false }
        var difference: UInt8 = 0
        for (left, right) in zip(lhs, rhs) { difference |= left ^ right }
        return difference == 0
    }
}

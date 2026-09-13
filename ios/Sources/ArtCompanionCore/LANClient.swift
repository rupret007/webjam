import Foundation

struct Enrollment: Sendable, CustomStringConvertible {
    let participantID: String
    let token: String
    var description: String { "Enrollment(private: redacted)" }
}

protocol RoomClient: Sendable {
    func enroll(installationID: String, displayName: String) async throws -> Enrollment
    func read(_ enrollment: Enrollment) async throws -> RoomSummary
    func close()
}

/// Two fixed routes on the literal private IPv4 from a validated invitation.
/// URLSession is thread safe; all other stored members are immutable.
final class LANClient: NSObject, RoomClient, @unchecked Sendable {
    private let invitation: Invitation
    private let session: URLSession
    private let responses: BoundedHTTPResponses

    init(invitation: Invitation, configuration: URLSessionConfiguration = .ephemeral) {
        self.invitation = invitation
        let responses = BoundedHTTPResponses()
        self.responses = responses
        configuration.urlCache = nil
        configuration.requestCachePolicy = .reloadIgnoringLocalCacheData
        configuration.httpCookieStorage = nil
        configuration.httpShouldSetCookies = false
        configuration.urlCredentialStorage = nil
        configuration.connectionProxyDictionary = [:]
        configuration.timeoutIntervalForRequest = 3
        configuration.timeoutIntervalForResource = 4
        configuration.waitsForConnectivity = false
        session = URLSession(configuration: configuration, delegate: responses, delegateQueue: nil)
        super.init()
    }

    deinit { close() }

    func close() {
        responses.close()
        session.invalidateAndCancel()
    }

    private func request(_ path: String, token: String, participant: String? = nil,
                         body: Data? = nil) async throws -> Data {
        var request = URLRequest(url: invitation.endpoint.appendingPathComponent(path))
        request.httpMethod = body == nil ? "GET" : "POST"
        request.httpBody = body
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        if let participant { request.setValue(participant, forHTTPHeaderField: "X-WebJam-Participant") }
        if body != nil { request.setValue("application/json", forHTTPHeaderField: "Content-Type") }
        let task = session.dataTask(with: request)
        // Register before installing the cancellation handler so cancellation
        // cannot slip between task creation and continuation ownership.
        responses.register(task)
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { continuation in
                responses.start(task, continuation: continuation)
            }
        } onCancel: {
            responses.cancel(task)
        }
    }

    func enroll(installationID: String, displayName: String) async throws -> Enrollment {
        let body = try JSONEncoder().encode(["installation_id": installationID, "display_name": displayName])
        let data = try await request("v1/enroll", token: invitation.token, body: body)
        _ = try StrictJSON.object(data)
        struct Reply: Decodable { let participant_id: String; let participant_token: String; let installation_id: String }
        guard let reply = try? JSONDecoder().decode(Reply.self, from: data),
              Invitation.canonicalUUID(reply.participant_id), reply.installation_id == installationID,
              Invitation.validToken(reply.participant_token) else { throw CompanionError.invalidResponse }
        return Enrollment(participantID: reply.participant_id, token: reply.participant_token)
    }

    func read(_ enrollment: Enrollment) async throws -> RoomSummary {
        let data = try await request("v1/state", token: enrollment.token, participant: enrollment.participantID)
        return try RoomSummary.decode(data, sessionID: invitation.sessionID)
    }
}

/// Response validation runs before accumulating body data. CFNetwork may withhold
/// headers until an initial body byte arrives; an unobserved response stays
/// unavailable at the bounded timeout instead of inventing authentication truth.
/// All mutable request state is protected by lock; continuations resume outside it.
private final class BoundedHTTPResponses: NSObject, URLSessionDataDelegate, @unchecked Sendable {
    private struct Pending {
        let task: URLSessionDataTask
        var continuation: CheckedContinuation<Data, Error>?
        var result: Result<Data, Error>?
        var accepted = false
        var data = Data()
    }

    private let lock = NSLock()
    private var pending: [Int: Pending] = [:]
    private var closed = false

    func register(_ task: URLSessionDataTask) {
        lock.withLock {
            pending[task.taskIdentifier] = Pending(
                task: task, result: closed ? .failure(CancellationError()) : nil
            )
        }
    }

    func start(_ task: URLSessionDataTask, continuation: CheckedContinuation<Data, Error>) {
        let early: Result<Data, Error>? = lock.withLock {
            guard var entry = pending[task.taskIdentifier] else { return .failure(CancellationError()) }
            if let result = entry.result {
                pending.removeValue(forKey: task.taskIdentifier)
                return result
            }
            entry.continuation = continuation
            pending[task.taskIdentifier] = entry
            task.resume()
            return nil
        }
        if let early {
            task.cancel()
            continuation.resume(with: early)
        }
    }

    func cancel(_ task: URLSessionDataTask) {
        finish(task, result: .failure(CancellationError()))
        task.cancel()
    }

    func close() {
        let retired = lock.withLock {
            closed = true
            let entries = Array(pending.values)
            for var entry in entries {
                if entry.continuation != nil {
                    pending.removeValue(forKey: entry.task.taskIdentifier)
                } else {
                    entry.result = .failure(CancellationError())
                    entry.data = Data()
                    pending[entry.task.taskIdentifier] = entry
                }
            }
            return entries
        }
        for entry in retired {
            entry.continuation?.resume(throwing: CancellationError())
            entry.task.cancel()
        }
    }

    private func finish(_ task: URLSessionTask, result: Result<Data, Error>) {
        let continuation: CheckedContinuation<Data, Error>? = lock.withLock {
            guard var entry = pending[task.taskIdentifier], entry.result == nil else { return nil }
            if let continuation = entry.continuation {
                pending.removeValue(forKey: task.taskIdentifier)
                return continuation
            }
            // Cancellation may precede start(). Retain only its terminal result
            // until the continuation arrives, then remove this entry.
            entry.result = result
            entry.data = Data()
            pending[task.taskIdentifier] = entry
            return nil
        }
        continuation?.resume(with: result)
    }

    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest,
                    completionHandler: @escaping @Sendable (URLRequest?) -> Void) {
        finish(task, result: .failure(CompanionError.invalidResponse))
        completionHandler(nil)
        task.cancel()
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask,
                    didReceive response: URLResponse,
                    completionHandler: @escaping @Sendable (URLSession.ResponseDisposition) -> Void) {
        if let failure = responseFailure(response, task: dataTask) {
            finish(dataTask, result: .failure(failure))
            completionHandler(.cancel)
            dataTask.cancel()
            return
        }
        let accepted = lock.withLock {
            guard var entry = pending[dataTask.taskIdentifier], entry.result == nil else { return false }
            entry.accepted = true
            pending[dataTask.taskIdentifier] = entry
            return true
        }
        completionHandler(accepted ? .allow : .cancel)
    }

    func urlSession(_ session: URLSession, dataTask: URLSessionDataTask, didReceive data: Data) {
        let failure: CompanionError? = lock.withLock {
            guard var entry = pending[dataTask.taskIdentifier], entry.result == nil else { return nil }
            guard entry.accepted else { return .invalidResponse }
            guard data.count <= 65536 - entry.data.count else { return .oversizedResponse }
            entry.data.append(data)
            pending[dataTask.taskIdentifier] = entry
            return nil
        }
        if let failure {
            finish(dataTask, result: .failure(failure))
            dataTask.cancel()
        }
    }

    func urlSession(_ session: URLSession, task: URLSessionTask, didCompleteWithError error: Error?) {
        // Some runtimes expose the response only at completion. Observed status
        // still takes precedence over an underlying body/timeout error.
        if let response = task.response, let failure = responseFailure(response, task: task) {
            finish(task, result: .failure(failure))
            return
        }
        let result: Result<Data, Error>? = lock.withLock {
            guard let entry = pending[task.taskIdentifier], entry.result == nil else { return nil }
            if error != nil { return .failure(CompanionError.unavailable) }
            guard entry.accepted else { return .failure(CompanionError.invalidResponse) }
            return .success(entry.data)
        }
        if let result { finish(task, result: result) }
    }

    private func responseFailure(_ response: URLResponse, task: URLSessionTask) -> CompanionError? {
        guard let response = response as? HTTPURLResponse,
              response.url == task.originalRequest?.url else { return .invalidResponse }
        if response.statusCode == 401 || response.statusCode == 403 { return .authentication }
        if response.statusCode != 200 { return .invalidResponse }
        if response.expectedContentLength > 65536 { return .oversizedResponse }
        return nil
    }
}

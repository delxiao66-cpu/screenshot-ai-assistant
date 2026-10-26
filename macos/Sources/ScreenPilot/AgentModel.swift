import AppKit
import ScreenCaptureKit

struct RemoteTask: Decodable {
    let id: String
    let mode: String
    let status: String
    let image_count: Int
    let error: String?
    var label: String {
        ["collecting": "正在收集", "capturing": "正在截图", "processing": "正在分析",
         "superseded": "已被新任务替代", "completed": "分析完成", "failed": "任务失败"][status] ?? status
    }
}
private struct ServerStatus: Decodable { let active_tasks: [RemoteTask] }
private struct Login: Decodable { let token: String }

@MainActor
final class AgentModel: ObservableObject {
    @Published var serverURL = UserDefaults.standard.string(forKey: "serverURL") ?? ""
    @Published var username = UserDefaults.standard.string(forKey: "username") ?? ""
    @Published var password = ""
    @Published var connection = "离线"
    @Published var serverOnline = false
    @Published var current: RemoteTask?
    @Published var batch: RemoteTask?
    @Published var error = ""
    @Published var busy = false
    @Published var isLoggedIn = false
    @Published var displays: [SCDisplay] = []
    @Published var displayID: CGDirectDisplayID = CGMainDisplayID()
    let capture = ScreenCapture()
    let deviceID: String
    private var baseURL: URL?
    private var token: String?
    private var socket: URLSessionWebSocketTask?
    private var connectionJob: Task<Void, Never>?
    private var heartbeatJob: Task<Void, Never>?
    private var pollingJob: Task<Void, Never>?
    private var captureJob: Task<Void, Never>?
    private var captureID: String?
    private var generation = UUID()
    private var lastPong = Date()
    private var rejected = false
    private let session: URLSession = {
        let config = URLSessionConfiguration.ephemeral
        config.timeoutIntervalForRequest = 40
        config.timeoutIntervalForResource = 40
        return URLSession(configuration: config)
    }()

    init() {
        deviceID = UserDefaults.standard.string(forKey: "deviceID") ?? "macos_\(UUID().uuidString)"
        UserDefaults.standard.set(deviceID, forKey: "deviceID")
        token = SessionStore.read()
        baseURL = try? Self.normalized(serverURL)
        isLoggedIn = token != nil && baseURL != nil
        reconnect()
        pollingJob = Task { [weak self] in
            while !Task.isCancelled {
                await self?.refresh()
                try? await Task.sleep(for: .seconds(3))
            }
        }
    }

    static func normalized(_ raw: String) throws -> URL {
        guard let url = URL(string: raw.trimmingCharacters(in: .whitespacesAndNewlines)),
              let host = url.host,
              ((url.scheme == "http" && ["localhost", "127.0.0.1"].contains(host)) ||
               (url.scheme == "https" && host.hasSuffix(".ts.net"))),
              url.user == nil, url.password == nil, url.query == nil, url.fragment == nil,
              ["", "/"].contains(url.path) else {
            throw ClientError(message: "请使用本机 HTTP 地址或 Tailscale HTTPS 根地址，不要带路径、参数或账号。")
        }
        return url
    }

    func perform(_ action: @escaping () async throws -> Void) {
        guard !busy else { return }
        busy = true
        Task {
            defer { busy = false }
            do { error = ""; try await action() }
            catch is CancellationError { }
            catch { self.error = error.localizedDescription }
        }
    }

    func login() async throws {
        let url = try Self.normalized(serverURL)
        var request = URLRequest(url: url.appendingPathComponent("api/auth/login"))
        request.httpMethod = "POST"
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.httpBody = try JSONSerialization.data(withJSONObject: ["username": username.trimmingCharacters(in: .whitespaces), "password": password])
        let result: Login = try await send(request)
        try SessionStore.save(result.token)
        stopConnection()
        baseURL = url
        token = result.token
        serverURL = url.absoluteString
        UserDefaults.standard.set(serverURL, forKey: "serverURL")
        UserDefaults.standard.set(username, forKey: "username")
        password = ""
        current = nil; batch = nil
        isLoggedIn = true
        reconnect()
    }

    private func send<T: Decodable>(_ request: URLRequest) async throws -> T {
        let (data, response) = try await session.data(for: request)
        try Task.checkCancellation()
        guard let response = response as? HTTPURLResponse else { throw ClientError(message: "后端响应无效。") }
        guard (200..<300).contains(response.statusCode) else {
            let payload = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            if response.statusCode == 401 && request.value(forHTTPHeaderField: "Authorization") != nil {
                rejected = true
                stopConnection()
                isLoggedIn = false
                error = "账号会话已失效，请重新登录。"
            }
            throw ClientError(message: payload?["error"] as? String ?? payload?["detail"] as? String ?? "后端请求失败（\(response.statusCode)）。")
        }
        return try JSONDecoder().decode(T.self, from: data)
    }

    private func api<T: Decodable>(_ path: String, method: String = "GET", json: [String: Any]? = nil,
                                   body: Data? = nil, headers: [String: String] = [:]) async throws -> T {
        guard let baseURL, let token else { throw ClientError(message: "请先登录账号。") }
        guard let url = URL(string: path, relativeTo: baseURL) else { throw ClientError(message: "请求地址无效。") }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        for (key, value) in headers { request.setValue(value, forHTTPHeaderField: key) }
        request.httpBody = body
        if let json {
            request.httpBody = try JSONSerialization.data(withJSONObject: json)
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return try await send(request)
    }

    func refresh() async {
        guard token != nil, baseURL != nil, !rejected else { return }
        let epoch = generation
        do {
            let status: ServerStatus = try await api("/api/status")
            guard epoch == generation else { return }
            serverOnline = true
            batch = status.active_tasks.first { $0.mode == "batch" && ["collecting", "capturing"].contains($0.status) }
            if let id = current?.id {
                let updated: RemoteTask = try await api("/api/tasks/\(id)")
                if epoch == generation, current?.id == id { current = updated }
            } else { current = status.active_tasks.first }
        } catch {
            guard epoch == generation else { return }
            serverOnline = false
            self.error = error.localizedDescription
        }
    }

    func startCapture(mode: String) async throws {
        guard token != nil, baseURL != nil else { throw ClientError(message: "请先登录账号。") }
        let task: RemoteTask = try await api("/api/tasks", method: "POST", json: ["mode": mode, "source": "local", "reuse": mode == "batch"])
        current = task
        // Local actions capture immediately and upload directly, avoiding the WebSocket round trip.
        let jpeg = try await capture.jpeg(displayID: displayID)
        let boundary = "ScreenPilot-\(UUID().uuidString)"
        var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"images\"; filename=\"capture.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8)
        body.append(jpeg)
        body.append(Data("\r\n--\(boundary)--\r\n".utf8))
        current = try await api("/api/tasks/\(task.id)/images", method: "POST", body: body,
            headers: ["Content-Type": "multipart/form-data; boundary=\(boundary)"])
        await refresh()
    }

    func analyze() async throws {
        await refresh()
        guard let batch, batch.status == "collecting", batch.image_count > 0 else {
            throw ClientError(message: "当前没有可分析的多图任务，请先添加截图。")
        }
        current = try await api("/api/tasks/\(batch.id)/analyze", method: "POST")
        await refresh()
    }

    func loadDisplays() async throws {
        displays = try await capture.displays(forceRefresh: true)
        if !displays.contains(where: { $0.displayID == displayID }), let first = displays.first {
            displayID = displays.first(where: { $0.displayID == CGMainDisplayID() })?.displayID ?? first.displayID
        }
    }

    func openController() {
        do { NSWorkspace.shared.open(try Self.normalized(serverURL)) }
        catch { self.error = error.localizedDescription }
    }

    func logout() {
        stopConnection()
        token = nil
        baseURL = nil
        isLoggedIn = false
        current = nil
        batch = nil
        password = ""
        try? SessionStore.delete()
    }

    private func stopConnection() {
        generation = UUID()
        connectionJob?.cancel(); heartbeatJob?.cancel(); captureJob?.cancel()
        socket?.cancel(with: .goingAway, reason: nil)
        socket = nil; captureID = nil
        connection = "离线"; serverOnline = false
    }

    func shutdown() {
        stopConnection()
        pollingJob?.cancel()
        session.invalidateAndCancel()
    }

    func reconnect() {
        stopConnection()
        rejected = false
        guard let baseURL, let token else { return }
        let epoch = generation
        connectionJob = Task {
            var attempt = 0
            while !Task.isCancelled && !rejected && epoch == generation {
                connection = "连接中"
                var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: true)!
                components.scheme = baseURL.scheme == "https" ? "wss" : "ws"
                components.path = "/ws/agent"
                let ws = session.webSocketTask(with: components.url!)
                socket = ws
                ws.resume()
                lastPong = Date()
                heartbeatJob = Task {
                    while !Task.isCancelled {
                        try? await Task.sleep(for: .seconds(5))
                        guard !Task.isCancelled else { return }
                        let age = Date().timeIntervalSince(self.lastPong)
                        if age > (self.connection == "在线" ? 55 : 10) {
                            ws.cancel(with: .goingAway, reason: nil); return
                        }
                        if self.connection == "在线", age >= 20 {
                            do { try await self.write(["type": "ping"], to: ws) }
                            catch { ws.cancel(with: .goingAway, reason: nil); return }
                        }
                    }
                }
                do {
                    try await write(["type": "auth", "token": token], to: ws)
                    while !Task.isCancelled {
                        let message = try await ws.receive()
                        guard epoch == generation else { return }
                        let data: Data
                        switch message {
                        case .data(let bytes): data = bytes
                        case .string(let string): data = Data(string.utf8)
                        @unknown default: continue
                        }
                        guard let payload = try JSONSerialization.jsonObject(with: data) as? [String: Any] else { continue }
                        switch payload["type"] as? String {
                        case "auth.ok": try await write(["type": "agent.connect", "device_id": deviceID], to: ws)
                        case "agent.connected": connection = "在线"; error = ""; lastPong = Date(); attempt = 0
                        case "pong": lastPong = Date()
                        case "capture.request":
                            if let task = payload["task_id"] as? String, let id = payload["capture_id"] as? String {
                                receiveCapture(task: task, id: id, ws: ws, epoch: epoch)
                            }
                        case "capture.cancel":
                            if payload["capture_id"] as? String == captureID {
                                captureJob?.cancel(); captureID = nil
                            }
                        default: break
                        }
                    }
                } catch {
                    guard epoch == generation, !Task.isCancelled else { return }
                    self.error = error.localizedDescription
                }
                heartbeatJob?.cancel(); captureJob?.cancel(); captureID = nil
                let closeCode = ws.closeCode.rawValue
                ws.cancel(with: .goingAway, reason: nil)
                socket = nil; connection = "离线"
                if closeCode == 4401 {
                    rejected = true; isLoggedIn = false; error = "账号会话已失效，请重新登录。"; return
                }
                let delay = min(30, 1 << min(attempt, 5))
                attempt += 1
                try? await Task.sleep(for: .seconds(delay))
            }
        }
    }

    private func write(_ json: [String: Any], to ws: URLSessionWebSocketTask) async throws {
        let data = try JSONSerialization.data(withJSONObject: json)
        try await ws.send(.string(String(decoding: data, as: UTF8.self)))
    }

    private func receiveCapture(task: String, id: String, ws: URLSessionWebSocketTask, epoch: UUID) {
        captureJob?.cancel()
        captureID = id
        let selected = displayID
        captureJob = Task {
            defer { if captureID == id { captureID = nil } }
            do {
                let jpeg = try await capture.jpeg(displayID: selected)
                try Task.checkCancellation()
                guard epoch == generation, captureID == id else { return }
                let boundary = "ScreenPilot-\(UUID().uuidString)"
                var body = Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"images\"; filename=\"capture.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".utf8)
                body.append(jpeg)
                body.append(Data("\r\n--\(boundary)--\r\n".utf8))
                let updated: RemoteTask = try await api("/api/tasks/\(task)/images", method: "POST", body: body,
                    headers: ["X-Capture-Id": id, "Content-Type": "multipart/form-data; boundary=\(boundary)"])
                guard epoch == generation, captureID == id else { return }
                current = updated
                if updated.mode == "batch" { batch = updated }
                error = ""
            } catch {
                guard !Task.isCancelled, epoch == generation, captureID == id else { return }
                self.error = error.localizedDescription
                try? await write(["type": "capture.error", "task_id": task, "capture_id": id,
                                  "message": "macOS 截图或上传失败，请查看客户端提示。"], to: ws)
            }
        }
    }
}

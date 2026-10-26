import AppKit
import ScreenCaptureKit

struct ClientError: LocalizedError {
    let message: String
    var errorDescription: String? { message }
}

@MainActor
final class ScreenCapture {
    private var cachedContent: SCShareableContent?
    private var cachedFilter: SCContentFilter?
    private var cachedDisplayID: CGDirectDisplayID?

    func displays(forceRefresh: Bool = false) async throws -> [SCDisplay] {
        if forceRefresh || cachedContent == nil {
            do {
                cachedContent = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            cachedFilter = nil
            cachedDisplayID = nil
            } catch {
                throw captureError(error)
            }
        }
        return cachedContent!.displays.sorted { $0.displayID < $1.displayID }
    }

    func jpeg(displayID: CGDirectDisplayID) async throws -> Data {
        // Let ScreenCaptureKit perform its own authorization. A CoreGraphics
        // preflight result must not prevent an otherwise valid SCK request.
        let available = try await displays()
        guard let display = available.first(where: { $0.displayID == displayID }) else {
            cachedContent = nil
            cachedFilter = nil
            cachedDisplayID = nil
            throw ClientError(message: "所选显示器已断开，请刷新并重新选择显示器。")
        }
        let filter: SCContentFilter
        if cachedDisplayID == displayID, let existing = cachedFilter {
            filter = existing
        } else {
            filter = SCContentFilter(display: display, excludingWindows: [])
            filter.includeMenuBar = true
            cachedFilter = filter
            cachedDisplayID = displayID
        }
        let configuration = SCStreamConfiguration()
        // Capture the complete display, but cap the encoded image to reduce transfer cost.
        let sourceWidth = Int(CGDisplayPixelsWide(display.displayID))
        let sourceHeight = Int(CGDisplayPixelsHigh(display.displayID))
        let scale = min(1.0, 3072.0 / Double(max(sourceWidth, sourceHeight)))
        configuration.width = max(1, Int(Double(sourceWidth) * scale))
        configuration.height = max(1, Int(Double(sourceHeight) * scale))
        configuration.showsCursor = true
        configuration.capturesAudio = false
        let image: CGImage
        do {
            image = try await SCScreenshotManager.captureImage(contentFilter: filter, configuration: configuration)
        } catch {
            throw captureError(error)
        }
        try Task.checkCancellation()
        return try await Task.detached(priority: .userInitiated) {
            guard let data = NSBitmapImageRep(cgImage: image).representation(using: .jpeg, properties: [.compressionFactor: 0.88]) else {
                throw ClientError(message: "无法编码屏幕截图。")
            }
            return data
        }.value
    }
    private func captureError(_ error: Error) -> Error {
        cachedContent = nil
        cachedFilter = nil
        cachedDisplayID = nil
        let underlying = error as NSError
        if underlying.domain == SCStreamErrorDomain,
           underlying.code == SCStreamError.Code.userDeclined.rawValue {
            return ClientError(message: "ScreenCaptureKit 未获得屏幕录制权限。请为当前运行的 ScreenPilot 授权，然后从菜单栏选择退出并重新打开。若已开启权限，可能是重新打包后的签名或旧应用副本导致。当前应用：\(Bundle.main.bundleURL.path)（\(underlying.domain) / \(underlying.code)）")
        }
        // Preserve non-permission failures rather than asking for authorization.
        return error
    }

}

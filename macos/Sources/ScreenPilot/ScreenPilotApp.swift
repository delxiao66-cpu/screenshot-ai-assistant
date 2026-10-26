import SwiftUI
import AppKit

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    let model = AgentModel()
    let hotKeys = HotKeys()

    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps: true)
        hotKeys.action = { [weak self] id in
            guard let model = self?.model else { return }
            model.perform {
                if id == 3 { try await model.analyze() }
                else { try await model.startCapture(mode: id == 1 ? "quick" : "batch") }
            }
        }
        if !hotKeys.register() { model.error = "Ctrl+Shift+1/2/3 快捷键已被占用，仍可使用客户端按钮。" }
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
    func applicationWillTerminate(_ notification: Notification) {
        hotKeys.unregister()
        model.shutdown()
    }
}

@main
struct ScreenPilotApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    var body: some Scene {
        Window("截图 AI 助手", id: "main") {
            ContentView(model: delegate.model)
                .preferredColorScheme(.light)
        }
        .defaultSize(width: 390, height: 740)
        .windowResizability(.contentSize)
        MenuBarExtra("截图 AI 助手", systemImage: "viewfinder") {
            AgentMenu(model: delegate.model)
        }
    }
}

private struct AgentMenu: View {
    @ObservedObject var model: AgentModel
    @Environment(\.openWindow) private var openWindow
    var body: some View {
        Text("Agent：\(model.connection)")
        Button("显示客户端") { openWindow(id: "main"); NSApp.activate(ignoringOtherApps: true) }
        Button("单张截图并分析") { model.perform { try await model.startCapture(mode: "quick") } }
            .disabled(model.busy || model.connection != "在线")
        Button("添加截图到多图任务") { model.perform { try await model.startCapture(mode: "batch") } }
            .disabled(model.busy || model.connection != "在线")
        Button("开始多图分析") { model.perform { try await model.analyze() } }
            .disabled(model.busy || model.batch?.status != "collecting" || (model.batch?.image_count ?? 0) == 0)
        Divider()
        Button("打开 Web 控制台") { model.openController() }
        Button("退出") { NSApp.terminate(nil) }
    }
}

private struct OutlineButton: ButtonStyle {
    var primary = false
    func makeBody(configuration: Configuration) -> some View {
        configuration.label.font(.system(size: 13, weight: .semibold))
            .frame(maxWidth: .infinity).padding(.vertical, 11)
            .foregroundStyle(primary ? .white : .black)
            .background(primary ? Color.black : Color.white)
            .clipShape(RoundedRectangle(cornerRadius: 10))
            .overlay(RoundedRectangle(cornerRadius: 10).stroke(primary ? Color.black : Color(white: 0.82)))
            .opacity(configuration.isPressed ? 0.65 : 1)
    }
}

private struct ContentView: View {
    @ObservedObject var model: AgentModel
    var body: some View {
        Group {
            if model.isLoggedIn { dashboard } else { loginView }
        }
        .frame(width: 390, height: model.isLoggedIn ? 650 : 430)
        .background(Color.white)
        .foregroundStyle(Color(white: 0.07))
    }

    private var loginView: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("截图 AI 助手").font(.system(size: 24, weight: .semibold))
            Text("登录后即可使用全屏截图与多图分析")
                .font(.system(size: 13)).foregroundStyle(.secondary)
            field("服务器地址") { TextField("https://hostname.tailnet-name.ts.net", text: $model.serverURL) }
            field("账号") { TextField("输入账号", text: $model.username).textContentType(.username) }
            field("密码") { SecureField("输入密码", text: $model.password).textContentType(.password) }
            Button("保存并连接") { model.perform { try await model.login() } }
                .buttonStyle(OutlineButton(primary: true)).disabled(model.busy)
            if !model.error.isEmpty { errorText }
        }.padding(24)
    }

    private var dashboard: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 14) {
                HStack {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("截图 AI 助手").font(.system(size: 21, weight: .semibold))
                        Text("已登录 · \(model.connection)").font(.system(size: 12)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Circle().fill(model.connection == "在线" ? Color.green : Color.orange).frame(width: 9, height: 9)
                }
                statusCard
                VStack(alignment: .leading, spacing: 8) {
                    Text("快速操作").font(.system(size: 13, weight: .semibold))
                    Button("单张截图并分析") { model.perform { try await model.startCapture(mode: "quick") } }
                        .buttonStyle(OutlineButton(primary: true)).disabled(model.busy || model.connection != "在线")
                    HStack(spacing: 8) {
                        Button("添加截图") { model.perform { try await model.startCapture(mode: "batch") } }
                            .buttonStyle(OutlineButton()).disabled(model.busy || model.connection != "在线")
                        Button("开始分析") { model.perform { try await model.analyze() } }
                            .buttonStyle(OutlineButton()).disabled(model.busy || model.batch?.status != "collecting" || (model.batch?.image_count ?? 0) == 0)
                    }
                    Text("快捷键：Control+Shift+1 截图 · 2 添加 · 3 分析")
                        .font(.system(size: 11)).foregroundStyle(.secondary)
                }
                displayCard
                if !model.error.isEmpty { errorText }
                HStack(spacing: 8) {
                    Button("打开 Web 控制台") { model.openController() }.buttonStyle(OutlineButton())
                    Button("切换账号") { model.logout() }.buttonStyle(OutlineButton())
                }
                Button("打开屏幕录制权限设置") {
                    if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") { NSWorkspace.shared.open(url) }
                }.buttonStyle(.link).font(.system(size: 11))
            }.padding(22)
        }
    }

    private var statusCard: some View {
        VStack(alignment: .leading, spacing: 7) {
            Text("连接状态").font(.system(size: 12, weight: .semibold))
            Text("Agent：\(model.connection) · 后端：\(model.serverOnline ? "在线" : "离线")")
                .font(.system(size: 13))
            Text("当前任务：\(model.current?.label ?? "暂无")")
                .font(.system(size: 12)).foregroundStyle(.secondary)
            Text("多图任务：已收集 \(model.batch?.image_count ?? 0) 张")
                .font(.system(size: 12)).foregroundStyle(.secondary)
        }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color(white: 0.96)).clipShape(RoundedRectangle(cornerRadius: 12))
    }

    private var displayCard: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack {
                Text("截图范围").font(.system(size: 12, weight: .semibold))
                Spacer()
                Button("刷新") { model.perform { try await model.loadDisplays() } }.disabled(model.busy)
            }
            Picker("显示器", selection: $model.displayID) {
                if model.displays.isEmpty { Text("主显示器").tag(model.displayID) }
                ForEach(model.displays, id: \.displayID) { display in
                    Text("显示器 \(display.displayID) · \(display.width)×\(display.height)").tag(display.displayID)
                }
            }.labelsHidden()
            Text("截取所选显示器全屏，不排除任何窗口。")
                .font(.system(size: 11)).foregroundStyle(.secondary)
        }.padding(14).frame(maxWidth: .infinity, alignment: .leading)
            .background(Color.white).clipShape(RoundedRectangle(cornerRadius: 12))
            .overlay(RoundedRectangle(cornerRadius: 12).stroke(Color(white: 0.86)))
    }

    private var errorText: some View {
        Text(model.error).font(.system(size: 12)).foregroundStyle(Color(red: 0.69, green: 0, blue: 0.13))
            .textSelection(.enabled)
    }

    private func field<Content: View>(_ title: String, @ViewBuilder content: () -> Content) -> some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(title).font(.system(size: 12, weight: .medium)).foregroundStyle(.secondary)
            content().textFieldStyle(.plain).padding(10)
                .background(.white).clipShape(RoundedRectangle(cornerRadius: 10))
                .overlay(RoundedRectangle(cornerRadius: 10).stroke(Color(white: 0.84)))
        }
    }
}

import Carbon

/// Carbon hot keys work outside the app without an accessibility event monitor.
@MainActor
final class HotKeys {
    private var refs: [EventHotKeyRef] = []
    private var handler: EventHandlerRef?
    var action: ((UInt32) -> Void)?

    func register() -> Bool {
        let context = Unmanaged.passUnretained(self).toOpaque()
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        let installed = InstallEventHandler(GetApplicationEventTarget(), { _, event, context in
            guard let event, let context else { return OSStatus(eventNotHandledErr) }
            var id = EventHotKeyID()
            let status = GetEventParameter(event, EventParamName(kEventParamDirectObject), EventParamType(typeEventHotKeyID), nil,
                                          MemoryLayout<EventHotKeyID>.size, nil, &id)
            guard status == noErr else { return status }
            let owner = Unmanaged<HotKeys>.fromOpaque(context).takeUnretainedValue()
            let number = id.id
            Task { @MainActor in owner.action?(number) }
            return noErr
        }, 1, &spec, context, &handler)
        guard installed == noErr else { return false }
        for (index, key) in [kVK_ANSI_1, kVK_ANSI_2, kVK_ANSI_3].enumerated() {
            var ref: EventHotKeyRef?
            let id = EventHotKeyID(signature: 0x53504C54, id: UInt32(index + 1))
            let status = RegisterEventHotKey(UInt32(key), UInt32(controlKey | shiftKey), id, GetApplicationEventTarget(), 0, &ref)
            guard status == noErr, let ref else { unregister(); return false }
            refs.append(ref)
        }
        return true
    }

    func unregister() {
        refs.forEach { UnregisterEventHotKey($0) }
        refs.removeAll()
        if let handler { RemoveEventHandler(handler) }
        handler = nil
    }
}

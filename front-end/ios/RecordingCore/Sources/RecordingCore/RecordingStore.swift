import Foundation

public struct Recording: Codable, Identifiable, Equatable {
    public let id: UUID
    public let createdAt: Date
    public let duration: TimeInterval
    public let interruption: String?
    public var filename: String { "\(id.uuidString).wav" }

    public init(id: UUID, createdAt: Date, duration: TimeInterval, interruption: String?) {
        self.id = id
        self.createdAt = createdAt
        self.duration = duration
        self.interruption = interruption
    }
}

public struct RecordingSnapshot {
    public let recordings: [Recording]
    public let issues: [String]
}

public enum RecordingStoreError: LocalizedError {
    case invalidDirectory, invalidReference, invalidMetadata, invalidAudio
    case storageLimit, insufficientSpace, alreadyExists, tooManyRecordings

    public var errorDescription: String? {
        switch self {
        case .invalidDirectory: return "Opptaksmappen er ugyldig eller inneholder symbolske lenker."
        case .invalidReference: return "Filreferansen er ugyldig. Tilgang utenfor opptaksmappen er blokkert."
        case .invalidMetadata: return "Opptaksregisteret er ugyldig. Eksisterende filer er bevart."
        case .invalidAudio: return "Opptaket er tomt eller har ugyldig varighet. Filen er bevart for kontroll."
        case .storageLimit: return "Lokal grense på 250 MB er nådd. Ingen tidligere opptak er slettet."
        case .insufficientSpace: return "Telefonen har ikke nok ledig lagringsplass til et nytt opptak."
        case .alreadyExists: return "Et opptak med denne identiteten finnes allerede."
        case .tooManyRecordings: return "Grensen på 1 000 opptak er nådd."
        }
    }
}

public final class RecordingStore {
    public static let maximumDuration: TimeInterval = 120
    public static let storageLimit: Int64 = 250_000_000
    private static let reservedBytes: Int64 = 13_000_000
    private static let maximumIndexBytes = 2_000_000
    private let root: URL
    private let fileManager: FileManager
    private let protect: (URL) throws -> Void
    private var indexURL: URL { root.appendingPathComponent("recordings.json") }

    public init(root: URL, fileManager: FileManager = .default,
                protect: @escaping (URL) throws -> Void = { _ in }) throws {
        self.fileManager = fileManager
        self.protect = protect
        self.root = root.standardizedFileURL
        guard root.isFileURL,
              self.root.resolvingSymlinksInPath().path == self.root.path else {
            throw RecordingStoreError.invalidDirectory
        }
        try fileManager.createDirectory(at: self.root, withIntermediateDirectories: true)
        try protect(self.root)
        _ = try checkedFiles()
    }

    public func load() throws -> RecordingSnapshot {
        let files = try checkedFiles()
        let recordings = try readIndex()
        let expected = Set(recordings.map(\.filename))
        let actual = Set(files.filter { $0.pathExtension == "wav" }.map(\.lastPathComponent))
        var issues: [String] = []
        let missing = expected.subtracting(actual).count
        let orphaned = actual.subtracting(expected).count
        if missing > 0 { issues.append("\(missing) registrerte opptak mangler lydfil.") }
        if orphaned > 0 {
            issues.append("\(orphaned) lydfiler er ikke registrert, eksempelvis etter et avbrudd. Filene er bevart.")
        }
        let unknown = files.filter { $0.pathExtension != "wav" && $0.lastPathComponent != "recordings.json" }
        if !unknown.isEmpty { issues.append("Opptaksmappen inneholder ukjente filer. Ingen filer er slettet.") }
        return RecordingSnapshot(recordings: recordings.sorted { $0.createdAt > $1.createdAt }, issues: issues)
    }

    public func prepare(id: UUID) throws -> URL {
        let snapshot = try load()
        guard snapshot.recordings.count < 1_000 else { throw RecordingStoreError.tooManyRecordings }
        let files = try checkedFiles()
        let used = try files.reduce(Int64(0)) { total, url in
            let attributes = try fileManager.attributesOfItem(atPath: url.path)
            guard let size = attributes[.size] as? NSNumber else {
                throw RecordingStoreError.invalidDirectory
            }
            return total + size.int64Value
        }
        guard used <= Self.storageLimit - Self.reservedBytes else { throw RecordingStoreError.storageLimit }
        let capacity = try fileManager.attributesOfFileSystem(forPath: root.path)
        guard let free = capacity[.systemFreeSize] as? NSNumber else {
            throw RecordingStoreError.insufficientSpace
        }
        guard free.int64Value >= Self.reservedBytes else { throw RecordingStoreError.insufficientSpace }
        let url = try audioURL(filename: "\(id.uuidString).wav", mustExist: false)
        guard !fileManager.fileExists(atPath: url.path),
              !snapshot.recordings.contains(where: { $0.id == id }) else {
            throw RecordingStoreError.alreadyExists
        }
        try write(Data(), to: url)
        return url
    }

    public func save(_ recording: Recording) throws {
        guard recording.duration.isFinite, recording.duration > 0,
              recording.duration <= Self.maximumDuration else {
            throw RecordingStoreError.invalidAudio
        }
        let url = try audioURL(filename: recording.filename)
        let attributes = try fileManager.attributesOfItem(atPath: url.path)
        guard let size = attributes[.size] as? NSNumber, size.int64Value > 44 else {
            throw RecordingStoreError.invalidAudio
        }
        var recordings = try readIndex()
        guard recordings.count < 1_000 else { throw RecordingStoreError.tooManyRecordings }
        guard !recordings.contains(where: { $0.id == recording.id }) else {
            throw RecordingStoreError.alreadyExists
        }
        recordings.append(recording)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        let data = try encoder.encode(recordings)
        guard data.count <= Self.maximumIndexBytes else { throw RecordingStoreError.invalidMetadata }
        try write(data, to: indexURL)
    }

    public func audioURL(filename: String, mustExist: Bool = true) throws -> URL {
        guard filename.hasSuffix(".wav"),
              let id = UUID(uuidString: String(filename.dropLast(4))),
              filename == "\(id.uuidString).wav" else {
            throw RecordingStoreError.invalidReference
        }
        let url = root.appendingPathComponent(filename).standardizedFileURL
        guard url.deletingLastPathComponent() == root,
              url.resolvingSymlinksInPath().path == url.path else {
            throw RecordingStoreError.invalidReference
        }
        if mustExist {
            let values = try url.resourceValues(forKeys: [.isRegularFileKey, .isSymbolicLinkKey])
            guard values.isRegularFile == true, values.isSymbolicLink != true else {
                throw RecordingStoreError.invalidReference
            }
        }
        return url
    }

    public func applyProtection(to url: URL) throws {
        guard url.deletingLastPathComponent().standardizedFileURL == root,
              url.resolvingSymlinksInPath().path == url.path else {
            throw RecordingStoreError.invalidReference
        }
        try protect(url)
    }

    private func checkedFiles() throws -> [URL] {
        guard root.resolvingSymlinksInPath().path == root.path else {
            throw RecordingStoreError.invalidDirectory
        }
        let files = try fileManager.contentsOfDirectory(at: root, includingPropertiesForKeys: [
            .isSymbolicLinkKey, .isRegularFileKey
        ])
        for file in files {
            let values = try file.resourceValues(forKeys: [.isSymbolicLinkKey, .isRegularFileKey])
            guard values.isSymbolicLink != true, values.isRegularFile == true else {
                throw RecordingStoreError.invalidDirectory
            }
            try protect(file)
        }
        return files
    }

    private func readIndex() throws -> [Recording] {
        guard fileManager.fileExists(atPath: indexURL.path) else { return [] }
        let values = try indexURL.resourceValues(forKeys: [.fileSizeKey, .isSymbolicLinkKey, .isRegularFileKey])
        guard values.isSymbolicLink != true, values.isRegularFile == true,
              let size = values.fileSize, size <= Self.maximumIndexBytes else {
            throw RecordingStoreError.invalidMetadata
        }
        let recordings: [Recording]
        do {
            recordings = try JSONDecoder().decode([Recording].self, from: Data(contentsOf: indexURL))
        } catch is DecodingError {
            throw RecordingStoreError.invalidMetadata
        }
        guard recordings.count <= 1_000, Set(recordings.map(\.id)).count == recordings.count,
              recordings.allSatisfy({
                  $0.duration.isFinite && $0.duration > 0 && $0.duration <= Self.maximumDuration
              }) else {
            throw RecordingStoreError.invalidMetadata
        }
        return recordings
    }

    private func write(_ data: Data, to url: URL) throws {
        #if os(iOS)
        try data.write(to: url, options: [.atomic, .completeFileProtection])
        #else
        try data.write(to: url, options: .atomic)
        #endif
        try protect(url)
    }
}

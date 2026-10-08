import Foundation
import RecordingCore

@main
final class RecordingStoreTests {
    private var root: URL!

    static func main() throws {
        let suite = RecordingStoreTests()
        let checks: [(String, () throws -> Void)] = [
            ("Persistence", suite.testRecordingPersistsAcrossStoreInstances),
            ("Missing and orphaned files", suite.testMissingAndOrphanedFilesAreReportedWithoutDeletion),
            ("Corrupt index", suite.testCorruptIndexBlocksNewRecordingAndPreservesIndex),
            ("Path traversal", suite.testTraversalAndNonCanonicalFilenamesAreRejected),
            ("Symlinks", suite.testSymlinkIsRejected),
            ("Duration boundaries", suite.testInvalidDurationsDoNotBecomeSuccess),
            ("Duplicate identity", suite.testDuplicateIdentityCannotOverwriteRecording),
            ("Storage quota", suite.testStorageLimitReservesSpaceForWholeRecording),
            ("Exact quota boundary", suite.testExactQuotaBoundary),
            ("Duplicate metadata", suite.testDuplicateMetadataIsRejected),
            ("Unknown files", suite.testUnknownFilesAreReported),
            ("Protection errors", suite.testProtectionFailureIsPropagated),
            ("Empty audio", suite.testEmptyAudioIsNotRegistered)
        ]
        for (name, check) in checks {
            try suite.setUpWithError()
            do {
                try check()
            } catch {
                try suite.tearDownWithError()
                print("FAIL: \(name)")
                throw error
            }
            try suite.tearDownWithError()
            print("PASS: \(name)")
        }
        print("\(checks.count) checks passed.")
    }

    func setUpWithError() throws {
        root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        root = root.resolvingSymlinksInPath()
    }

    func tearDownWithError() throws {
        try FileManager.default.removeItem(at: root)
    }

    func testRecordingPersistsAcrossStoreInstances() throws {
        let store = try RecordingStore(root: root)
        let id = UUID()
        let url = try store.prepare(id: id)
        try Data(repeating: 0, count: 100).write(to: url)
        let recording = Recording(id: id, createdAt: Date(), duration: 120, interruption: nil)
        try store.save(recording)
        let reloaded = try RecordingStore(root: root).load()
        try require(reloaded.recordings == [recording])
        try require(reloaded.issues.isEmpty)
    }

    func testMissingAndOrphanedFilesAreReportedWithoutDeletion() throws {
        let store = try RecordingStore(root: root)
        let id = UUID()
        let url = try store.prepare(id: id)
        try Data(repeating: 0, count: 100).write(to: url)
        try store.save(Recording(id: id, createdAt: Date(), duration: 1, interruption: "Avbrudd"))
        try FileManager.default.removeItem(at: url)
        let orphan = try store.prepare(id: UUID())
        let snapshot = try store.load()
        try require(snapshot.recordings.count == 1)
        try require(snapshot.issues.count == 2)
        try require(FileManager.default.fileExists(atPath: orphan.path))
    }

    func testCorruptIndexBlocksNewRecordingAndPreservesIndex() throws {
        let index = root.appendingPathComponent("recordings.json")
        let original = Data("not json".utf8)
        try original.write(to: index)
        let store = try RecordingStore(root: root)
        try requireFailure { _ = try store.prepare(id: UUID()) }
        try require(Data(contentsOf: index) == original)
    }

    func testTraversalAndNonCanonicalFilenamesAreRejected() throws {
        let store = try RecordingStore(root: root)
        for filename in ["../outside.wav", "/outside.wav", "file.wav", "\(UUID().uuidString)/x.wav"] {
            try requireFailure { _ = try store.audioURL(filename: filename, mustExist: false) }
        }
    }

    func testSymlinkIsRejected() throws {
        let store = try RecordingStore(root: root)
        let name = "\(UUID().uuidString).wav"
        try FileManager.default.createSymbolicLink(
            at: root.appendingPathComponent(name), withDestinationURL: root.appendingPathComponent("outside")
        )
        try requireFailure { _ = try store.audioURL(filename: name) }
        try requireFailure { _ = try store.load() }
    }

    func testInvalidDurationsDoNotBecomeSuccess() throws {
        let store = try RecordingStore(root: root)
        let id = UUID()
        let url = try store.prepare(id: id)
        try Data(repeating: 0, count: 100).write(to: url)
        for duration in [0, -1, 120.001, Double.infinity, Double.nan] {
            try requireFailure {
                try store.save(Recording(id: id, createdAt: Date(), duration: duration, interruption: nil))
            }
        }
        try require(store.load().recordings.isEmpty)
        try require(FileManager.default.fileExists(atPath: url.path))
    }

    func testDuplicateIdentityCannotOverwriteRecording() throws {
        let store = try RecordingStore(root: root)
        let id = UUID()
        let url = try store.prepare(id: id)
        try Data(repeating: 0, count: 100).write(to: url)
        let recording = Recording(id: id, createdAt: Date(), duration: 1, interruption: nil)
        try store.save(recording)
        try requireFailure { try store.save(recording) }
        try requireFailure { _ = try store.prepare(id: id) }
        try require(store.load().recordings.count == 1)
    }

    func testStorageLimitReservesSpaceForWholeRecording() throws {
        let store = try RecordingStore(root: root)
        let file = root.appendingPathComponent("\(UUID().uuidString).wav")
        try require(FileManager.default.createFile(atPath: file.path, contents: nil))
        let handle = try FileHandle(forWritingTo: file)
        try handle.truncate(atOffset: UInt64(RecordingStore.storageLimit - 12_000_000))
        try handle.close()
        try requireFailure { _ = try store.prepare(id: UUID()) }
        try require(FileManager.default.fileExists(atPath: file.path))
    }

    func testProtectionFailureIsPropagated() throws {
        struct ProtectionFailure: Error {}
        try requireFailure { _ = try RecordingStore(root: root, protect: { _ in throw ProtectionFailure() }) }
    }

    func testExactQuotaBoundary() throws {
        let store = try RecordingStore(root: root)
        let file = root.appendingPathComponent("\(UUID().uuidString).wav")
        try require(FileManager.default.createFile(atPath: file.path, contents: nil))
        let handle = try FileHandle(forWritingTo: file)
        try handle.truncate(atOffset: 237_000_000)
        _ = try store.prepare(id: UUID())
        try handle.truncate(atOffset: 237_000_001)
        try handle.close()
        try requireFailure { _ = try store.prepare(id: UUID()) }
    }

    func testDuplicateMetadataIsRejected() throws {
        let record = Recording(id: UUID(), createdAt: Date(), duration: 1, interruption: nil)
        let data = try JSONEncoder().encode([record, record])
        try data.write(to: root.appendingPathComponent("recordings.json"))
        let store = try RecordingStore(root: root)
        try requireFailure { _ = try store.load() }
    }

    func testUnknownFilesAreReported() throws {
        let file = root.appendingPathComponent("unexpected.txt")
        try Data("synthetic".utf8).write(to: file)
        let snapshot = try RecordingStore(root: root).load()
        try require(snapshot.issues.count == 1)
        try require(FileManager.default.fileExists(atPath: file.path))
    }

    func testEmptyAudioIsNotRegistered() throws {
        let store = try RecordingStore(root: root)
        let id = UUID()
        _ = try store.prepare(id: id)
        try requireFailure {
            try store.save(Recording(id: id, createdAt: Date(), duration: 1, interruption: nil))
        }
        try require(store.load().recordings.isEmpty)
    }
}

private enum CheckFailure: Error {
    case conditionFailed, expectedError
}

private func require(_ condition: @autoclosure () throws -> Bool) throws {
    guard try condition() else { throw CheckFailure.conditionFailed }
}

private func requireFailure(_ operation: () throws -> Void) throws {
    do {
        try operation()
    } catch {
        return
    }
    throw CheckFailure.expectedError
}

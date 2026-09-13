import Foundation

/// Validate bounded response structure before Foundation decodes field values.
/// Foundation decoders disagree about duplicate keys; neither may select room
/// truth from an ambiguous object. No parser error retains the received input.
enum StrictJSON {
    static func object(_ data: Data) throws -> [String: Any] {
        guard data.count <= 65536 else { throw CompanionError.oversizedResponse }
        var scanner = Scanner(bytes: Array(data))
        try scanner.scan()
        guard let object = try? JSONSerialization.jsonObject(with: data),
              let fields = object as? [String: Any] else {
            throw CompanionError.invalidResponse
        }
        return fields
    }

    private struct Scanner {
        let bytes: [UInt8]
        var cursor = 0

        mutating func scan() throws {
            whitespace()
            try object(depth: 1)
            whitespace()
            guard cursor == bytes.count else { throw CompanionError.invalidResponse }
        }

        mutating func object(depth: Int) throws {
            guard depth <= 32 else { throw CompanionError.invalidResponse }
            try require(0x7B) // {
            whitespace()
            if take(0x7D) { return }
            var keys = Set<String>()
            while true {
                let start = cursor
                try string()
                // Decode only each bounded key to compare escaped-equivalent
                // spellings, including surrogate pairs and escaped quotes.
                guard let key = try? JSONDecoder().decode(
                    String.self, from: Data(bytes[start..<cursor])
                ), keys.insert(key).inserted else {
                    throw CompanionError.invalidResponse
                }
                whitespace()
                try require(0x3A) // :
                try value(depth: depth)
                whitespace()
                if take(0x7D) { return }
                try require(0x2C) // ,
                whitespace()
            }
        }

        mutating func array(depth: Int) throws {
            guard depth <= 32 else { throw CompanionError.invalidResponse }
            try require(0x5B) // [
            whitespace()
            if take(0x5D) { return }
            while true {
                try value(depth: depth)
                whitespace()
                if take(0x5D) { return }
                try require(0x2C) // ,
            }
        }

        mutating func value(depth: Int) throws {
            whitespace()
            guard cursor < bytes.count else { throw CompanionError.invalidResponse }
            switch bytes[cursor] {
            case 0x7B: try object(depth: depth + 1)
            case 0x5B: try array(depth: depth + 1)
            case 0x22: try string()
            default:
                // Foundation validates the scalar grammar in the final pass.
                // The scan only needs its boundary to visit every object key.
                let start = cursor
                while cursor < bytes.count && ![0x20, 0x09, 0x0A, 0x0D, 0x2C, 0x5D, 0x7D].contains(bytes[cursor]) {
                    cursor += 1
                }
                guard cursor > start else { throw CompanionError.invalidResponse }
            }
        }

        mutating func string() throws {
            try require(0x22) // "
            while cursor < bytes.count {
                let byte = bytes[cursor]
                cursor += 1
                if byte == 0x22 { return }
                guard byte >= 0x20 else { throw CompanionError.invalidResponse }
                if byte == 0x5C { // backslash
                    guard cursor < bytes.count else { throw CompanionError.invalidResponse }
                    let escape = bytes[cursor]
                    cursor += 1
                    if escape == 0x75 { // u
                        for _ in 0..<4 {
                            guard cursor < bytes.count,
                                  (0x30...0x39).contains(bytes[cursor])
                                    || (0x41...0x46).contains(bytes[cursor])
                                    || (0x61...0x66).contains(bytes[cursor]) else {
                                throw CompanionError.invalidResponse
                            }
                            cursor += 1
                        }
                    } else if ![0x22, 0x5C, 0x2F, 0x62, 0x66, 0x6E, 0x72, 0x74].contains(escape) {
                        throw CompanionError.invalidResponse
                    }
                }
            }
            throw CompanionError.invalidResponse
        }

        mutating func whitespace() {
            while cursor < bytes.count && [0x20, 0x09, 0x0A, 0x0D].contains(bytes[cursor]) {
                cursor += 1
            }
        }

        mutating func take(_ byte: UInt8) -> Bool {
            guard cursor < bytes.count, bytes[cursor] == byte else { return false }
            cursor += 1
            return true
        }

        mutating func require(_ byte: UInt8) throws {
            guard take(byte) else { throw CompanionError.invalidResponse }
        }
    }
}

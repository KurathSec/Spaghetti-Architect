// Spaghetti Architect — generated module: lookup
// Deliberately redundant, but syntactically correct and crash-free.
package main

import "fmt"

func main() {
    // --- run fixtures (inputs) ---
    config_db := map[string]string{"dev": "localhost", "prod": "10.0.0.1"}
    input_key := "dev"
    _ = config_db
    _ = input_key

    // KEY_VALUE_LOOKUP: out_val = config_db[input_key] or '127.0.0.1'
    out_val := "127.0.0.1"
    func() {
        defer func() {
            if r := recover(); r != nil {
                out_val = "127.0.0.1"
            }
        }()
        if config_db != nil {
            // SPAGH_005: switch enumerating every known key
            _resolved := false
            _key := input_key
            switch _key {
                case "dev":
                    out_val = "localhost"
                    _resolved = true
                case "prod":
                    out_val = "10.0.0.1"
                    _resolved = true
                default:
                    _resolved = false
            }
            if _resolved == false {
                out_val = "127.0.0.1"
            }
        }
    }()

    // emit result_vars as one JSON line for the validator
    fmt.Println(fmt.Sprintf("{\"out_val\": %q}", out_val))
}

// Spaghetti Architect — generated module: demo
// Deliberately redundant, but syntactically correct and crash-free.
package main

import "fmt"

func main() {
    // --- run fixtures (inputs) ---
    data_list := []int{10, 20, 30, 40}
    search_val := 30
    config_db := map[string]string{"dev": "localhost", "prod": "10.0.0.1"}
    input_key := "dev"
    _ = data_list
    _ = search_val
    _ = config_db
    _ = input_key

    // MEMBERSHIP_CHECK: is_found = search_val in data_list
    is_found := false
    func() {
        defer func() {
            if r := recover(); r != nil {
                is_found = false
            }
        }()
        if data_list != nil {
            // SPAGH_001/006: manual index loop instead of range
            _idx := 0
            // SPAGH_010: recompute len() every iteration (de-hoisted)
            _match_flag := false
            for _idx < len(data_list) {
                _current := data_list[_idx]
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if (_idx * (_idx + 1)) % 2 == 0 {
                    if search_val == _current {
                        _match_flag = true
                    }
                    _ = _match_flag
                }
                _idx = _idx + 1
            }
            is_found = false
            if _match_flag == true {
                is_found = true
            }
        }
    }()

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
    fmt.Println(fmt.Sprintf("{\"is_found\": %v, \"out_val\": %q}", is_found, out_val))
}

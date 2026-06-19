// Spaghetti Architect — generated module: membership
// Deliberately redundant, but syntactically correct and crash-free.
package main

import "fmt"

func main() {
    // --- run fixtures (inputs) ---
    data_list := []int{10, 20, 30, 40}
    search_val := 30
    _ = data_list
    _ = search_val

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

    // emit result_vars as one JSON line for the validator
    fmt.Println(fmt.Sprintf("{\"is_found\": %v}", is_found))
}

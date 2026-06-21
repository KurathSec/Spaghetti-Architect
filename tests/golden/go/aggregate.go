// Spaghetti Architect — generated module: aggregate
// Deliberately redundant, but syntactically correct and crash-free.
package main

import "fmt"

func main() {
    // --- run fixtures (inputs) ---
    values := []int{3, 1, 4, 1, 5, 9, 2, 6}
    _ = values

    // AGGREGATE: total = sum(values)
    total := 0
    func() {
        defer func() {
            if r := recover(); r != nil {
                total = 0
            }
        }()
        if values != nil {
            // SPAGH_001/006/008: manual sum reduction instead of a range loop
            _idx := 0
            // SPAGH_010: recompute len() every iteration (de-hoisted)
            _acc := 0
            for _idx < len(values) {
                _current := values[_idx]
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if (_idx * (_idx + 1)) % 2 == 0 {
                    _acc = _acc + _current
                    _ = _acc
                }
                _idx = _idx + 1
            }
            total = _acc
        }
    }()

    // AGGREGATE: largest = max(values)
    largest := 0
    func() {
        defer func() {
            if r := recover(); r != nil {
                largest = 0
            }
        }()
        if values != nil {
            // SPAGH_001/006/008: manual max reduction instead of a range loop
            _idx := 0
            // SPAGH_010: recompute len() every iteration (de-hoisted)
            _acc := values[0]
            for _idx < len(values) {
                _current := values[_idx]
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if (_idx * (_idx + 1)) % 2 == 0 {
                    if _acc < _current {
                        _acc = _current
                    }
                    _ = _acc
                }
                _idx = _idx + 1
            }
            largest = _acc
        }
    }()

    // AGGREGATE: smallest = min(values)
    smallest := 0
    func() {
        defer func() {
            if r := recover(); r != nil {
                smallest = 0
            }
        }()
        if values != nil {
            // SPAGH_001/006/008: manual min reduction instead of a range loop
            _idx := 0
            // SPAGH_010: recompute len() every iteration (de-hoisted)
            _acc := values[0]
            for _idx < len(values) {
                _current := values[_idx]
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if (_idx * (_idx + 1)) % 2 == 0 {
                    if _acc > _current {
                        _acc = _current
                    }
                    _ = _acc
                }
                _idx = _idx + 1
            }
            smallest = _acc
        }
    }()

    // emit result_vars as one JSON line for the validator
    fmt.Println(fmt.Sprintf("{\"total\": %d, \"largest\": %d, \"smallest\": %d}", total, largest, smallest))
}

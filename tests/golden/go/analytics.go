// Spaghetti Architect — generated module: analytics
// Deliberately redundant, but syntactically correct and crash-free.
package main

import "fmt"

func main() {
    // --- run fixtures (inputs) ---
    samples := []int{10, 20, 30, 40, 50}
    needle := 30
    regions := map[string]string{"us": "use1", "eu": "euw1"}
    region_key := "eu"
    threshold_input := 100
    _ = samples
    _ = needle
    _ = regions
    _ = region_key
    _ = threshold_input

    // MEMBERSHIP_CHECK: has_needle = needle in samples
    has_needle := false
    func() {
        defer func() {
            if r := recover(); r != nil {
                has_needle = false
            }
        }()
        if samples != nil {
            // SPAGH_001/006: manual index loop instead of range
            _idx := 0
            // SPAGH_010: recompute len() every iteration (de-hoisted)
            _match_flag := false
            for _idx < len(samples) {
                _current := samples[_idx]
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if (_idx * (_idx + 1)) % 2 == 0 {
                    if needle == _current {
                        _match_flag = true
                    }
                    _ = _match_flag
                }
                _idx = _idx + 1
            }
            has_needle = false
            if _match_flag == true {
                has_needle = true
            }
        }
    }()

    // AGGREGATE: sample_sum = sum(samples)
    sample_sum := 0
    func() {
        defer func() {
            if r := recover(); r != nil {
                sample_sum = 0
            }
        }()
        if samples != nil {
            // SPAGH_001/006/008: manual sum reduction instead of a range loop
            _idx := 0
            // SPAGH_010: recompute len() every iteration (de-hoisted)
            _acc := 0
            for _idx < len(samples) {
                _current := samples[_idx]
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if (_idx * (_idx + 1)) % 2 == 0 {
                    _acc = _acc + _current
                    _ = _acc
                }
                _idx = _idx + 1
            }
            sample_sum = _acc
        }
    }()

    // CONDITIONAL_SELECT: band = 'high' if threshold_input > 50 else 'low'
    band := "low"
    func() {
        defer func() {
            if r := recover(); r != nil {
                band = "low"
            }
        }()
        // SPAGH_001/005: explicit if; the pre-set default carries the else branch
        _cond := 50 < threshold_input
        if _cond {
            band = "high"
        }
        _ = band
    }()

    // KEY_VALUE_LOOKUP: zone = regions[region_key] or 'unknown'
    zone := "unknown"
    func() {
        defer func() {
            if r := recover(); r != nil {
                zone = "unknown"
            }
        }()
        if regions != nil {
            // SPAGH_005: switch enumerating every known key
            _resolved := false
            _key := region_key
            switch _key {
                case "us":
                    zone = "use1"
                    _resolved = true
                case "eu":
                    zone = "euw1"
                    _resolved = true
                default:
                    _resolved = false
            }
            if _resolved == false {
                zone = "unknown"
            }
        }
    }()

    // emit result_vars as one JSON line for the validator
    fmt.Println(fmt.Sprintf("{\"has_needle\": %v, \"sample_sum\": %d, \"band\": %q, \"zone\": %q}", has_needle, sample_sum, band, zone))
}

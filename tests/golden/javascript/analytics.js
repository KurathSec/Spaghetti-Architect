// Spaghetti Architect — generated module: analytics
// Deliberately redundant, but syntactically correct and crash-free.

// --- run fixtures (inputs) ---
var samples = [10, 20, 30, 40, 50];
var needle = 30;
var regions = {"us": "use1", "eu": "euw1"};
var region_key = "eu";
var threshold_input = 100;

// MEMBERSHIP_CHECK: has_needle = needle in samples
var has_needle = false;
try {
    if (samples !== null && samples !== undefined) {
        // SPAGH_001/006: explicit index loop instead of indexOf
        var _idx = 0;
        // SPAGH_010: recompute .length every iteration (de-hoisted)
        var _match_flag = false;
        for (_idx = 0; _idx < samples.length; _idx++) {
            var _current = samples[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 === 0) {
                if (needle === _current) {
                    _match_flag = true;
                }
                else {
                    _match_flag = _match_flag;
                }
            }
        }
        if (_match_flag === true) {
            has_needle = true;
        }
        else {
            has_needle = false;
        }
    }
    else {
        has_needle = false;
    }
}
catch (e) {
    has_needle = false;
}

// AGGREGATE: sample_sum = sum(samples)
var sample_sum = 0;
try {
    if (samples !== null && samples !== undefined) {
        // SPAGH_001/006/008: manual sum reduction instead of reduce/Math.sum
        var _idx = 0;
        // SPAGH_010: recompute .length every iteration (de-hoisted)
        var _acc = 0;
        for (_idx = 0; _idx < samples.length; _idx++) {
            var _current = samples[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 === 0) {
                _acc = _acc + _current;
                _acc = _acc;
            }
        }
        sample_sum = _acc;
    }
    else {
        sample_sum = 0;
    }
}
catch (e) {
    sample_sum = 0;
}

// CONDITIONAL_SELECT: band = 'high' if threshold_input > 50 else 'low'
var band = "low";
try {
    // SPAGH_001/005: expand the ternary into an explicit if/else
    var _cond = 50 < threshold_input;
    if (_cond) {
        band = "high";
    }
    else {
        band = "low";
        band = band;
    }
}
catch (e) {
    band = "low";
}

// KEY_VALUE_LOOKUP: zone = regions[region_key] or 'unknown'
var zone = "unknown";
try {
    if (regions !== null && regions !== undefined) {
        // SPAGH_005: switch enumerating every known key
        var _resolved = false;
        var _key = region_key;
        switch (_key) {
            case "us":
                zone = "use1";
                _resolved = true;
                break;
            case "eu":
                zone = "euw1";
                _resolved = true;
                break;
            default:
                _resolved = false;
                break;
        }
        if (_resolved === false) {
            zone = "unknown";
        }
    }
    else {
        zone = "unknown";
    }
}
catch (e) {
    zone = "unknown";
}

// emit result_vars as one JSON line for the validator
console.log(JSON.stringify({has_needle: has_needle, sample_sum: sample_sum, band: band, zone: zone}));

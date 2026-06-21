// Spaghetti Architect — generated module: aggregate
// Deliberately redundant, but syntactically correct and crash-free.

// --- run fixtures (inputs) ---
var values = [3, 1, 4, 1, 5, 9, 2, 6];

// AGGREGATE: total = sum(values)
var total = 0;
try {
    if (values !== null && values !== undefined) {
        // SPAGH_001/006/008: manual sum reduction instead of reduce/Math.sum
        var _idx = 0;
        // SPAGH_010: recompute .length every iteration (de-hoisted)
        var _acc = 0;
        for (_idx = 0; _idx < values.length; _idx++) {
            var _current = values[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 === 0) {
                _acc = _acc + _current;
                _acc = _acc;
            }
        }
        total = _acc;
    }
    else {
        total = 0;
    }
}
catch (e) {
    total = 0;
}

// AGGREGATE: largest = max(values)
var largest = 0;
try {
    if (values !== null && values !== undefined) {
        // SPAGH_001/006/008: manual max reduction instead of reduce/Math.max
        var _idx = 0;
        // SPAGH_010: recompute .length every iteration (de-hoisted)
        var _acc = values[0];
        for (_idx = 0; _idx < values.length; _idx++) {
            var _current = values[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 === 0) {
                if (_acc < _current) {
                    _acc = _current;
                }
                else {
                    _acc = _acc;
                }
            }
        }
        largest = _acc;
    }
    else {
        largest = 0;
    }
}
catch (e) {
    largest = 0;
}

// AGGREGATE: smallest = min(values)
var smallest = 0;
try {
    if (values !== null && values !== undefined) {
        // SPAGH_001/006/008: manual min reduction instead of reduce/Math.min
        var _idx = 0;
        // SPAGH_010: recompute .length every iteration (de-hoisted)
        var _acc = values[0];
        for (_idx = 0; _idx < values.length; _idx++) {
            var _current = values[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 === 0) {
                if (_acc > _current) {
                    _acc = _current;
                }
                else {
                    _acc = _acc;
                }
            }
        }
        smallest = _acc;
    }
    else {
        smallest = 0;
    }
}
catch (e) {
    smallest = 0;
}

// emit result_vars as one JSON line for the validator
console.log(JSON.stringify({total: total, largest: largest, smallest: smallest}));

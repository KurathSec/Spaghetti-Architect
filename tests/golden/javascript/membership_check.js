// Spaghetti Architect — generated module: membership
// Deliberately redundant, but syntactically correct and crash-free.

// --- run fixtures (inputs) ---
var data_list = [10, 20, 30, 40];
var search_val = 30;

// MEMBERSHIP_CHECK: is_found = search_val in data_list
var is_found = false;
try {
    if (data_list !== null && data_list !== undefined) {
        // SPAGH_001/006: explicit index loop instead of indexOf
        var _idx = 0;
        // SPAGH_010: recompute .length every iteration (de-hoisted)
        var _match_flag = false;
        for (_idx = 0; _idx < data_list.length; _idx++) {
            var _current = data_list[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 === 0) {
                if (search_val === _current) {
                    _match_flag = true;
                }
                else {
                    _match_flag = _match_flag;
                }
            }
        }
        if (_match_flag === true) {
            is_found = true;
        }
        else {
            is_found = false;
        }
    }
    else {
        is_found = false;
    }
}
catch (e) {
    is_found = false;
}

// emit result_vars as one JSON line for the validator
console.log(JSON.stringify({is_found: is_found}));

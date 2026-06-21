// Spaghetti Architect — generated module: grade
// Deliberately redundant, but syntactically correct and crash-free.

// --- run fixtures (inputs) ---
var score = 72;

// CONDITIONAL_SELECT: verdict = 'pass' if score >= 60 else 'fail'
var verdict = "fail";
try {
    // SPAGH_001/005: expand the ternary into an explicit if/else
    var _cond = 60 <= score;
    if (_cond) {
        verdict = "pass";
    }
    else {
        verdict = "fail";
        verdict = verdict;
    }
}
catch (e) {
    verdict = "fail";
}

// emit result_vars as one JSON line for the validator
console.log(JSON.stringify({verdict: verdict}));

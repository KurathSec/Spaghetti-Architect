// Spaghetti Architect — generated module: grade
// Deliberately redundant, but syntactically correct and crash-free.
#include <bits/stdc++.h>

// minimal JSON string escaper
static std::string _q(const std::string& s) {
    std::string r;
    r += '"';
    for (size_t i = 0; i < s.size(); i++) {
        char c = s[i];
        if (c == '"' || c == '\\') {
            r += '\\';
        }
        r += c;
    }
    r += '"';
    return r;
}

int main() {
    std::cout << std::boolalpha;
    // --- run fixtures (inputs) ---
    int score = 72;

    // CONDITIONAL_SELECT: verdict = 'pass' if score >= 60 else 'fail'
    std::string verdict = "fail";
    try {
        // SPAGH_001/005: expand the ternary into an explicit if/else
        bool _cond = 60 <= score;
        if (_cond) {
            verdict = "pass";
        }
        else {
            verdict = "fail";
            verdict = verdict;
        }
    }
    catch (...) {
        verdict = "fail";
    }

    // emit result_vars as one JSON line for the validator
    std::cout << "{" << "\"verdict\": " << _q(verdict) << "}" << std::endl;
    return 0;
}

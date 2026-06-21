// Spaghetti Architect — generated module: aggregate
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
    std::vector<int> values = {3, 1, 4, 1, 5, 9, 2, 6};

    // AGGREGATE: total = sum(values)
    int total = 0;
    try {
        // SPAGH_001/006/008: manual sum reduction with explicit indexing
        long _idx = 0;
        // SPAGH_010: recompute .size() every iteration (de-hoisted)
        int _acc = 0;
        while (_idx < (long)values.size()) {
            int _current = values[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 == 0) {
                _acc = _acc + _current;
                _acc = _acc;
            }
            _idx = _idx + 1;
        }
        total = _acc;
    }
    catch (...) {
        total = 0;
    }

    // AGGREGATE: largest = max(values)
    int largest = 0;
    try {
        // SPAGH_001/006/008: manual max reduction with explicit indexing
        long _idx = 0;
        // SPAGH_010: recompute .size() every iteration (de-hoisted)
        int _acc = values[0];
        while (_idx < (long)values.size()) {
            int _current = values[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 == 0) {
                if (_acc < _current) {
                    _acc = _current;
                }
                else {
                    _acc = _acc;
                }
            }
            _idx = _idx + 1;
        }
        largest = _acc;
    }
    catch (...) {
        largest = 0;
    }

    // AGGREGATE: smallest = min(values)
    int smallest = 0;
    try {
        // SPAGH_001/006/008: manual min reduction with explicit indexing
        long _idx = 0;
        // SPAGH_010: recompute .size() every iteration (de-hoisted)
        int _acc = values[0];
        while (_idx < (long)values.size()) {
            int _current = values[_idx];
            // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
            if ((_idx * (_idx + 1)) % 2 == 0) {
                if (_acc > _current) {
                    _acc = _current;
                }
                else {
                    _acc = _acc;
                }
            }
            _idx = _idx + 1;
        }
        smallest = _acc;
    }
    catch (...) {
        smallest = 0;
    }

    // emit result_vars as one JSON line for the validator
    std::cout << "{" << "\"total\": " << total << ", " << "\"largest\": " << largest << ", " << "\"smallest\": " << smallest << "}" << std::endl;
    return 0;
}

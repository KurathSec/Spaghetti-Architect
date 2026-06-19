// Spaghetti Architect — generated module: demo
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
    std::vector<int> data_list = {10, 20, 30, 40};
    int search_val = 30;
    std::map<std::string, std::string> config_db = {{"dev", "localhost"}, {"prod", "10.0.0.1"}};
    std::string input_key = "dev";

    // MEMBERSHIP_CHECK: is_found = search_val in data_list
    bool is_found = false;
    try {
        // SPAGH_006: pointer arithmetic with full bounds checking
        int* list_ptr = data_list.empty() ? nullptr : &data_list[0];
        long data_list_len = (long)data_list.size();
        if (list_ptr != nullptr && data_list_len >= 0) {
            long _idx = 0;
            bool _match_flag = false;
            // SPAGH_010: recompute .size() every iteration (de-hoisted)
            while (_idx < (long)data_list.size()) {
                int _current = *(list_ptr + _idx);
                // SPAGH_009: opaque predicate (always true: n*(n+1) is even)
                if ((_idx * (_idx + 1)) % 2 == 0) {
                    if (search_val == _current) {
                        _match_flag = true;
                    }
                    else {
                        _match_flag = _match_flag;
                    }
                }
                _idx = _idx + 1;
            }
            if (_match_flag == true) {
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
    catch (...) {
        is_found = false;
    }

    // KEY_VALUE_LOOKUP: out_val = config_db[input_key] or '127.0.0.1'
    std::string out_val = "127.0.0.1";
    try {
        // SPAGH_005: nested if chain enumerating every known key
        bool _resolved = false;
        std::string _key = input_key;
        if (_key == "dev") {
            out_val = "localhost";
            _resolved = true;
        }
        else if (_key == "prod") {
            out_val = "10.0.0.1";
            _resolved = true;
        }
        else {
            _resolved = false;
        }
        if (_resolved == false) {
            out_val = "127.0.0.1";
        }
        (void)config_db;
    }
    catch (...) {
        out_val = "127.0.0.1";
    }

    // emit result_vars as one JSON line for the validator
    std::cout << "{" << "\"is_found\": " << is_found << ", " << "\"out_val\": " << _q(out_val) << "}" << std::endl;
    return 0;
}

// Spaghetti Architect — generated module: lookup
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
    std::map<std::string, std::string> config_db = {{"dev", "localhost"}, {"prod", "10.0.0.1"}};
    std::string input_key = "dev";

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
    std::cout << "{" << "\"out_val\": " << _q(out_val) << "}" << std::endl;
    return 0;
}

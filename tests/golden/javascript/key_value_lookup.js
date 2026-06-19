// Spaghetti Architect — generated module: lookup
// Deliberately redundant, but syntactically correct and crash-free.

// --- run fixtures (inputs) ---
var config_db = {"dev": "localhost", "prod": "10.0.0.1"};
var input_key = "dev";

// KEY_VALUE_LOOKUP: out_val = config_db[input_key] or '127.0.0.1'
var out_val = "127.0.0.1";
try {
    if (config_db !== null && config_db !== undefined) {
        // SPAGH_005: switch enumerating every known key
        var _resolved = false;
        var _key = input_key;
        switch (_key) {
            case "dev":
                out_val = "localhost";
                _resolved = true;
                break;
            case "prod":
                out_val = "10.0.0.1";
                _resolved = true;
                break;
            default:
                _resolved = false;
                break;
        }
        if (_resolved === false) {
            out_val = "127.0.0.1";
        }
    }
    else {
        out_val = "127.0.0.1";
    }
}
catch (e) {
    out_val = "127.0.0.1";
}

// emit result_vars as one JSON line for the validator
console.log(JSON.stringify({out_val: out_val}));

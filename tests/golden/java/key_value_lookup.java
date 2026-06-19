// Spaghetti Architect — generated class: Lookup
// Deliberately redundant, but syntactically correct and crash-free.
import java.util.HashMap;
import java.util.Map;
public class Lookup {
    public static void main(String[] args) {
        // --- run fixtures (inputs) ---
        Map<String, String> config_db = new HashMap<>();
        config_db.put("dev", "localhost");
        config_db.put("prod", "10.0.0.1");
        String input_key = "dev";

        // KEY_VALUE_LOOKUP: out_val = config_db[input_key] or '127.0.0.1'
        String out_val = "127.0.0.1";
        try {
            if (config_db != null) {
                // SPAGH_005: nested if chain enumerating every known key
                boolean _resolved = false;
                String _key = input_key;
                if (_key.equals("dev")) {
                    out_val = "localhost";
                    _resolved = true;
                }
                else if (_key.equals("prod")) {
                    out_val = "10.0.0.1";
                    _resolved = true;
                }
                else {
                    _resolved = false;
                }
                if (_resolved == false) {
                    out_val = "127.0.0.1";
                }
            }
            else {
                out_val = "127.0.0.1";
            }
        }
        catch (Exception e) {
            out_val = "127.0.0.1";
        }

        // emit result_vars as one JSON line for the validator
        System.out.println("{" + "\"out_val\": " + _q(out_val) + "}");
    }

    // minimal JSON string escaper
    static String _q(String s) {
        StringBuilder b = new StringBuilder();
        b.append('"');
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '"' || c == '\\') {
                b.append('\\');
            }
            b.append(c);
        }
        b.append('"');
        return b.toString();
    }
}

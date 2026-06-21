// Spaghetti Architect — generated class: Grade
// Deliberately redundant, but syntactically correct and crash-free.
public class Grade {
    public static void main(String[] args) {
        // --- run fixtures (inputs) ---
        int score = 72;

        // CONDITIONAL_SELECT: verdict = 'pass' if score >= 60 else 'fail'
        String verdict = "fail";
        try {
            // SPAGH_001/005: expand the ternary into an explicit if/else
            boolean _cond = 60 <= score;
            if (_cond) {
                verdict = "pass";
            }
            else {
                verdict = "fail";
                verdict = verdict;
            }
        }
        catch (Exception e) {
            verdict = "fail";
        }

        // emit result_vars as one JSON line for the validator
        System.out.println("{" + "\"verdict\": " + _q(verdict) + "}");
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

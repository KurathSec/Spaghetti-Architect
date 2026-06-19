"""Generator registry: language -> generator instance (blueprint §13)."""

from .cpp_gen import CppGenerator
from .go_gen import GoGenerator
from .java_gen import JavaGenerator
from .javascript_gen import JavaScriptGenerator
from .python_gen import PythonGenerator

REGISTRY = {
    g.language: g
    for g in (
        PythonGenerator(),
        JavaScriptGenerator(),
        GoGenerator(),
        JavaGenerator(),
        CppGenerator(),
    )
}

"""Static gate and loader for the fixed `type: browser` replay scripts.

A repository-controlled script `scripts/browser_sources/<source_id>.py` is
replayed on every fetch, so it is loaded as data, not as a trusted module: the
AST gate below admits only the import names a script has to have and only names
the script binds itself, so no expression it can write reaches a module object,
the interpreter's builtins or anything else outside the page it is handed.

This module owns the filesystem and the interpreter entry points, which is
exactly why it is not importable from a script: `browser_contract` stays the
allowlisted half and holds nothing but the output contract.
"""

from __future__ import annotations

import ast
from pathlib import Path
from types import ModuleType
from typing import Any

from tentacles.sources.browser_contract import ExtractScript, ScriptGateError

# `{module: the only names a script may take from it}`. A module handle is a
# launcher for everything that module imports in turn — `typing.get_type_hints`
# evaluates annotation strings as code, and any `tentacles.*` module can reach
# the loader — so an import may only name the page type and the two contract
# models. Everything else a replay needs is plain Python: no shipped script
# imports `re`, `json`, `math`, `datetime`, `urllib.parse` or `dataclasses`.
ALLOWED_IMPORTS: dict[str, frozenset[str]] = {
    'playwright.async_api': frozenset({'Page'}),
    'tentacles.sources.browser_contract': frozenset({'BrowserExtractOutput', 'RawBrowserItem'}),
}
# Every name a script reads must be bound by the script itself or be one of
# these builtins: value constructors, collection helpers and exceptions. None of
# them holds a namespace or turns a string into code. `type` is absent on
# purpose — `type(name, bases, {...})` builds a class, with its hooks, without a
# `class` statement for the gate to see.
ALLOWED_BUILTINS: frozenset[str] = frozenset({
    'abs', 'all', 'any', 'bool', 'chr', 'dict', 'divmod', 'enumerate', 'float', 'frozenset',
    'int', 'isinstance', 'len', 'list', 'max', 'min', 'ord', 'print', 'range', 'repr',
    'reversed', 'round', 'set', 'sorted', 'str', 'sum', 'tuple', 'zip',
    'AssertionError', 'AttributeError', 'Exception', 'IndexError', 'KeyError', 'RuntimeError',
    'StopIteration', 'TimeoutError', 'TypeError', 'ValueError',
})
# Named only to make the rejection legible: the rule above already refuses every
# one of them, because none is a script-local binding or an allowlisted builtin.
DANGEROUS_NAMES: frozenset[str] = frozenset({
    '__builtins__', '__import__', 'breakpoint', 'compile', 'delattr', 'dir', 'eval', 'exec',
    'exit', 'getattr', 'globals', 'help', 'input', 'locals', 'memoryview', 'object', 'open',
    'quit', 'setattr', 'super', 'type', 'vars',
})
# An attribute is read off an object the script already holds, so the gate can
# only see its name: an underscore prefix is where the interpreter keeps a
# function's globals and a class's bases, and the rest reach the filesystem, a
# new process, a remote debugging endpoint or a host file through an otherwise
# plain page object. The README contract already says a script reads page text
# and downloads no binary content, so the writers are named here too. Frames are
# refused as a group: `gi_frame.f_back` / `f_code` reach an interpreter frame
# without naming an underscore attribute, and `str.format` walks attributes and
# dict keys from a format string, so neither may appear in the AST the gate sees.
FORBIDDEN_ATTRIBUTES: frozenset[str] = frozenset({
    'compile', 'connect', 'connect_over_cdp', 'delattr', 'eval', 'exec', 'executable_path',
    'f_back', 'f_builtins', 'f_code', 'f_globals', 'f_lasti', 'f_lineno', 'f_locals', 'f_trace',
    'fork', 'format', 'format_map', 'func_globals', 'getattr', 'gi_frame', 'gi_code',
    'import_module', 'install', 'launch', 'launch_persistent_context', 'launch_server',
    'load_script', 'open', 'pdf', 'popen', 'read_text', 'save_as', 'screenshot',
    'set_input_files', 'setattr', 'spawn', 'storage_state', 'system', 'tracing', 'write_text',
    # Raw CDP hands the script `Browser.setDownloadBehavior` and friends.
    'new_browser_cdp_session',
})
# Schemes that read the host rather than the web.
_LOCAL_SCHEMES: tuple[str, ...] = ('file:',)
# Exact statement types only: a future node type (`match`, `except*`, a type
# alias) is rejected by default. No `class` — its `__init_subclass__` hook runs
# when the class is defined — and no `global`/`nonlocal`/`del`, which only make
# the name rules harder to read.
ALLOWED_STATEMENTS: frozenset[type[ast.stmt]] = frozenset({
    ast.AnnAssign, ast.Assert, ast.Assign, ast.AsyncFor, ast.AsyncFunctionDef, ast.AsyncWith,
    ast.AugAssign, ast.Break, ast.Continue, ast.Expr, ast.For, ast.FunctionDef, ast.If,
    ast.Import, ast.ImportFrom, ast.Pass, ast.Raise, ast.Return, ast.Try, ast.While, ast.With,
})
# The wrappers a type name may sit under while it is still an annotation:
# `list[RawBrowserItem]`, `RawBrowserItem | None`, `contract.RawBrowserItem`.
_ANNOTATION_WRAPPERS: tuple[type[ast.expr], ...] = (
    ast.Attribute, ast.BinOp, ast.List, ast.Subscript, ast.Tuple,
)
# A script that never returns is a bug, not a page that changed.
MAX_SCRIPT_BYTES = 512 * 1024


def check_script(source: str, *, name: str) -> ast.Module:
    """Statically reject a script outside the import, name and attribute rules.

    Returns the parsed module so the caller compiles exactly what was checked.
    """
    if len(source.encode("utf-8", "ignore")) > MAX_SCRIPT_BYTES:
        raise ScriptGateError(f"browser script {name} exceeds {MAX_SCRIPT_BYTES} bytes")
    try:
        tree = ast.parse(source, filename=name)
    except SyntaxError as exc:
        raise ScriptGateError(f"browser script {name} is not valid Python (line {exc.lineno})") from None
    imported = _check_imports(tree, name)
    readable = _bound_names(tree) | ALLOWED_BUILTINS
    parents = _parents(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.stmt) and type(node) not in ALLOWED_STATEMENTS:
            raise ScriptGateError(_rejected(name, node, f"dangerous statement {type(node).__name__}"))
        if isinstance(node, (ast.arg, ast.FunctionDef, ast.AsyncFunctionDef)):
            # A definition or a parameter must not carry a name the gate bans:
            # `def __reduce__():` defines a protocol hook nothing asked for.
            _check_binding(node.arg if isinstance(node, ast.arg) else node.name, node, name)
        if isinstance(node, ast.Name):
            _check_name(node, name, imported=imported, readable=readable, parents=parents)
        elif isinstance(node, (ast.Attribute, ast.Subscript)):
            _check_reference(node, name, imported=imported)
        elif isinstance(node, ast.Call):
            # Playwright writes and reads host files on the caller's behalf:
            # `route.fulfill(path=…)`, `tracing.stop(path=…)` and
            # `storage_state(path=…)` all take a host path, and none of them is a
            # filesystem API the gate could otherwise see.
            if any(keyword.arg == 'path' for keyword in node.keywords):
                raise ScriptGateError(_rejected(name, node, "dangerous use of a host path argument"))
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            # A navigation to `file:` reads the host filesystem as the browser user.
            if node.value.strip().lower().startswith(_LOCAL_SCHEMES):
                raise ScriptGateError(_rejected(name, node, f"dangerous local URL {node.value[:24]!r}"))
    return tree


def _check_imports(tree: ast.Module, name: str) -> dict[str, str]:
    """Validate every import form; return the name each one binds.

    `import a.b` binds `a`, a handle on the whole package, so it is rejected
    whatever the module is: the only surviving spellings are `from <allowlisted
    module> import <allowlisted name>` and one allowlisted module under an alias,
    which the attribute rule below leaves with nothing to reach.
    """
    bound: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            exports = ALLOWED_IMPORTS.get(node.module or "") if not node.level else None
            if exports is None:
                raise ScriptGateError(_rejected(
                    name, node, f"dangerous import from {node.module or '.'!r}"))
            for alias in node.names:
                if alias.name not in exports:
                    raise ScriptGateError(_rejected(
                        name, node, f"dangerous import of {alias.name!r} from {node.module!r}"))
                bound[_import_binding(alias, name, node)] = node.module or ""
        elif isinstance(node, ast.Import):
            target = node.names[0]
            if len(node.names) != 1 or target.asname is None or target.name not in ALLOWED_IMPORTS:
                raise ScriptGateError(_rejected(name, node, f"dangerous import of {target.name!r}"))
            bound[_import_binding(target, name, node)] = target.name
    return bound


def _import_binding(alias: ast.alias, name: str, node: ast.AST) -> str:
    """The name an import binds; it may not shadow a builtin or a banned name."""
    binding = alias.asname or alias.name
    if binding in ALLOWED_BUILTINS or binding in DANGEROUS_NAMES or _is_dunder(binding):
        raise ScriptGateError(_rejected(name, node, f"dangerous import alias {binding!r}"))
    return binding


def _bound_names(tree: ast.Module) -> frozenset[str]:
    """Every name the script itself binds: parameters, assignments and targets.

    Deliberately flat across scopes: a name bound in one function and read in
    another is a `NameError` at runtime, never a capability, because nothing the
    script can bind is anything but a value it built itself.
    """
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            found.add(node.name)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
            found |= _argument_names(node.args)
        elif isinstance(node, ast.Assign):
            for target in node.targets:
                found |= _target_names(target)
        elif isinstance(node, (ast.AnnAssign, ast.AsyncFor, ast.AugAssign, ast.For)):
            found |= _target_names(node.target)
        elif isinstance(node, ast.withitem):
            if node.optional_vars is not None:
                found |= _target_names(node.optional_vars)
        elif isinstance(node, ast.ExceptHandler):
            if node.name:
                found.add(node.name)
        elif isinstance(node, (ast.NamedExpr, ast.comprehension)):
            found |= _target_names(node.target)
    return frozenset(found)


def _argument_names(args: ast.arguments) -> set[str]:
    names = {arg.arg for arg in (*args.posonlyargs, *args.args, *args.kwonlyargs)}
    for extra in (args.vararg, args.kwarg):
        if extra is not None:
            names.add(extra.arg)
    return names


def _target_names(target: ast.expr) -> set[str]:
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, ast.Starred):
        return _target_names(target.value)
    if isinstance(target, (ast.List, ast.Tuple)):
        names: set[str] = set()
        for element in target.elts:
            names |= _target_names(element)
        return names
    return set()


def _parents(tree: ast.Module) -> dict[ast.AST, ast.AST]:
    return {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}


def _check_binding(identifier: str, node: ast.AST, name: str) -> None:
    """The names a script may never spell, wherever it spells them."""
    if identifier in DANGEROUS_NAMES or _is_dunder(identifier):
        raise ScriptGateError(_rejected(name, node, f"dangerous name {identifier!r}"))


def _check_name(node: ast.Name, name: str, *, imported: dict[str, str],
                readable: frozenset[str], parents: dict[ast.AST, ast.AST]) -> None:
    """A name a script reads must be one it bound, or one allowlisted builtin."""
    _check_binding(node.id, node, name)
    if node.id in imported:
        # An imported name is a page type to annotate with or a model to build;
        # a second binding would launder that class's attributes into a local.
        if not _annotates_or_calls(node, parents):
            raise ScriptGateError(_rejected(name, node, f"dangerous use of imported name {node.id!r}"))
    elif node.id not in readable:
        raise ScriptGateError(_rejected(name, node, f"unknown name {node.id!r}"))


def _check_reference(node: ast.Attribute | ast.Subscript, name: str, *,
                     imported: dict[str, str]) -> None:
    """No private/launcher attribute, and no chain that starts at an import."""
    if isinstance(node, ast.Attribute) and (node.attr.startswith("_") or node.attr in FORBIDDEN_ATTRIBUTES):
        raise ScriptGateError(_rejected(name, node, f"dangerous attribute {node.attr!r}"))
    root = _chain_root(node)
    if isinstance(root, ast.Name) and root.id in imported:
        kind = "attribute" if isinstance(node, ast.Attribute) else "subscript"
        raise ScriptGateError(_rejected(name, node, f"dangerous {kind} on imported name {root.id!r}"))


def _chain_root(node: ast.expr) -> ast.expr:
    """The expression an attribute/subscript/call chain starts from."""
    while isinstance(node, (ast.Attribute, ast.Call, ast.Subscript)):
        node = node.value if isinstance(node, (ast.Attribute, ast.Subscript)) else node.func
    return node


def _annotates_or_calls(node: ast.Name, parents: dict[ast.AST, ast.AST]) -> bool:
    """Whether `node` is a call target or part of a type annotation."""
    parent = parents.get(node)
    if isinstance(parent, ast.Call) and parent.func is node:
        return True
    child: ast.AST = node
    while parent is not None:
        if isinstance(parent, ast.arg) and parent.annotation is child:
            return True
        if isinstance(parent, ast.AnnAssign) and parent.annotation is child:
            return True
        if isinstance(parent, (ast.FunctionDef, ast.AsyncFunctionDef)) and parent.returns is child:
            return True
        if not isinstance(parent, _ANNOTATION_WRAPPERS):
            return False
        child, parent = parent, parents.get(parent)
    return False


def _is_dunder(name: str) -> bool:
    return name.startswith("__") and name.endswith("__")


def _rejected(name: str, node: ast.AST, violation: str) -> str:
    return f"browser script {name} rejected: {violation} at line {getattr(node, 'lineno', 0)}"


def load_script(path: Path) -> ExtractScript:
    """Gate, compile and execute one fixed script; return its `extract` callable."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        raise ScriptGateError(f"browser script {path.name} cannot be read as UTF-8") from None
    code = compile(check_script(source, name=path.name), str(path), "exec")
    module = ModuleType(f"tentacles_browser_scripts.{path.stem}")
    module.__file__ = str(path)
    exec(code, module.__dict__)  # noqa: S102 - the AST gate above is the trust boundary
    extract: Any = module.__dict__.get("extract")
    if not callable(extract):
        raise ScriptGateError(f"browser script {path.name} must define async def extract(page, *, page_url, max_items, timeout_ms)")
    return extract

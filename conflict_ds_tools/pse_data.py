"""PSE data files - Lua tables, literals only (the records layer's format, PSE DESIGN §13.4).

The Python twin of the patch's C++ reader / writer (ConflictDesertStormPatch pse/records/data.h): both write the same
canonical bytes for the same input (the patch's scripts/records_gate.py checks it).

A file is `[-- header comments] return <table>`. Values: tables, strings, integers (64-bit), floats, true / false and
'-' before a number - nothing else (names, calls, operators, nil are refused with "line:column: what").
Full-line comments before an entry belong to it; other comments are dropped.

Canonical output (dumps): list items first in order; then "id", integer keys ascending, string keys in UTF-8 byte
order; a table with no comments and only scalars on one line when the whole line fits in 100 bytes; else one entry per
line, two spaces per level, a "," after every entry; strings in "..." with \\\\ \\" \\n \\r \\t and \\ddd for other
control bytes; integers in decimal, floats as Python's repr (shortest round trip; inf / nan refused).

In Python a document is a `Document(header=[...], root=Table)`; `Table` keeps entries in file order with their
comments: `t.items` (list part), `t["key"]`, `t[3]`, `t.set(...)`, `t.push(...)`, `t.to_python()`.
`loads(text)` / `dumps(doc)` / `canonical(text)`; `from_python(obj)` builds a Table from dicts / lists.
"""
import math

WIDTH = 100
MAX_DEPTH = 200
KEYWORDS = {"and", "break", "do", "else", "elseif", "end", "false", "for", "function", "goto", "if", "in", "local",
            "nil", "not", "or", "repeat", "return", "then", "true", "until", "while"}
INT_MIN, INT_MAX = -(1 << 63), (1 << 63) - 1


class DataError(ValueError):
    pass


def _is_name_start(c):
    return c.isascii() and (c.isalpha() or c == "_")


def _is_name_char(c):
    return c.isascii() and (c.isalnum() or c == "_")


def is_name(s):
    return bool(s) and _is_name_start(s[0]) and all(_is_name_char(c) for c in s) and s not in KEYWORDS


class Entry:
    __slots__ = ("key", "value", "comments")

    def __init__(self, key, value, comments=None):
        self.key = key            # None = a list item, str or int
        self.value = value
        self.comments = list(comments or [])


class Table:
    def __init__(self, entries=None):
        self.entries = list(entries or [])

    @property
    def items(self):
        return [e.value for e in self.entries if e.key is None]

    def _find(self, key):
        for e in self.entries:
            if e.key is not None and type(e.key) is type(key) and e.key == key:
                return e
        return None

    def __getitem__(self, key):
        e = self._find(key)
        if e is None:
            raise KeyError(key)
        return e.value

    def get(self, key, default=None):
        e = self._find(key)
        return default if e is None else e.value

    def __contains__(self, key):
        return self._find(key) is not None

    def set(self, key, value, comments=None):
        e = self._find(key)
        if e is None:
            self.entries.append(Entry(key, value, comments))
        else:
            e.value = value
            if comments is not None:
                e.comments = list(comments)
        return value

    __setitem__ = set

    def push(self, value, comments=None):
        self.entries.append(Entry(None, value, comments))
        return value

    def remove(self, key):
        self.entries = [e for e in self.entries if not (e.key is not None and type(e.key) is type(key) and e.key == key)]

    def to_python(self):
        """dict for keyed tables, list for pure lists (comments dropped)."""
        keyed = [e for e in self.entries if e.key is not None]
        if not keyed:
            return [_py(e.value) for e in self.entries]
        d = {e.key: _py(e.value) for e in keyed}
        items = [_py(e.value) for e in self.entries if e.key is None]
        for i, v in enumerate(items, 1):
            d[i] = v
        return d

    def __eq__(self, other):
        if not isinstance(other, Table):
            return NotImplemented
        a, b = _ordered(self), _ordered(other)
        return len(a) == len(b) and all(
            x.key == y.key and type(x.key) is type(y.key) and _value_eq(x.value, y.value) for x, y in zip(a, b))

    def __repr__(self):
        return "Table(%r)" % (self.to_python(),)


def _value_eq(a, b):
    if type(a) is not type(b):
        return False
    return a == b


def _py(v):
    return v.to_python() if isinstance(v, Table) else v


def from_python(obj):
    """dict / list / scalars -> Table (dict order kept; the writer orders keys anyway)."""
    if isinstance(obj, Table):
        return obj
    if isinstance(obj, dict):
        return Table([Entry(k, from_python(v)) for k, v in obj.items()])
    if isinstance(obj, (list, tuple)):
        return Table([Entry(None, from_python(v)) for v in obj])
    if isinstance(obj, bool) or isinstance(obj, (int, float, str)):
        return obj
    raise DataError("can't store %r" % (obj,))


class Document:
    def __init__(self, root=None, header=None):
        self.root = root if root is not None else Table()
        self.header = list(header or [])


# ---- reader ---------------------------------------------------------------------------------------------------------

class _Parser:
    def __init__(self, text):
        self.t = text
        self.p = 0
        self.pending = []

    def fail(self, what, at=None):
        p = self.p if at is None else at
        line = self.t.count("\n", 0, p) + 1
        col = p - (self.t.rfind("\n", 0, p) + 1) + 1
        raise DataError("%d:%d: %s" % (line, col, what))

    def peek(self, c):
        return self.p < len(self.t) and self.t[self.p] == c

    def word(self, w):
        n = len(w)
        if self.t[self.p:self.p + n] != w or (self.p + n < len(self.t) and _is_name_char(self.t[self.p + n])):
            return False
        self.p += n
        return True

    def long_level(self, at):
        t = self.t
        if at >= len(t) or t[at] != "[":
            return -1
        k = at + 1
        level = 0
        while k < len(t) and t[k] == "=":
            k += 1
            level += 1
        return level if k < len(t) and t[k] == "[" else -1

    def long_body(self, level):
        self.p += 2 + level
        if self.p < len(self.t) and self.t[self.p] == "\r":
            self.p += 1
        if self.p < len(self.t) and self.t[self.p] == "\n":
            self.p += 1
        close = "]" + "=" * level + "]"
        end = self.t.find(close, self.p)
        if end < 0:
            return None
        s = self.t[self.p:end]
        self.p = end + len(close)
        return s

    def skip_space(self):
        t = self.t
        line_start = self.p == 0 or t[self.p - 1] == "\n"
        while self.p < len(t):
            c = t[self.p]
            if c == "\n":
                line_start = True
                self.p += 1
            elif c in " \t\r":
                self.p += 1
            elif c == "-" and t[self.p + 1:self.p + 2] == "-":
                self.p += 2
                level = self.long_level(self.p)
                if level >= 0:
                    text = self.long_body(level)
                    if text is None:
                        text = t[self.p:]
                        self.p = len(t)
                    if line_start:
                        for l in text.split("\n"):
                            l = l.rstrip("\r \t")
                            if l.startswith(" "):
                                l = l[1:]
                            self.pending.append(l)
                else:
                    e = t.find("\n", self.p)
                    text = t[self.p:] if e < 0 else t[self.p:e]
                    self.p = len(t) if e < 0 else e
                    text = text.rstrip("\r \t")
                    if text.startswith(" "):
                        text = text[1:]
                    if line_start:
                        self.pending.append(text)
            else:
                break

    def run(self):
        self.skip_space()
        header = self.pending
        self.pending = []
        if not self.word("return"):
            self.fail("expected 'return' followed by a table")
        self.skip_space()
        self.pending = []
        if not self.peek("{"):
            self.fail("expected '{' after 'return'")
        root = self.table(0)
        self.skip_space()
        if self.p < len(self.t):
            self.fail("unexpected text after the table")
        return Document(root, header)

    def table(self, depth):
        if depth > MAX_DEPTH:
            self.fail("tables nested too deeply")
        t = self.t
        out = Table()
        self.p += 1
        list_count = 0
        while True:
            self.skip_space()
            if self.p >= len(t):
                self.fail("table not closed ('}' missing)")
            if self.peek("}"):
                self.p += 1
                self.pending = []
                break
            comments = self.pending
            self.pending = []
            key_at = self.p
            key = None
            if self.peek("[") and self.long_level(self.p) < 0:
                self.p += 1
                self.skip_space()
                k = self.scalar()
                if isinstance(k, bool) or not isinstance(k, (str, int)):
                    self.fail("a key must be a string or an integer", key_at)
                key = k
                self.skip_space()
                if not self.peek("]"):
                    self.fail("']' expected after the key")
                self.p += 1
                self.skip_space()
                if not self.peek("="):
                    self.fail("'=' expected after the key")
                self.p += 1
            elif self.p < len(t) and _is_name_start(t[self.p]):
                k = self.p
                while k < len(t) and _is_name_char(t[k]):
                    k += 1
                name = t[self.p:k]
                q = k
                while q < len(t) and t[q] in " \t\r\n":
                    q += 1
                if q < len(t) and t[q] == "=" and t[q + 1:q + 2] != "=" and is_name(name):
                    key = name
                    self.p = q + 1
            self.skip_space()
            value = self.table(depth + 1) if self.peek("{") else self.scalar()
            if key is None:
                list_count += 1
            elif out._find(key) is not None:
                self.fail("duplicate key", key_at)
            out.entries.append(Entry(key, value, comments))
            self.skip_space()
            if self.peek(",") or self.peek(";"):
                self.p += 1
                continue
            if self.peek("}"):
                continue
            self.fail("',' or '}' expected")
        for e in out.entries:
            if type(e.key) is int and 1 <= e.key <= list_count:
                self.fail("an integer key overlaps the list items")
        return out

    def scalar(self):
        t = self.t
        if self.p >= len(t):
            self.fail("value expected")
        c = t[self.p]
        if c in "\"'":
            return self.quoted()
        level = self.long_level(self.p)
        if level >= 0:
            s = self.long_body(level)
            if s is None:
                self.fail("long string not closed")
            return s
        if self.word("true"):
            return True
        if self.word("false"):
            return False
        if c == "-" or c.isdigit() or c == ".":
            return self.number()
        if self.word("nil"):
            self.fail("nil is not allowed (leave the entry out)")
        self.fail("only literals are allowed: tables, strings, numbers, true / false")

    def number(self):
        t = self.t
        neg = False
        if self.peek("-"):
            neg = True
            self.p += 1
            self.skip_space()
        s = self.p
        if t[self.p:self.p + 2] in ("0x", "0X"):
            self.p += 2
            v = 0
            n = 0
            while self.p < len(t) and t[self.p] in "0123456789abcdefABCDEF":
                v = (v * 16 + int(t[self.p], 16)) & 0xFFFFFFFFFFFFFFFF
                self.p += 1
                n += 1
            if not n or (self.p < len(t) and (_is_name_char(t[self.p]) or t[self.p] == ".")):
                self.fail("bad hexadecimal number")
            if neg:
                v = (-v) & 0xFFFFFFFFFFFFFFFF
            return v - (1 << 64) if v >= (1 << 63) else v
        is_float = False
        while self.p < len(t):
            d = t[self.p]
            if d.isdigit() and d.isascii():
                self.p += 1
            elif d in ".eE":
                is_float = True
                self.p += 1
                if d in "eE" and self.p < len(t) and t[self.p] in "+-":
                    self.p += 1
            else:
                break
        if self.p < len(t) and _is_name_char(t[self.p]):
            self.fail("bad number")
        text = t[s:self.p]
        if text in ("", "."):
            self.fail("bad number")
        if not is_float:
            v = int(text)
            if v > ((1 << 63) if neg else (1 << 63) - 1):
                self.fail("integer out of range (64-bit)")
            return -v if neg else v
        try:
            v = float(text)
        except ValueError:
            self.fail("bad number")
        return -v if neg else v

    def quoted(self):
        t = self.t
        q = t[self.p]
        self.p += 1
        out = bytearray()
        while True:
            if self.p >= len(t) or t[self.p] == "\n":
                self.fail("string not closed")
            c = t[self.p]
            self.p += 1
            if c == q:
                break
            if c != "\\":
                out += c.encode("utf-8")
                continue
            if self.p >= len(t):
                self.fail("string not closed")
            e = t[self.p]
            self.p += 1
            simple = {"a": 7, "b": 8, "f": 12, "n": 10, "r": 13, "t": 9, "v": 11, "\\": 92, '"': 34, "'": 39, "\n": 10}
            if e in simple:
                out.append(simple[e])
            elif e == "z":
                while self.p < len(t) and t[self.p] in " \t\r\n\f\v":
                    self.p += 1
            elif e == "x":
                h = t[self.p:self.p + 2]
                if len(h) != 2 or any(ch not in "0123456789abcdefABCDEF" for ch in h):
                    self.fail("bad \\x escape")
                out.append(int(h, 16))
                self.p += 2
            elif e == "u":
                if not self.peek("{"):
                    self.fail("bad \\u escape")
                self.p += 1
                cp = 0
                n = 0
                while self.p < len(t) and t[self.p] in "0123456789abcdefABCDEF":
                    cp = cp * 16 + int(t[self.p], 16)
                    self.p += 1
                    n += 1
                    if cp > 0x7FFFFFFF:
                        self.fail("bad \\u escape")
                if not n or not self.peek("}") or cp > 0x10FFFF:
                    self.fail("bad \\u escape")
                self.p += 1
                out += _utf8(cp)
            elif e.isdigit() and e.isascii():
                v = int(e)
                for _ in range(2):
                    if self.p < len(t) and t[self.p].isdigit() and t[self.p].isascii():
                        v = v * 10 + int(t[self.p])
                        self.p += 1
                if v > 255:
                    self.fail("bad \\ddd escape")
                out.append(v)
            else:
                self.fail("bad escape in a string")
        return out.decode("utf-8", errors="surrogateescape")


def _utf8(cp):
    # same bytes as the C++ (surrogates encoded as-is)
    if cp < 0x80:
        return bytes([cp])
    if cp < 0x800:
        return bytes([0xC0 | (cp >> 6), 0x80 | (cp & 0x3F)])
    if cp < 0x10000:
        return bytes([0xE0 | (cp >> 12), 0x80 | ((cp >> 6) & 0x3F), 0x80 | (cp & 0x3F)])
    return bytes([0xF0 | (cp >> 18), 0x80 | ((cp >> 12) & 0x3F), 0x80 | ((cp >> 6) & 0x3F), 0x80 | (cp & 0x3F)])


def loads(text):
    """Parse a PSE data file (str or bytes, UTF-8). Raises DataError("line:col: what")."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", errors="surrogateescape")
    if text.startswith("﻿"):
        text = text[1:]
    return _Parser(text).run()


# ---- writer ---------------------------------------------------------------------------------------------------------

def _bytes(s):
    return s.encode("utf-8", errors="surrogateescape")


def quote(s):
    out = bytearray(b'"')
    for c in _bytes(s):
        if c == 0x5C:
            out += b"\\\\"
        elif c == 0x22:
            out += b'\\"'
        elif c == 0x0A:
            out += b"\\n"
        elif c == 0x0D:
            out += b"\\r"
        elif c == 0x09:
            out += b"\\t"
        elif c < 0x20 or c == 0x7F:
            out += b"\\%03d" % c
        else:
            out.append(c)
    out += b'"'
    return out.decode("utf-8", errors="surrogateescape")


def format_float(v):
    if math.isinf(v) or math.isnan(v):
        raise DataError("inf / nan can't be written")
    return repr(float(v))


def _scalar(v):
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        if not INT_MIN <= v <= INT_MAX:
            raise DataError("integer out of range (64-bit)")
        return str(v)
    if isinstance(v, float):
        return format_float(v)
    if isinstance(v, str):
        return quote(v)
    raise DataError("can't write %r" % (v,))


def key_text(k):
    if k is None:
        return ""
    if isinstance(k, int) and not isinstance(k, bool):
        return "[%d] = " % k
    return (k if is_name(k) else "[" + quote(k) + "]") + " = "


def _ordered(t):
    lst = [e for e in t.entries if e.key is None]
    keyed = [e for e in t.entries if e.key is not None]

    def rank(e):
        if e.key == "id" and isinstance(e.key, str):
            return (0, b"", 0)
        if isinstance(e.key, int):
            return (1, b"", e.key)
        return (2, _bytes(e.key), 0)
    keyed.sort(key=rank)
    return lst + keyed


def _blen(s):
    return len(_bytes(s))


class _Writer:
    def __init__(self):
        self.out = []

    def inline(self, t):
        if not t.entries:
            return "{}"
        parts = []
        for e in _ordered(t):
            if e.comments or isinstance(e.value, Table):
                return None
            parts.append(key_text(e.key) + _scalar(e.value))
        return "{ " + ", ".join(parts) + " }"

    def emit(self, v, depth, prefix, suffix):
        if depth > MAX_DEPTH:
            raise DataError("tables nested too deeply")
        indent = "  " * depth
        if not isinstance(v, Table):
            self.out.append(indent + prefix + _scalar(v) + suffix + "\n")
            return
        one = self.inline(v)
        if one is not None and _blen(indent) + _blen(prefix) + _blen(one) + _blen(suffix) <= WIDTH:
            self.out.append(indent + prefix + one + suffix + "\n")
            return
        self.out.append(indent + prefix + "{\n")
        for e in _ordered(v):
            for c in e.comments:
                self.out.append(_comment(c, depth + 1))
            self.emit(e.value, depth + 1, key_text(e.key), ",")
        self.out.append(indent + "}" + suffix + "\n")


def _comment(text, depth):
    return "  " * depth + ("--" if not text else "-- " + text) + "\n"


def dumps(doc):
    """Canonical text of a Document (or a Table / dict / list for the root)."""
    if not isinstance(doc, Document):
        doc = Document(from_python(doc))
    w = _Writer()
    for c in doc.header:
        w.out.append(_comment(c, 0))
    w.emit(doc.root, 0, "return ", "")
    return "".join(w.out)


def dumps_bytes(doc):
    return _bytes(dumps(doc))


def canonical(text):
    return dumps(loads(text))


def load(path):
    with open(path, "rb") as f:
        return loads(f.read())


def save(path, doc):
    with open(path, "wb") as f:
        f.write(dumps_bytes(doc))

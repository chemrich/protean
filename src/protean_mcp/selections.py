"""PyMOL selection syntax → MolScript source.

Protean accepts PyMOL's selection algebra and compiles it to MolScript, which
Mol\\* evaluates. We own the grammar; Mol\\* owns execution. See PLAN.md
decision 5 for why we do not use Mol\\*'s bundled PyMOL transpiler: it parses
everything but answers several common idioms with a silent empty set, and a
wrong answer an agent cannot detect is worse than an error.

That principle drives the design here: anything this module cannot compile
*correctly* raises :class:`SelectionError`. It never degrades to an empty
selection.

Grammar (loosest to tightest binding, matching PyMOL):

    or_expr   := and_expr (('or' | '|') and_expr)*
    and_expr  := unary (('and' | '&') unary)*
    unary     := ('not' | '!') unary | modifier unary | postfix
    postfix   := primary (spatial_op)*
    spatial_op:= 'within' NUM 'of' primary
               | 'around' NUM
               | 'expand' NUM
    primary   := '(' or_expr ')' | property_sel | keyword_sel
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

__all__ = ["SelectionError", "to_molscript", "parse"]


class SelectionError(ValueError):
    """Raised for selections we cannot compile correctly.

    Deliberately preferred over returning an empty selection, so that a caller
    never mistakes "unsupported" for "nothing matched".
    """


# -- tokenizer ---------------------------------------------------------------

_TOKEN_RE = re.compile(
    r"""
      \s+
    | (?P<lparen>\()
    | (?P<rparen>\))
    | (?P<op><=|>=|!=|[<>=]|!|&|\|)
    | (?P<word>[^\s()<>=!&|]+)
    """,
    re.VERBOSE,
)


def _tokenize(text: str) -> list[tuple[str, str]]:
    tokens: list[tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        match = _TOKEN_RE.match(text, pos)
        if match is None:
            raise SelectionError(f"Unexpected character at offset {pos}: {text[pos]!r}")
        pos = match.end()
        for kind in ("lparen", "rparen", "op", "word"):
            value = match.group(kind)
            if value is not None:
                tokens.append((kind, value))
                break
    return tokens


# -- AST ---------------------------------------------------------------------


@dataclass(frozen=True)
class Keyword:
    """A zero-argument class selector: ``polymer``, ``solvent``, ``backbone``."""

    name: str


@dataclass(frozen=True)
class Property:
    """A property selector with a value list: ``chain A+B``, ``resi 50-60``."""

    prop: str
    values: tuple[str, ...]


@dataclass(frozen=True)
class Compare:
    """A numeric comparison: ``b > 50``."""

    prop: str
    op: str
    value: float


@dataclass(frozen=True)
class Not:
    child: object


@dataclass(frozen=True)
class And:
    left: object
    right: object


@dataclass(frozen=True)
class Or:
    left: object
    right: object


@dataclass(frozen=True)
class Modifier:
    """A prefix expansion: ``byres``, ``bychain``, ``first``."""

    kind: str
    child: object


@dataclass(frozen=True)
class Within:
    """``child within radius of target`` — atoms of *child* near *target*."""

    child: object
    radius: float
    target: object
    exclude_self: bool = False


@dataclass(frozen=True)
class Expand:
    """``child expand radius`` — *child* plus everything within *radius*."""

    child: object
    radius: float
    whole_residues: bool = False


# -- vocabulary --------------------------------------------------------------

# prop -> (molscript test level, molscript property, value kind)
_PROPERTIES: dict[str, tuple[str, str, str]] = {
    "chain": ("chain-test", "atom.auth_asym_id", "str"),
    "segi": ("chain-test", "atom.label_asym_id", "str"),
    "resi": ("residue-test", "atom.auth_seq_id", "int"),
    "resn": ("residue-test", "atom.label_comp_id", "str"),
    "name": ("atom-test", "atom.label_atom_id", "str"),
    "elem": ("atom-test", "atom.el", "str"),
    "alt": ("atom-test", "atom.label_alt_id", "str"),
    "index": ("atom-test", "atom.id", "int"),
}

# Mol* stores element symbols upper-cased, and mmCIF comp/atom ids are already
# upper case; PyMOL matches these case-insensitively, so normalise to match.
# Chain, segment and altloc ids stay verbatim — their case is significant.
_UPPERCASE_VALUES = frozenset({"elem", "resn", "name"})

_PROPERTY_ALIASES = {
    "c.": "chain",
    "s.": "segi",
    "segment": "segi",
    "i.": "resi",
    "resid": "resi",
    "r.": "resn",
    "resname": "resn",
    "n.": "name",
    "e.": "elem",
    "element": "elem",
    "idx.": "index",
}

# prop -> molscript property, for numeric comparisons
_COMPARABLE = {
    "b": "atom.B_iso_or_equiv",
    "q": "atom.occupancy",
}

_KEYWORD_ALIASES = {
    "*": "all",
    "water": "solvent",
    "het": "hetatm",
    "org": "organic",
    "ino": "inorganic",
    "bb.": "backbone",
    "sc.": "sidechain",
    "h.": "hydro",
    "hydrogens": "hydro",
    "polymer.protein": "protein",
    "polymer.nucleic": "nucleic",
    "ligand": "organic",
}

_MODIFIERS = {"byres", "bychain", "bymolecule", "first", "last", "neighbor", "bound_to"}

# Constructs we can parse but refuse to compile, with the reason. Keeping these
# explicit is what lets us fail loudly instead of silently returning nothing.
_UNSUPPORTED: dict[str, str] = {
    "ss": "Mol* exposes no mol-script alias for secondary-structure flags",
    "bymolecule": "atom.key.molecule returns an empty set on tested structures",
    "last": "Mol* provides sel.atom.first but no last-element filter",
    "neighbor": "bond-topology semantics not yet verified against PyMOL",
    "bound_to": "bond-topology semantics not yet verified against PyMOL",
    "extend": "PyMOL extends along bonds; no verified mol-script equivalent",
    "rank": "no mol-script equivalent for per-object atom rank",
    "pepseq": "sequence-motif matching not yet implemented",
    "like": "not implemented",
    "beyond": "not implemented",
    "near_to": "not implemented",
}

# Atomic numbers PyMOL counts as metals.
_METAL_NUMBERS = (
    3, 4, 11, 12, 13, 19, 20, 21, 22, 23, 24, 25, 26, 27, 28, 29, 30, 31,
    37, 38, 39, 40, 41, 42, 43, 44, 45, 46, 47, 48, 49, 50,
    55, 56, 57, 72, 73, 74, 75, 76, 77, 78, 79, 80, 81, 82, 83,
)

_BACKBONE_ATOMS = ("N", "CA", "C", "O")
_PROTEIN_SUBTYPES = ("polypeptide(L)", "polypeptide(D)", "cyclic-pseudo-peptide")
_NUCLEIC_SUBTYPES = (
    "polyribonucleotide",
    "polydeoxyribonucleotide",
    "polydeoxyribonucleotide/polyribonucleotide hybrid",
    "peptide nucleic acid",
)


# -- parser ------------------------------------------------------------------


@dataclass
class _Parser:
    tokens: list[tuple[str, str]]
    pos: int = field(default=0)

    def peek(self) -> tuple[str, str] | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def next(self) -> tuple[str, str]:
        token = self.peek()
        if token is None:
            raise SelectionError("Unexpected end of selection")
        self.pos += 1
        return token

    def at_word(self, *words: str) -> bool:
        token = self.peek()
        return token is not None and token[0] == "word" and token[1].lower() in words

    def at_op(self, *ops: str) -> bool:
        token = self.peek()
        return token is not None and token[0] == "op" and token[1] in ops

    # -- grammar rules

    def parse(self) -> object:
        node = self.or_expr()
        if self.peek() is not None:
            kind, value = self.peek()  # type: ignore[misc]
            raise SelectionError(f"Unexpected trailing token: {value!r}")
        return node

    def or_expr(self) -> object:
        node = self.and_expr()
        while self.at_word("or") or self.at_op("|"):
            self.next()
            node = Or(node, self.and_expr())
        return node

    def and_expr(self) -> object:
        node = self.unary()
        while self.at_word("and") or self.at_op("&"):
            self.next()
            node = And(node, self.unary())
        return node

    def unary(self) -> object:
        if self.at_word("not") or self.at_op("!"):
            self.next()
            return Not(self.unary())
        token = self.peek()
        if token is not None and token[0] == "word":
            word = token[1].lower()
            if word in _MODIFIERS:
                self.next()
                if word in _UNSUPPORTED:
                    raise SelectionError(
                        f"'{word}' is not supported: {_UNSUPPORTED[word]}"
                    )
                return Modifier(word, self.unary())
        return self.postfix()

    def postfix(self) -> object:
        node = self.primary()
        while True:
            if self.at_word("within"):
                self.next()
                radius = self._number()
                if not self.at_word("of"):
                    raise SelectionError("Expected 'of' after 'within <radius>'")
                self.next()
                node = Within(node, radius, self.primary())
            elif self.at_word("around"):
                self.next()
                radius = self._number()
                # PyMOL's `around` excludes the source atoms; `expand` keeps them.
                node = Within(Keyword("all"), radius, node, exclude_self=True)
            elif self.at_word("expand"):
                self.next()
                node = Expand(node, self._number())
            elif self.at_word("extend"):
                raise SelectionError(f"'extend' is not supported: {_UNSUPPORTED['extend']}")
            else:
                return node

    def primary(self) -> object:
        token = self.next()
        kind, value = token
        if kind == "lparen":
            node = self.or_expr()
            if self.peek() is None or self.peek()[0] != "rparen":  # type: ignore[index]
                raise SelectionError("Unbalanced parenthesis")
            self.next()
            return node
        if kind != "word":
            raise SelectionError(f"Unexpected token: {value!r}")

        word = value.lower()

        # `within 5 of X` with no left operand: PyMOL rejects it, users write it
        # anyway, and Mol*'s transpiler answers 0. Treat it as `all within ...`.
        if word in ("within", "around", "expand"):
            self.pos -= 1
            return Keyword("all")

        if word in _COMPARABLE:
            return self._comparison(word)

        prop = _PROPERTY_ALIASES.get(word, word)
        if prop in _PROPERTIES:
            return Property(prop, self._value_list())
        if prop in _UNSUPPORTED:
            raise SelectionError(f"'{word}' is not supported: {_UNSUPPORTED[prop]}")

        keyword = _KEYWORD_ALIASES.get(word, word)
        if keyword in _KEYWORD_EMITTERS:
            return Keyword(keyword)

        raise SelectionError(
            f"Unknown selection keyword: {value!r}. Supported keywords: "
            + ", ".join(sorted(set(_KEYWORD_EMITTERS) | set(_PROPERTIES) | set(_COMPARABLE)))
        )

    def _comparison(self, prop: str) -> Compare:
        if not self.at_op("<", ">", "<=", ">=", "=", "!="):
            raise SelectionError(f"Expected a comparison operator after '{prop}'")
        op = self.next()[1]
        return Compare(prop, op, self._number())

    def _number(self) -> float:
        kind, value = self.next()
        if kind != "word":
            raise SelectionError(f"Expected a number, got {value!r}")
        try:
            return float(value)
        except ValueError:
            raise SelectionError(f"Expected a number, got {value!r}") from None

    def _value_list(self) -> tuple[str, ...]:
        token = self.peek()
        if token is None or token[0] != "word":
            raise SelectionError("Expected a value after the selector")
        self.next()
        # PyMOL separates alternatives with '+' or ','; ranges use '-'.
        raw = token[1].replace(",", "+")
        values = tuple(v for v in raw.split("+") if v)
        if not values:
            raise SelectionError("Expected a value after the selector")
        return values


# -- emitter -----------------------------------------------------------------

# Apostrophes are safe bare — nucleic atom names like C1' tokenize fine.
_BARE_RE = re.compile(r"^[A-Za-z0-9_']+$")


def _atom(value: str) -> str:
    """Render a MolScript literal.

    mol-script delimits strings with backticks. Double and single quotes are
    *not* string syntax: it accepts them and then matches nothing, so a value
    like ``polypeptide(L)`` quoted the C-like way silently selects zero atoms.
    """
    if _BARE_RE.match(value):
        return value
    if "`" in value:
        raise SelectionError(f"Cannot quote a value containing a backtick: {value!r}")
    return f"`{value}`"


def _group(level: str, test: str) -> str:
    return f"(sel.atom.atom-groups :{level} {test})"


def _all() -> str:
    return "(sel.atom.all)"


def _intersect(left: str, right: str) -> str:
    return f"(sel.atom.intersect-by {left} :by {right})"


def _merge(left: str, right: str) -> str:
    return f"(sel.atom.merge {left} {right})"


def _except(left: str, right: str) -> str:
    return f"(sel.atom.except-by {left} :by {right})"


def _in_set(prop: str, values: tuple[str, ...]) -> str:
    if len(values) == 1:
        return f"(= {prop} {_atom(values[0])})"
    rendered = " ".join(_atom(v) for v in values)
    return f"(set.has (set {rendered}) {prop})"


def _int_terms(prop: str, values: tuple[str, ...], *, insertion_codes: bool = False) -> str:
    """Integer values with PyMOL range support: ``50-60+70``.

    When *insertion_codes* is set (``resi``), a trailing letter selects a single
    inserted residue — ``resi 100A`` — as used by antibody numbering schemes.
    """
    terms: list[str] = []
    for value in values:
        # Allow negative bounds: -5--1 means -5 to -1.
        match = re.fullmatch(r"(-?\d+)-(-?\d+)", value)
        if match:
            low, high = match.group(1), match.group(2)
            terms.append(f"(and (>= {prop} {low}) (<= {prop} {high}))")
            continue
        if insertion_codes:
            match = re.fullmatch(r"(-?\d+)([A-Za-z])", value)
            if match:
                number, code = match.group(1), match.group(2).upper()
                terms.append(
                    f"(and (= {prop} {number}) (= atom.pdbx_PDB_ins_code {code}))"
                )
                continue
        if not re.fullmatch(r"-?\d+", value):
            expected = "an integer, range, or insertion code" if insertion_codes else (
                "an integer or range"
            )
            raise SelectionError(f"Expected {expected}, got {value!r}")
        terms.append(f"(= {prop} {value})")
    if len(terms) == 1:
        return terms[0]
    return "(or " + " ".join(terms) + ")"


def _keyword_polymer() -> str:
    return _group("entity-test", "(= atom.entity-type polymer)")


def _keyword_backbone() -> str:
    return _intersect(
        _keyword_polymer(),
        _group("atom-test", _in_set("atom.label_atom_id", _BACKBONE_ATOMS)),
    )


def _keyword_nonpolymer() -> str:
    return _group("entity-test", "(= atom.entity-type non-polymer)")


def _keyword_branched() -> str:
    """Oligosaccharides. mmCIF types glycans as their own `branched` entity, so
    they are *not* reachable through non-polymer — the trap this keyword and the
    `organic` fix below both exist to avoid."""
    return _group("entity-test", "(= atom.entity-type branched)")


def _keyword_organic() -> str:
    """Carbon-containing residues outside the polymer — PyMOL's `organic`.

    Spans non-polymer *and* branched entities so that glycans on a glycoprotein
    are included, matching what PyMOL reports for the same structure.
    """
    het = _merge(_keyword_nonpolymer(), _keyword_branched())
    carbons = _group("atom-test", "(= atom.el C)")
    return _expand_property(_intersect(het, carbons), "atom.key.res")


def _expand_property(inner: str, prop: str) -> str:
    return f"(sel.atom.expand-property {inner} :property ({prop}))"


_KEYWORD_EMITTERS: dict[str, object] = {
    "all": _all,
    "none": lambda: "(sel.atom.empty)",
    "polymer": _keyword_polymer,
    "protein": lambda: _group(
        "entity-test", _in_set("atom.entity-subtype", _PROTEIN_SUBTYPES)
    ),
    "nucleic": lambda: _group(
        "entity-test", _in_set("atom.entity-subtype", _NUCLEIC_SUBTYPES)
    ),
    "solvent": lambda: _group("entity-test", "(= atom.entity-type water)"),
    "hetatm": lambda: _group("atom-test", "atom.is-het"),
    "backbone": _keyword_backbone,
    "sidechain": lambda: _except(_keyword_polymer(), _keyword_backbone()),
    "hydro": lambda: _group("atom-test", _in_set("atom.el", ("H", "D"))),
    "metals": lambda: _group(
        "atom-test",
        _in_set("atom.atomic-number", tuple(str(n) for n in _METAL_NUMBERS)),
    ),
    "organic": _keyword_organic,
    "inorganic": lambda: _except(_keyword_nonpolymer(), _keyword_organic()),
    # Beyond PyMOL, which has neither a carbohydrate nor an ion selector.
    "glycan": _keyword_branched,
    "ion": lambda: _group("entity-test", "(= atom.entity-subtype ion)"),
}


def _emit(node: object) -> str:
    if isinstance(node, Keyword):
        return _KEYWORD_EMITTERS[node.name]()  # type: ignore[operator]

    if isinstance(node, Property):
        level, prop, kind = _PROPERTIES[node.prop]
        if kind == "int":
            test = _int_terms(prop, node.values, insertion_codes=node.prop == "resi")
        else:
            values = node.values
            if node.prop in _UPPERCASE_VALUES:
                values = tuple(v.upper() for v in values)
            test = _in_set(prop, values)
        return _group(level, test)

    if isinstance(node, Compare):
        prop = _COMPARABLE[node.prop]
        op = "!=" if node.op == "!=" else node.op
        value = int(node.value) if node.value.is_integer() else node.value
        return _group("atom-test", f"({op} {prop} {value})")

    if isinstance(node, Not):
        return _except(_all(), _emit(node.child))

    if isinstance(node, And):
        return _intersect(_emit(node.left), _emit(node.right))

    if isinstance(node, Or):
        return _merge(_emit(node.left), _emit(node.right))

    if isinstance(node, Modifier):
        inner = _emit(node.child)
        if node.kind == "first":
            return f"(sel.atom.first {inner})"
        key = {"byres": "atom.key.res", "bychain": "atom.key.chain"}[node.kind]
        return _expand_property(inner, key)

    if isinstance(node, Within):
        radius = _render_number(node.radius)
        inner = _emit(node.child)
        target = _emit(node.target)
        near = f"(sel.atom.within {inner} :target {target} :max-radius {radius})"
        return _except(near, target) if node.exclude_self else near

    if isinstance(node, Expand):
        radius = _render_number(node.radius)
        whole = "true" if node.whole_residues else "false"
        return (
            f"(sel.atom.include-surroundings {_emit(node.child)} "
            f":radius {radius} :as-whole-residues {whole})"
        )

    raise SelectionError(f"Cannot compile node: {node!r}")


def _render_number(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else str(value)


# -- public API --------------------------------------------------------------


def parse(selection: str) -> object:
    """Parse a PyMOL selection into an AST. Raises :class:`SelectionError`."""
    if not selection or not selection.strip():
        raise SelectionError("Empty selection")
    return _Parser(_tokenize(selection)).parse()


def to_molscript(selection: str) -> str:
    """Compile a PyMOL selection string to MolScript source.

    >>> to_molscript("chain A")
    '(sel.atom.atom-groups :chain-test (= atom.auth_asym_id A))'
    """
    return _emit(parse(selection))

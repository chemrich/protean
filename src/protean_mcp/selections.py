"""PyMOL selection syntax → an AST.

Protean accepts PyMOL's selection algebra for leaf predicates and parses it
here. Evaluation lives in :mod:`selections_numpy`, against the coordinates we
hold in Python; this module owns the grammar and nothing else. See PLAN.md
decision 5 for why we do not use Mol\\*'s bundled PyMOL transpiler: it parses
everything but answers several common idioms with a silent empty set, and a
wrong answer an agent cannot detect is worse than an error.

That principle drives the design here: anything this module cannot parse
*correctly* raises :class:`SelectionError`. It never degrades to an empty
selection. The vocabulary tables below are what turn an unrecognised word into
an error rather than a query that matches nothing, so they must stay in step
with the evaluator — :mod:`tests.test_selections` asserts that they do.

This module compiled to MolScript until the Python evaluator took over. The
emitter is gone: the server had stopped calling it, and a second
implementation nobody runs is a liability, not a safety net.

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

__all__ = ["COMPARABLE", "KEYWORDS", "PROPERTIES", "SelectionError", "parse"]


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

# Property selectors that take a value list. Which field each one reads, and
# how case is treated, belongs to the evaluator; the grammar only needs to know
# the name is real.
PROPERTIES = frozenset({"chain", "segi", "resi", "resn", "name", "elem", "index", "ss"})

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

# Properties that take a numeric comparison rather than a value list.
COMPARABLE = frozenset({"b", "q"})

# The keywords the grammar accepts. This list is what makes an unknown keyword
# an error instead of a silent empty set, so it has to stay in step with the
# evaluator in selections_numpy; a test asserts every name here resolves there.
KEYWORDS = frozenset(
    {
        "all",
        "none",
        "polymer",
        "protein",
        "nucleic",
        "solvent",
        "hetatm",
        "backbone",
        "sidechain",
        "hydro",
        "metals",
        "organic",
        "inorganic",
        # Beyond PyMOL, which has neither a carbohydrate nor an ion selector.
        "glycan",
        "ion",
    }
)

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

# Constructs we can parse but refuse to evaluate, with the reason. Keeping
# these explicit is what lets us fail loudly instead of silently returning
# nothing — the caller learns the difference between "unsupported" and "no
# match", which is the whole reason this table exists.
_UNSUPPORTED: dict[str, str] = {
    "alt": (
        "alternate locations are resolved when coordinates are parsed, so no "
        "altloc field survives to select on"
    ),
    "bymolecule": "connected-molecule grouping is not implemented",
    "last": "no last-element filter; `first` is available",
    "neighbor": "bond-topology semantics not yet verified against PyMOL",
    "bound_to": "bond-topology semantics not yet verified against PyMOL",
    "extend": "PyMOL extends along bonds; bond topology is not available",
    "rank": "per-object atom rank is not tracked",
    "pepseq": "sequence-motif matching not yet implemented",
    "like": "not implemented",
    "beyond": "not implemented",
    "near_to": "not implemented",
}


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
            _, value = self.peek()  # type: ignore[misc]
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
                raise SelectionError(
                    f"'extend' is not supported: {_UNSUPPORTED['extend']}"
                )
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

        if word in COMPARABLE:
            return self._comparison(word)

        prop = _PROPERTY_ALIASES.get(word, word)
        if prop in PROPERTIES:
            return Property(prop, self._value_list())
        if prop in _UNSUPPORTED:
            raise SelectionError(f"'{word}' is not supported: {_UNSUPPORTED[prop]}")

        keyword = _KEYWORD_ALIASES.get(word, word)
        if keyword in KEYWORDS:
            return Keyword(keyword)

        raise SelectionError(
            f"Unknown selection keyword: {value!r}. Supported keywords: "
            + ", ".join(sorted(KEYWORDS | PROPERTIES | COMPARABLE))
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


# -- public API --------------------------------------------------------------


def parse(selection: str) -> object:
    """Parse a PyMOL selection into an AST. Raises :class:`SelectionError`."""
    if not selection or not selection.strip():
        raise SelectionError("Empty selection")
    return _Parser(_tokenize(selection)).parse()

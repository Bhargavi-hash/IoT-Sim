"""
lexer/tokenizer.py

Tokenizes the IoT schema language.
Handles all keywords across:
  CREATE DEVICE_CLASS, CREATE TABLE, CREATE EVENTTYPE,
  CREATE EVENTSTREAM, DISTRIBUTION FOR, and IoTDL rules.
"""

import re
from dataclasses import dataclass
from enum import Enum, auto
from typing import List


# -----------------------------------------------------------------------
# Token types
# -----------------------------------------------------------------------

class TT(Enum):
    # Literals
    INTEGER       = auto()
    FLOAT         = auto()
    STRING        = auto()      # quoted "..."
    IDENTIFIER    = auto()

    # Keywords — CREATE constructs
    CREATE        = auto()
    DEVICE_CLASS  = auto()
    TABLE         = auto()
    EVENTTYPE     = auto()
    EVENTSTREAM   = auto()
    DISTRIBUTION  = auto()
    FOR           = auto()

    # Keywords — DEVICE_CLASS body
    MEASURES      = auto()
    UNIT          = auto()
    RANGE         = auto()
    RESOLUTION    = auto()
    ZONE          = auto()
    ENUM          = auto()
    STAR          = auto()      # *

    # Keywords — TABLE body
    PRIMARY       = auto()
    KEY           = auto()
    REFERENCES    = auto()
    NULL          = auto()

    # Keywords — EVENTTYPE body
    EVENTTIME     = auto()

    # Keywords — EVENTSTREAM body
    ARRIVALS      = auto()
    PERIODIC      = auto()
    ON_CHANGE     = auto()
    ON_THRESHOLD  = auto()
    FAILURE       = auto()

    # Time units
    MS            = auto()
    SEC           = auto()
    MIN           = auto()
    HOURS         = auto()
    DAYS          = auto()

    # Keywords — DISTRIBUTION
    WHERE         = auto()
    ABOVE         = auto()
    BELOW         = auto()
    PROB          = auto()

    # Distribution types
    NORMAL        = auto()
    UNIFORM       = auto()
    EXPONENTIAL   = auto()
    POISSON       = auto()
    BINOMIAL      = auto()

    # Distribution params
    MEAN          = auto()
    STD_DEV       = auto()
    LOW           = auto()
    HIGH          = auto()
    N             = auto()
    P             = auto()

    # Data types
    INTEGER_TYPE  = auto()
    FLOAT_TYPE    = auto()
    STRING_TYPE   = auto()
    ENUM_TYPE     = auto()

    # IoTDL rule keywords
    NEW           = auto()
    SLIDING       = auto()
    TUMBLING      = auto()
    LANDMARK      = auto()
    AT            = auto()
    TURNSTILE     = auto()      # :-
    AVG           = auto()
    MIN_AGG       = auto()
    MAX_AGG       = auto()
    COUNT         = auto()
    SUM           = auto()
    LAST          = auto()

    # Functional dependency
    ARROW         = auto()      # -->

    # Punctuation
    LPAREN        = auto()      # (
    RPAREN        = auto()      # )
    LBRACKET      = auto()      # [
    RBRACKET      = auto()      # ]
    COMMA         = auto()      # ,
    SEMICOLON     = auto()      # ;
    DOT           = auto()      # .
    EQUALS        = auto()      # =
    PLUS          = auto()      # +
    MINUS         = auto()      # -
    UNDERSCORE    = auto()      # _
    GT            = auto()      # >
    LT            = auto()      # <

    # Special
    EOF           = auto()
    NEWLINE       = auto()


# -----------------------------------------------------------------------
# Token
# -----------------------------------------------------------------------

@dataclass
class Token:
    type:    TT
    value:   str
    line:    int
    column:  int

    def __repr__(self):
        return f'Token({self.type.name}, {self.value!r}, L{self.line}:C{self.column})'


# -----------------------------------------------------------------------
# Keyword map
# -----------------------------------------------------------------------

KEYWORDS: dict[str, TT] = {
    'CREATE':       TT.CREATE,
    'DEVICE_CLASS': TT.DEVICE_CLASS,
    'TABLE':        TT.TABLE,
    'EVENTTYPE':    TT.EVENTTYPE,
    'EVENTSTREAM':  TT.EVENTSTREAM,
    'DISTRIBUTION': TT.DISTRIBUTION,
    'FOR':          TT.FOR,

    'MEASURES':     TT.MEASURES,
    'UNIT':         TT.UNIT,
    'RANGE':        TT.RANGE,
    'RESOLUTION':   TT.RESOLUTION,
    'ZONE':         TT.ZONE,
    'ENUM':         TT.ENUM,

    'PRIMARY':      TT.PRIMARY,
    'KEY':          TT.KEY,
    'REFERENCES':   TT.REFERENCES,
    'NULL':         TT.NULL,

    'EVENTTIME':    TT.EVENTTIME,

    'ARRIVALS':     TT.ARRIVALS,
    'PERIODIC':     TT.PERIODIC,
    'ON_CHANGE':    TT.ON_CHANGE,
    'ON_THRESHOLD': TT.ON_THRESHOLD,
    'FAILURE':      TT.FAILURE,

    'MS':           TT.MS,
    'SEC':          TT.SEC,
    'MIN':          TT.MIN,
    'HOURS':        TT.HOURS,
    'DAYS':         TT.DAYS,

    'WHERE':        TT.WHERE,
    'ABOVE':        TT.ABOVE,
    'BELOW':        TT.BELOW,
    'PROB':         TT.PROB,

    'NORMAL':       TT.NORMAL,
    'UNIFORM':      TT.UNIFORM,
    'EXPONENTIAL':  TT.EXPONENTIAL,
    'POISSON':      TT.POISSON,
    'BINOMIAL':     TT.BINOMIAL,

    'mean':         TT.MEAN,
    'std_dev':      TT.STD_DEV,
    'low':          TT.LOW,
    'high':         TT.HIGH,
    'n':            TT.N,
    'p':            TT.P,

    'INTEGER':      TT.INTEGER_TYPE,
    'FLOAT':        TT.FLOAT_TYPE,
    'STRING':       TT.STRING_TYPE,

    'new':          TT.NEW,
    'sliding':      TT.SLIDING,
    'tumbling':     TT.TUMBLING,
    'landmark':     TT.LANDMARK,
    'avg':          TT.AVG,
    'min':          TT.MIN_AGG,
    'max':          TT.MAX_AGG,
    'count':        TT.COUNT,
    'sum':          TT.SUM,
    'last':         TT.LAST,
}


# -----------------------------------------------------------------------
# Lexer
# -----------------------------------------------------------------------

class LexerError(Exception):
    def __init__(self, msg: str, line: int, col: int):
        super().__init__(f'{msg} at line {line}, column {col}')
        self.line = line
        self.col  = col


class Lexer:
    def __init__(self, source: str):
        self.source  = source
        self.pos     = 0
        self.line    = 1
        self.column  = 1
        self.tokens: List[Token] = []

    def tokenize(self) -> List[Token]:
        while self.pos < len(self.source):
            self._skip_whitespace_and_comments()
            if self.pos >= len(self.source):
                break

            ch = self.source[self.pos]

            if ch == '\n':
                self.line   += 1
                self.column  = 1
                self.pos    += 1
                continue


                self._add(TT.ARROW, '-->', 3)
            elif self._peek(2) == ':-':
                self._add(TT.TURNSTILE, ':-', 2)

            # Single-char punctuation
            elif ch == '(':  self._add(TT.LPAREN,    ch, 1)
            elif ch == ')':  self._add(TT.RPAREN,    ch, 1)
            elif ch == '[':  self._add(TT.LBRACKET,  ch, 1)
            elif ch == ']':  self._add(TT.RBRACKET,  ch, 1)
            elif ch == ',':  self._add(TT.COMMA,     ch, 1)
            elif ch == ';':  self._add(TT.SEMICOLON, ch, 1)
            elif ch == '.':  self._add(TT.DOT,       ch, 1)
            elif ch == '=':  self._add(TT.EQUALS,    ch, 1)
            elif ch == '+':  self._add(TT.PLUS,      ch, 1)
            elif ch == '-':
                if self._peek(3) == '-->':
                    self._add(TT.ARROW, '-->', 3)
                else:
                    self._add(TT.MINUS, ch, 1)
            elif ch == '*':  self._add(TT.STAR,      ch, 1)
            elif ch == '>':  self._add(TT.GT,        ch, 1)
            elif ch == '<':  self._add(TT.LT,        ch, 1)
            elif ch == '@':  self._add(TT.AT, ch, 1)
            elif ch == '_':  self._scan_underscore()

            # String literal
            elif ch == '"':  self._scan_string()

            # Number
            elif ch.isdigit() or (ch == '-' and self._next_is_digit()):
                self._scan_number()

            # Identifier or keyword
            elif ch.isalpha() or ch == '_':
                self._scan_word()

            else:
                raise LexerError(f"Unexpected character {ch!r}", self.line, self.column)

        self.tokens.append(Token(TT.EOF, '', self.line, self.column))
        return self.tokens

    # ------------------------------------------------------------------

    def _peek(self, ahead: int) -> str:
        return self.source[self.pos:self.pos + ahead]

    def _next_is_digit(self) -> bool:
        return self.pos + 1 < len(self.source) and self.source[self.pos + 1].isdigit()

    def _add(self, tt: TT, value: str, length: int):
        self.tokens.append(Token(tt, value, self.line, self.column))
        self.pos    += length
        self.column += length

    def _skip_whitespace_and_comments(self):
        while self.pos < len(self.source):
            ch = self.source[self.pos]
            # Skip spaces and tabs
            if ch in (' ', '\t', '\r'):
                self.pos    += 1
                self.column += 1
            # Single-line comment: -- (but NOT -->) or //
            elif (self.source[self.pos:self.pos+2] == '--' and
                  self.source[self.pos:self.pos+3] != '-->') or \
                  self.source[self.pos:self.pos+2] == '//':
                while self.pos < len(self.source) and self.source[self.pos] != '\n':
                    self.pos += 1
            # Block comment: /* ... */
            elif self.source[self.pos:self.pos+2] == '/*':
                self.pos += 2
                while self.pos < len(self.source) - 1:
                    if self.source[self.pos:self.pos+2] == '*/':
                        self.pos += 2
                        break
                    if self.source[self.pos] == '\n':
                        self.line   += 1
                        self.column  = 1
                    self.pos += 1
            else:
                break

    def _scan_string(self):
        start_col = self.column
        self.pos += 1   # skip opening "
        buf = []
        while self.pos < len(self.source) and self.source[self.pos] != '"':
            buf.append(self.source[self.pos])
            self.pos    += 1
            self.column += 1
        self.pos    += 1   # skip closing "
        self.column += 1
        self.tokens.append(Token(TT.STRING, ''.join(buf), self.line, start_col))

    def _scan_number(self):
        start     = self.pos
        start_col = self.column
        is_float  = False
        if self.source[self.pos] == '-':
            self.pos += 1
        while self.pos < len(self.source) and self.source[self.pos].isdigit():
            self.pos += 1
        if self.pos < len(self.source) and self.source[self.pos] == '.':
            is_float = True
            self.pos += 1
            while self.pos < len(self.source) and self.source[self.pos].isdigit():
                self.pos += 1
        text = self.source[start:self.pos]
        tt   = TT.FLOAT if is_float else TT.INTEGER
        self.column += len(text)
        self.tokens.append(Token(tt, text, self.line, start_col))

    def _scan_word(self):
        start     = self.pos
        start_col = self.column
        while self.pos < len(self.source) and (
            self.source[self.pos].isalnum() or self.source[self.pos] in ('_',)
        ):
            self.pos    += 1
            self.column += 1
        word = self.source[start:self.pos]
        tt   = KEYWORDS.get(word, TT.IDENTIFIER)
        self.tokens.append(Token(tt, word, self.line, start_col))

    def _scan_underscore(self):
        # Could be wildcard _ or start of identifier _abc
        start_col = self.column
        start     = self.pos
        self.pos    += 1
        self.column += 1
        if self.pos < len(self.source) and (
            self.source[self.pos].isalnum() or self.source[self.pos] == '_'
        ):
            while self.pos < len(self.source) and (
                self.source[self.pos].isalnum() or self.source[self.pos] == '_'
            ):
                self.pos    += 1
                self.column += 1
            word = self.source[start:self.pos]
            tt   = KEYWORDS.get(word, TT.IDENTIFIER)
            self.tokens.append(Token(tt, word, self.line, start_col))
        else:
            self.tokens.append(Token(TT.UNDERSCORE, '_', self.line, start_col))

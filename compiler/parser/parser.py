"""
parser/parser.py

Recursive-descent parser for the IoT schema language.
Consumes a token list from the Lexer and produces a Program AST.
"""

from __future__ import annotations
from typing import List, Optional

from lexer.tokenizer import Token, TT
from ast_nodes.nodes import (
    ASTNode, Program,
    MeasuresDef, DeviceClassNode,
    ColumnDef, TableNode,
    AttributeDef, FunctionalDep, EventTypeNode,
    ArrivalSpec, EventStreamNode,
    DistributionSpec, TierSpec, WhereBlock, DistributionNode,
    AggFunction, WindowSpec, SourceClause, Constraint, RuleNode,
)


class ParseError(Exception):
    def __init__(self, msg: str, token: Token):
        super().__init__(f'{msg} — got {token.type.name}({token.value!r}) '
                         f'at line {token.line}, col {token.column}')
        self.token = token


class Parser:
    def __init__(self, tokens: List[Token]):
        self.tokens = tokens
        self.pos    = 0

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @property
    def current(self) -> Token:
        return self.tokens[self.pos]

    def peek(self, offset: int = 1) -> Token:
        idx = self.pos + offset
        return self.tokens[idx] if idx < len(self.tokens) else self.tokens[-1]

    def advance(self) -> Token:
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def expect(self, tt: TT) -> Token:
        if self.current.type != tt:
            raise ParseError(f'Expected {tt.name}', self.current)
        return self.advance()

    def match(self, *types: TT) -> bool:
        return self.current.type in types

    def consume_if(self, tt: TT) -> Optional[Token]:
        if self.current.type == tt:
            return self.advance()
        return None

    def skip_semicolons(self):
        while self.match(TT.SEMICOLON):
            self.advance()

    # ------------------------------------------------------------------
    # Entry point
    # ------------------------------------------------------------------

    def parse(self) -> Program:
        device_classes, tables, event_types = [], [], []
        event_streams, distributions, rules  = [], [], []

        while not self.match(TT.EOF):
            self.skip_semicolons()
            if self.match(TT.EOF):
                break

            if self.match(TT.CREATE):
                nxt = self.peek()
                if nxt.type == TT.DEVICE_CLASS:
                    device_classes.append(self._parse_device_class())
                elif nxt.type == TT.TABLE:
                    tables.append(self._parse_table())
                elif nxt.type == TT.EVENTTYPE:
                    event_types.append(self._parse_eventtype())
                elif nxt.type == TT.EVENTSTREAM:
                    event_streams.append(self._parse_eventstream())
                else:
                    raise ParseError('Unknown CREATE target', nxt)

            elif self.match(TT.DISTRIBUTION):
                distributions.append(self._parse_distribution())

            elif self.match(TT.NEW):
                rules.append(self._parse_rule())

            else:
                raise ParseError('Unexpected token at top level', self.current)

        return Program(
            device_classes=device_classes,
            tables=tables,
            event_types=event_types,
            event_streams=event_streams,
            distributions=distributions,
            rules=rules,
        )

    # ------------------------------------------------------------------
    # CREATE DEVICE_CLASS
    # ------------------------------------------------------------------

    def _parse_device_class(self) -> DeviceClassNode:
        line = self.current.line
        self.expect(TT.CREATE)
        self.expect(TT.DEVICE_CLASS)
        name = self.expect(TT.IDENTIFIER).value
        self.expect(TT.LPAREN)

        measures  = []
        zone_enum = False

        while not self.match(TT.RPAREN):
            if self.match(TT.MEASURES):
                measures.append(self._parse_measures())
            elif self.match(TT.ZONE):
                self.advance()               # ZONE
                self.expect(TT.ENUM)
                self.expect(TT.STAR)
                zone_enum = True
            self.consume_if(TT.COMMA)

        self.expect(TT.RPAREN)
        self.skip_semicolons()
        return DeviceClassNode(name=name, measures=measures, zone_enum=zone_enum)


    def _parse_signed_number(self) -> float:
        """Parse an optionally negative number."""
        sign = 1.0
        if self.match(TT.MINUS):
            self.advance()
            sign = -1.0
        return sign * float(self.advance().value)
    def _parse_measures(self) -> MeasuresDef:
        line = self.current.line
        self.expect(TT.MEASURES)
        name = self.expect(TT.IDENTIFIER).value
        self.expect(TT.UNIT)
        unit = self.expect(TT.STRING).value
        self.expect(TT.RANGE)
        self.expect(TT.LPAREN)
        rmin = self._parse_signed_number()
        self.expect(TT.COMMA)
        rmax = self._parse_signed_number()
        self.expect(TT.RPAREN)
        self.expect(TT.RESOLUTION)
        res  = float(self.advance().value)
        return MeasuresDef(name=name, unit=unit, range_min=rmin, range_max=rmax,
                           resolution=res)

    # ------------------------------------------------------------------
    # CREATE TABLE
    # ------------------------------------------------------------------

    def _parse_table(self) -> TableNode:
        line = self.current.line
        self.expect(TT.CREATE)
        self.expect(TT.TABLE)
        name = self.expect(TT.IDENTIFIER).value
        self.expect(TT.LPAREN)
        columns = []
        while not self.match(TT.RPAREN):
            columns.append(self._parse_column())
            self.consume_if(TT.COMMA)
        self.expect(TT.RPAREN)
        self.skip_semicolons()
        return TableNode(name=name, columns=columns)

    def _parse_column(self) -> ColumnDef:
        line = self.current.line
        name = self.expect(TT.IDENTIFIER).value

        # Data type
        if self.match(TT.INTEGER_TYPE, TT.FLOAT_TYPE, TT.STRING_TYPE, TT.ENUM_TYPE, TT.ENUM):
            dtype = self.advance().value.upper()
        else:
            dtype = self.advance().value.upper()

        primary_key = False
        references  = None
        nullable    = True

        # Optional modifiers
        while self.match(TT.PRIMARY, TT.REFERENCES):
            if self.match(TT.PRIMARY):
                self.advance()
                self.expect(TT.KEY)
                primary_key = True
                nullable    = False
            elif self.match(TT.REFERENCES):
                self.advance()
                # Handle REFERENCES DEVICE_CLASS (special keyword, no column)
                if self.match(TT.DEVICE_CLASS):
                    self.advance()
                    references = ('DEVICE_CLASS', 'name')
                else:
                    ref_table = self.expect(TT.IDENTIFIER).value
                    self.expect(TT.LPAREN)
                    ref_col   = self.expect(TT.IDENTIFIER).value
                    self.expect(TT.RPAREN)
                    references = (ref_table, ref_col)

        return ColumnDef(name=name, dtype=dtype, primary_key=primary_key,
                         references=references, nullable=nullable)

    # ------------------------------------------------------------------
    # CREATE EVENTTYPE
    # ------------------------------------------------------------------

    def _parse_eventtype(self) -> EventTypeNode:
        line = self.current.line
        self.expect(TT.CREATE)
        self.expect(TT.EVENTTYPE)
        name = self.expect(TT.IDENTIFIER).value
        self.expect(TT.LPAREN)

        eventtime_attr = 'T'
        attributes     = []
        func_deps      = []

        while not self.match(TT.RPAREN):
            # EVENTTIME T INTEGER
            if self.match(TT.EVENTTIME):
                self.advance()
                eventtime_attr = self.expect(TT.IDENTIFIER).value
                self.advance()   # INTEGER

            # Functional dependency: a, b --> c, d
            elif self._is_func_dep():
                func_deps.append(self._parse_func_dep())

            # Regular attribute
            else:
                attr_name  = self.expect(TT.IDENTIFIER).value
                attr_dtype = self.advance().value.upper()
                attributes.append(AttributeDef(name=attr_name, dtype=attr_dtype))

            self.consume_if(TT.COMMA)

        self.expect(TT.RPAREN)
        self.skip_semicolons()
        return EventTypeNode(name=name, eventtime_attr=eventtime_attr,
                             attributes=attributes, func_deps=func_deps)

    def _is_func_dep(self) -> bool:
        """Look ahead to detect 'a, b --> c' pattern within current line."""
        i = self.pos
        limit = min(i + 8, len(self.tokens))  # look at most 8 tokens ahead
        while i < limit:
            tt = self.tokens[i].type
            if tt == TT.ARROW:
                return True
            if tt not in (TT.IDENTIFIER, TT.COMMA):
                return False
            i += 1
        return False

    def _parse_func_dep(self) -> FunctionalDep:
        line = self.current.line
        lhs  = [self.expect(TT.IDENTIFIER).value]
        while self.match(TT.COMMA) and self.peek().type == TT.IDENTIFIER:
            self.advance()
            lhs.append(self.expect(TT.IDENTIFIER).value)
        self.expect(TT.ARROW)
        rhs = [self.expect(TT.IDENTIFIER).value]
        while self.match(TT.COMMA) and self.peek().type == TT.IDENTIFIER:
            self.advance()
            rhs.append(self.expect(TT.IDENTIFIER).value)
        return FunctionalDep(lhs=lhs, rhs=rhs)

    # ------------------------------------------------------------------
    # CREATE EVENTSTREAM
    # ------------------------------------------------------------------

    def _parse_eventstream(self) -> EventStreamNode:
        line = self.current.line
        self.expect(TT.CREATE)
        self.expect(TT.EVENTSTREAM)
        name = self.expect(TT.IDENTIFIER).value
        self.expect(TT.LPAREN)

        # EVENTTYPE et1, et2, ...
        self.expect(TT.EVENTTYPE)
        event_types = [self.expect(TT.IDENTIFIER).value]
        while self.match(TT.COMMA) and self.peek().type == TT.IDENTIFIER:
            self.advance()
            event_types.append(self.expect(TT.IDENTIFIER).value)

        # consume trailing comma after event types if present
        self.consume_if(TT.COMMA)
        # ARRIVALS ( ... )
        self.expect(TT.ARRIVALS)
        self.expect(TT.LPAREN)
        arrivals = []
        while not self.match(TT.RPAREN):
            arrivals.append(self._parse_arrival())
            self.consume_if(TT.COMMA)
        self.expect(TT.RPAREN)

        self.expect(TT.RPAREN)
        self.skip_semicolons()
        return EventStreamNode(name=name, event_types=event_types, arrivals=arrivals)

    def _parse_arrival(self) -> ArrivalSpec:
        line         = self.current.line
        measure_name = self.expect(TT.IDENTIFIER).value
        mode         = self.current.type.name
        interval_ms  = None
        threshold    = None

        if self.match(TT.PERIODIC):
            self.advance()
            self.expect(TT.LPAREN)
            value = float(self.advance().value)
            unit  = self.advance().value.upper()
            interval_ms = self._to_ms(value, unit)
            self.expect(TT.RPAREN)
            mode = 'PERIODIC'

        elif self.match(TT.ON_CHANGE):
            self.advance()
            self.expect(TT.LPAREN)
            threshold = float(self.advance().value)
            self.expect(TT.RPAREN)
            mode = 'ON_CHANGE'

        elif self.match(TT.ON_THRESHOLD):
            self.advance()
            self.expect(TT.LPAREN)
            threshold = float(self.advance().value)
            self.expect(TT.RPAREN)
            mode = 'ON_THRESHOLD'

        self.expect(TT.FAILURE)
        failure_prob = float(self.advance().value)

        return ArrivalSpec(measure_name=measure_name, mode=mode,
                           interval_ms=interval_ms, threshold=threshold,
                           failure_prob=failure_prob)

    def _to_ms(self, value: float, unit: str) -> float:
        return value * {
            'MS': 1, 'SEC': 1000, 'MIN': 60_000,
            'HOURS': 3_600_000, 'DAYS': 86_400_000
        }.get(unit, 1000)

    # ------------------------------------------------------------------
    # DISTRIBUTION FOR
    # ------------------------------------------------------------------

    def _parse_distribution(self) -> DistributionNode:
        line = self.current.line
        self.expect(TT.DISTRIBUTION)
        self.expect(TT.FOR)
        measure_name = self.expect(TT.IDENTIFIER).value
        self.expect(TT.LPAREN)

        where_blocks = []
        while self.match(TT.WHERE):
            where_blocks.append(self._parse_where_block())

        self.expect(TT.RPAREN)
        self.skip_semicolons()
        return DistributionNode(measure_name=measure_name,
                                where_blocks=where_blocks)

    def _parse_where_block(self) -> WhereBlock:
        line = self.current.line
        self.expect(TT.WHERE)
        # ZONE = <integer>
        self.expect(TT.ZONE)
        self.expect(TT.EQUALS)
        zone_id = int(self.expect(TT.INTEGER).value)
        self.expect(TT.LPAREN)

        tiers = []
        # First entry is always the NORMAL tier (unlabelled)
        normal_dist = self._parse_dist_spec()
        self.expect(TT.PROB)
        normal_prob = float(self.advance().value)
        tiers.append(TierSpec(tier='NORMAL', distribution=normal_dist,
                              probability=normal_prob))

        # Optional ABOVE and BELOW
        while self.match(TT.ABOVE, TT.BELOW):
            tier_name = self.advance().value.upper()
            self.expect(TT.LPAREN)
            dist = self._parse_dist_spec()
            self.expect(TT.PROB)
            prob = float(self.advance().value)
            self.expect(TT.RPAREN)
            tiers.append(TierSpec(tier=tier_name, distribution=dist,
                                  probability=prob))

        self.expect(TT.RPAREN)
        return WhereBlock(zone_id=zone_id, tiers=tiers)

    def _parse_dist_spec(self) -> DistributionSpec:
        line = self.current.line
        dist_type = self.advance().value.upper()
        self.expect(TT.LPAREN)
        params = {}
        while not self.match(TT.RPAREN):
            key = self.advance().value.lower()
            self.expect(TT.EQUALS)
            val = float(self.advance().value)
            params[key] = val
            self.consume_if(TT.COMMA)
        self.expect(TT.RPAREN)
        return DistributionSpec(dist_type=dist_type, params=params)

    # ------------------------------------------------------------------
    # IoTDL rules
    # ------------------------------------------------------------------

    def _parse_rule(self) -> RuleNode:
        line = self.current.line
        self.expect(TT.NEW)
        head_event = self.expect(TT.IDENTIFIER).value

        # Window: [sliding(s, 10)]
        self.expect(TT.LBRACKET)
        wtype    = self.advance().value.lower()
        self.expect(TT.LPAREN)
        slide_var = self.advance().value
        self.expect(TT.COMMA)
        size      = int(self.advance().value)
        self.expect(TT.RPAREN)
        self.expect(TT.RBRACKET)
        window = WindowSpec(window_type=wtype, slide_var=slide_var, size=size)

        # Head args: (PID, (avg_value = avg(value)))
        self.expect(TT.LPAREN)
        grouping_vars = []
        agg_functions = []
        while not self.match(TT.RPAREN):
            if self.match(TT.LPAREN):
                # Aggregation expression
                self.advance()
                out_var = self.advance().value
                self.expect(TT.EQUALS)
                func    = self.advance().value.lower()
                self.expect(TT.LPAREN)
                in_var  = self.advance().value
                self.expect(TT.RPAREN)
                self.expect(TT.RPAREN)
                agg_functions.append(AggFunction(output_var=out_var, func=func,
                                                 input_var=in_var))
            elif self.match(TT.IDENTIFIER):
                grouping_vars.append(self.advance().value)
            self.consume_if(TT.COMMA)
        self.expect(TT.RPAREN)

        # @ fire_time
        self.expect(TT.AT)
        self.expect(TT.LPAREN)
        fire_parts = []
        while not self.match(TT.RPAREN):
            fire_parts.append(self.advance().value)
        self.expect(TT.RPAREN)
        fire_time_expr = ' '.join(fire_parts)

        # :- body
        self.expect(TT.TURNSTILE)
        sources, constraints = self._parse_body()
        self.skip_semicolons()

        return RuleNode(
            head_event=head_event, window=window,
            grouping_vars=grouping_vars, agg_functions=agg_functions,
            fire_time_expr=fire_time_expr, sources=sources,
            constraints=constraints,
        )

    def _parse_body(self):
        sources     = []
        constraints = []

        while True:
            # Source clause: event_name(vars) @ z
            if (self.match(TT.IDENTIFIER) and
                    self.peek().type == TT.LPAREN):
                ev_name = self.advance().value
                self.expect(TT.LPAREN)
                bound_vars = []
                while not self.match(TT.RPAREN):
                    if self.match(TT.UNDERSCORE):
                        bound_vars.append('_')
                        self.advance()
                    elif self.match(TT.IDENTIFIER):
                        bound_vars.append(self.advance().value)
                    elif self.match(TT.COMMA):
                        self.advance()
                    else:
                        self.advance()
                self.expect(TT.RPAREN)
                self.expect(TT.AT)
                time_var = self.advance().value
                sources.append(SourceClause(event_name=ev_name, bound_vars=bound_vars,
                                            time_var=time_var))

            # Constraint: value < 25
            elif self.match(TT.IDENTIFIER):
                lhs = self.advance().value
                op  = self.advance().value
                rhs = self.advance().value
                constraints.append(Constraint(lhs=lhs, op=op, rhs=rhs))

            else:
                break

            if self.match(TT.COMMA):
                self.advance()
            else:
                break

        return sources, constraints

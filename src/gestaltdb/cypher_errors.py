"""Source-located errors shared by Cypher parsing and semantic analysis."""


class CypherSyntaxError(ValueError):
    """A Cypher syntax error with a source position."""

    def __init__(self, message: str, *, line: int, column: int, offset: int, source: str):
        super().__init__(f"{message} (line {line}, column {column})")
        self.message = message
        self.line = line
        self.column = column
        self.offset = offset
        self.source = source


class CypherSemanticError(ValueError):
    """A semantically invalid Cypher query with a source position."""

    def __init__(self, message: str, *, line: int, column: int, offset: int, source: str):
        super().__init__(f"{message} (line {line}, column {column})")
        self.message = message
        self.line = line
        self.column = column
        self.offset = offset
        self.source = source

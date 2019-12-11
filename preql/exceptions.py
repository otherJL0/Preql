from typing import Optional

from .utils import dataclass

@dataclass
class Meta:
    start_pos: int
    start_line: int
    start_column: int
    end_pos: int
    end_line: int
    end_column: int
    parent: Optional[object] = None

    def remake(self, parent):
        return Meta(
            self.start_pos,
            self.start_line,
            self.start_column,
            self.end_pos,
            self.end_line,
            self.end_column,
            parent
        )

    def __repr__(self):
        return '<Meta>'

@dataclass
class PreqlError(Exception):
    meta: Optional[Meta]

    def remake(self, source_code):
        return type(self)(self.meta, self.message, source_code)

    def get_context(self, text, span=40):
        MARK_CHAR = '-'
        pos = self.meta.start_pos
        start = max(pos - span, 0)
        end = pos + span
        text_before = text[start:pos].rsplit('\n', 1)[-1]
        text_after = text[pos:end].split('\n', 1)[0]
        parent = self.meta.parent
        mark_before = mark_after = 0
        if parent:
            mark_before = min(len(text_before), pos - parent.start_pos)
            mark_after = min(len(text_after), parent.end_pos - pos - 1)
            assert mark_before >= 0 and mark_after >= 0

        return ''.join([text_before, text_after, '\n',
               ' ' * (len(text_before)-mark_before), MARK_CHAR*mark_before, '^', MARK_CHAR*mark_after, '\n'
        ])

    def __str__(self):
        return self.message




@dataclass
class pql_NameNotFound(PreqlError):
    name: str
    source_code: Optional[str] = None

    def remake(self, source_code):
        return type(self)(self.meta, self.name, source_code)

    @property
    def message(self):
        return "Name not found: '%s'" % self.name

@dataclass
class pql_TypeError(PreqlError):
    message: str
    source_code: Optional[str] = None

@dataclass
class pql_ValueError(PreqlError):
    message: str
    source_code: Optional[str] = None

@dataclass
class pql_AttributeError(PreqlError):
    message: str
    source_code: Optional[str] = None

@dataclass
class pql_SyntaxError(PreqlError):
    message: str
    source_code: Optional[str] = None

@dataclass
class pql_JoinError(PreqlError):
    message: str
    source_code: Optional[str] = None



@dataclass
class ReturnSignal(Exception):
    value: object


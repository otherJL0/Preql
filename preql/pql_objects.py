"""
A collection of objects that may come to interaction with the user.
"""

from typing import List, Optional, Callable

from .utils import dataclass, SafeDict, safezip, split_at_index
from .exceptions import pql_TypeError
from .pql_types import PqlType, PqlObject, ColumnType, StructColumnType, DatumColumnType
from .pql_ast import Expr
from .sql import Sql

# Functions
@dataclass
class Param:
    name: str
    type: PqlType = None

class Function(PqlObject):
    param_collector = None

    def match_params(self, args):
        # TODO Default values (maybe just initialize match_params with them?)
        # total_params = length(params)

        for i, arg in enumerate(args):
            if arg.name:  # First keyword argument
                pos_args, named_args = split_at_index(args, i)
                pos_params, named_params = split_at_index(self.params, i)
                break
        else:   # No keywords
            pos_args = list(args)
            pos_params = self.params
            named_args = []
            named_params = []

        if len(pos_params) != len(pos_args):
            raise pql_TypeError(f"Function '{self.name}' takes {len(pos_params)} parameters but recieved {len(pos_args)} arguments.")

        matched = [(p, arg.value) for (p,arg) in safezip(pos_params, pos_args)]

        args_d = {na.name: na.value for na in named_args}

        for np in named_params:
            try:
                matched.append(np, args_d.pop(np.name))
            except KeyError:
                raise pql_TypeError("Parameter wasn't assigned: %s" % np.name)

        assert len(matched) == len(self.params)
        if args_d:  # Still left-over keywords?
            if self.param_collector:
                matched.append(self.param_collector, args_d)
            else:
                raise pql_TypeError(f"Function doesn't accept arguments named $(keys(args_d))")

        return matched



@dataclass
class UserFunction(Function):
    name: str
    expr: Expr
    params: List[Param]
    param_collector: Optional[Param] = None

@dataclass
class InternalFunction(Function):
    name: str
    func: Callable
    params: List[Param]
    param_collector: Optional[Param] = None


# Collections

@dataclass
class List_(Expr):
    elems: list

# @dataclass
# class Dict_(Expr):
#     elems: dict


# Other

@dataclass
class Instance:
    code: Sql
    type: PqlType

    subqueries: SafeDict

    @classmethod
    def make(cls, code, type_, instances):
        return cls(code, type_, merge_subqueries(instances))


@dataclass
class ColumnInstance(Instance):
    pass
    # def flatten(self):
    #     # if isinstance(self.type, types.StructColumnType):
    #     #     return [x for m in self.]
    #     assert False



class DatumColumnInstance(ColumnInstance):
    def flatten(self):
        return [self]

@dataclass
class StructColumnInstance(ColumnInstance):
    members: dict


def make_column_instance(code, type_):
    if isinstance(type_, StructColumnType):
        import pdb
        pdb.set_trace()
    else:
        return DatumColumnInstance.make(code, type_, [])
    assert False, type_


@dataclass
class TableInstance(Instance):
    columns: dict


def merge_subqueries(instances):
    return SafeDict().update(*[i.subqueries for i in instances])

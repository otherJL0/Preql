from contextlib import contextmanager
from copy import copy

import dsnparse

from runtype import Dispatch

from .exceptions import pql_NameNotFound, pql_TypeError, Meta

from . import pql_ast as ast
from . import pql_objects as objects
from . import pql_types as types
from . import sql
from .sql_interface import SqliteInterface, PostgresInterface

dy = Dispatch()

# Define common dispatch functions
@dy
def simplify():
    raise NotImplementedError()

@dy
def evaluate():
    raise NotImplementedError()


class AccessLevels:
    COMPILE = 1
    EVALUATE = 2
    READ_DB = 3
    WRITE_DB = 4

class State:
    AccessLevels = AccessLevels

    def __init__(self, db, fmt, ns=None):
        self.db = db
        self.fmt = fmt

        self.ns = ns or [{}]
        self.tick = [0]

        self.access_level = AccessLevels.WRITE_DB

    def get_var(self, name):
        for scope in reversed(self.ns):
            if name in scope:
                return scope[name]

        raise pql_NameNotFound(getattr(name, 'meta', None), str(name))

    def set_var(self, name, value):
        assert not isinstance(value, ast.Name)
        self.ns[-1][name] = value

    def get_all_vars(self):
        d = {}
        for scope in self.ns:
            d.update(scope) # Overwrite upper scopes
        return d

    def push_scope(self):
        self.ns.append({})

    def pop_scope(self):
        return self.ns.pop()


    def __copy__(self):
        s = State(self.db, self.fmt)
        s.ns = [dict(n) for n in self.ns]
        s.tick = self.tick
        s.access_level = self.access_level
        return s

    def reduce_access(self, new_level):
        assert new_level <= self.access_level
        s = copy(self)
        s.access_level = new_level
        return s

    @contextmanager
    def use_scope(self, scope: dict):
        x = len(self.ns)
        self.ns.append(scope)
        try:
            yield
        finally:
            self.ns.pop()
            assert x == len(self.ns)

    def connect(self, uri):
        print(f"[Preql] Connecting to {uri}")
        self.db = create_engine(uri, self.db._debug)


def create_engine(db_uri, debug):
    dsn = dsnparse.parse(db_uri)
    if len(dsn.paths) != 1:
        raise ValueError("Bad value for uri: %s" % db_uri)
    path ,= dsn.paths
    if dsn.scheme == 'sqlite':
        return SqliteInterface(path, debug=debug)
    elif dsn.scheme == 'postgres':
        return PostgresInterface(dsn.host, path, dsn.user, dsn.password, debug=debug)

    raise NotImplementedError(f"Scheme {dsn.scheme} currently not supported")



def get_alias(state: State, obj):
    if isinstance(obj, objects.TableInstance):
        return get_alias(state, obj.type.name)

    state.tick[0] += 1
    return obj + str(state.tick[0])


def assert_type(meta, t, type_, msg):
    if not isinstance(t, type_):
        raise pql_TypeError(meta, msg % (type_, t))

sql_repr = objects.sql_repr
make_value_instance = objects.make_value_instance
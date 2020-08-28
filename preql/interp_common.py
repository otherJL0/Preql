from contextlib import contextmanager
from copy import copy
from logging import getLogger

import dsnparse

from runtype import Dispatch

from .exceptions import pql_AttributeError, pql_NameNotFound, pql_TypeError, InsufficientAccessLevel, pql_NotImplementedError, pql_DatabaseConnectError, pql_ValueError

from . import pql_ast as ast
from . import pql_objects as objects
from .sql_interface import MysqlInterface, SqliteInterface, PostgresInterface, GitqliteInterface, ConnectError
from .pql_types import Type

dy = Dispatch()

logger = getLogger('interp')

# Define common dispatch functions
@dy
def simplify():
    raise NotImplementedError()

@dy
def evaluate(state: type(None), _any: type(None)):
    raise NotImplementedError()

@dy
def cast_to_python(state: type(NotImplemented), obj):
    raise NotImplementedError()


class AccessLevels:
    COMPILE = 1
    # COMPILE_TEXT
    EVALUATE = 2
    READ_DB = 3
    WRITE_DB = 4

class State:
    AccessLevels = AccessLevels

    def __init__(self, interp, db, fmt, ns=None):
        self.db = db
        self.interp = interp
        self.fmt = fmt
        # Add logger?

        self.ns = Namespace(ns)
        self.tick = [0]

        self.access_level = AccessLevels.WRITE_DB
        self._cache = {}
        self.stacktrace = []

    @classmethod
    def clone(cls, inst):
        s = cls(inst.interp, inst.db, inst.fmt)
        s.ns = copy(inst.ns)
        s.tick = inst.tick
        s.access_level = inst.access_level
        s._cache = inst._cache
        s.stacktrace = copy(inst.stacktrace)
        return s

    def __copy__(self):
        return self.clone(self)

    def reduce_access(self, new_level):
        assert new_level <= self.access_level
        s = copy(self)
        s.access_level = new_level
        return s

    def require_access(self, level):
        if self.access_level < level:
            raise InsufficientAccessLevel()
    def catch_access(self, level):
        if self.access_level < level:
            raise Exception("Bad access. Security risk.")

    def connect(self, uri):
        logger.info(f"[Preql] Connecting to {uri}")
        try:
            self.db = create_engine(uri, self.db._print_sql)
        except NotImplementedError as e:
            raise pql_NotImplementedError.make(self, None, *e.args) from e
        except ConnectError as e:
            raise pql_DatabaseConnectError.make(self, None, *e.args) from e
        except ValueError as e:
            raise pql_ValueError.make(self, None, *e.args) from e

        self.interp.include('core.pql', __file__) # TODO use an import mechanism instead


    def get_all_vars(self):
        return self.ns.get_all_vars()

    def get_var(self, name):
        try:
            return self.ns.get_var(self, name)
        except pql_NameNotFound as e:
            try:
                core = self.ns.get_var(self, '__builtins__')
            except pql_NameNotFound:
                raise e

            try:
                return core.get_attr(name)
            except pql_AttributeError:
                raise e

            assert False

    def set_var(self, name, value):
        return self.ns.set_var(name, value)
    def use_scope(self, scope: dict):
        return self.ns.use_scope(scope)



    def unique_name(self, obj):
        self.tick[0] += 1
        return obj + str(self.tick[0])


class Namespace:
    def __init__(self, ns=None):
        self.ns = ns or [{}]

    def __copy__(self):
        return Namespace([dict(n) for n in self.ns])

    def get_var(self, state, name):
        for scope in reversed(self.ns):
            if name in scope:
                return scope[name]

        raise pql_NameNotFound.make(state, name, str(name))

    def set_var(self, name, value):
        assert not isinstance(value, ast.Name)
        self.ns[-1][name] = value


    @contextmanager
    def use_scope(self, scope: dict):
        x = len(self.ns)
        self.ns.append(scope)
        try:
            yield
        finally:
            self.ns.pop()
            assert x == len(self.ns)

    def push_scope(self):
        self.ns.append({})

    def pop_scope(self):
        return self.ns.pop()

    def get_all_vars(self):
        d = {}
        for scope in reversed(self.ns):
            d.update(scope) # Overwrite upper scopes
        return d




def create_engine(db_uri, print_sql):
    dsn = dsnparse.parse(db_uri)
    if len(dsn.paths) != 1:
        raise ValueError("Bad value for uri: %s" % db_uri)
    path ,= dsn.paths
    if dsn.scheme == 'sqlite':
        return SqliteInterface(path, print_sql=print_sql)
    elif dsn.scheme == 'postgres':
        return PostgresInterface(dsn.host, dsn.port, path, dsn.user, dsn.password, print_sql=print_sql)
    elif dsn.scheme == 'mysql':
        return MysqlInterface(dsn.host, dsn.port, path, dsn.user, dsn.password, print_sql=print_sql)
    elif dsn.scheme == 'gitqlite':
        return GitqliteInterface(path, print_sql=print_sql)

    raise NotImplementedError(f"Scheme {dsn.scheme} currently not supported")



def assert_type(t, type_, state, ast, op, msg="%s expected an object of type %s, instead got '%s'"):
    assert isinstance(t, Type), t
    assert isinstance(type_, Type)
    if not (t <= type_):
        if type_.typename == 'union':
            type_str = ' or '.join("'%s'" % elem for elem in type_.elems)
        else:
            type_str = "'%s'" % type_
        raise pql_TypeError.make(state, ast, msg % (op, type_str, t))

def exclude_fields(state, table, fields):
    proj = ast.Projection(None, table, [ast.NamedField(None, None, ast.Ellipsis(None, exclude=fields ))])
    return evaluate(state, proj)

def call_pql_func(state, name, args):
    expr = ast.FuncCall(None, ast.Name(None, name), args)
    return evaluate(state, expr)



new_value_instance = objects.new_value_instance
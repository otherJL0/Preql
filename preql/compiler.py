import operator

from .utils import safezip, listgen, SafeDict, find_duplicate, dataclass
from .exceptions import pql_TypeError, PreqlError, pql_SyntaxError, pql_CompileError
from . import exceptions as exc

from . import settings
from . import pql_objects as objects
from . import pql_ast as ast
from . import sql
from .interp_common import dy, State, assert_type, new_value_instance, evaluate, call_pql_func
from .pql_types import T, join_names, pql_dp, flatten_type, Type, Object, combined_dp, table_to_struct

@dataclass
class Table(Object):
    type: Type
    name: str


def _process_fields(state: State, fields):
    processed_fields = []
    for f in fields:

        suggested_name = str(f.name) if f.name else guess_field_name(f.value)
        name = suggested_name.rsplit('.', 1)[-1]    # Use the last attribute as name

        v = evaluate(state, f.value)

        if isinstance(v, ast.ResolveParametersString):
            raise exc.InsufficientAccessLevel()

        if not isinstance(v, objects.AbsInstance):
            raise pql_TypeError.make(state, None, f"Projection field is not an instance. Instead it is: {v}")

        if (v.type <= T.aggregate):
            v = v.primary_key()
            v = objects.make_instance(sql.MakeArray(v.type, v.code), v.type, [v])

        processed_fields.append( [name, v] )

    return processed_fields


@listgen
def _expand_ellipsis(state, table, fields):
    direct_names = {f.value.name for f in fields if isinstance(f.value, ast.Name)}

    for f in fields:
        assert isinstance(f, ast.NamedField)

        if isinstance(f.value, ast.Ellipsis):
            if f.name:
                raise pql_SyntaxError.make(state, f, "Cannot use a name for ellipsis (inlining operation doesn't accept a name)")
            else:
                exclude = direct_names | set(f.value.exclude)
                for name in table_to_struct(table.type).elems:
                    assert isinstance(name, str)
                    if name not in exclude:
                        yield ast.NamedField(f.text_ref, name, ast.Name(None, name))
        else:
            yield f


@dy
def compile_to_inst(state: State, x):
    return x
@dy
def compile_to_inst(state: State, node: ast.Ast):
    return node

@dy
def compile_to_inst(state: State, proj: ast.Projection):
    table = evaluate(state, proj.table)
    if table is objects.EmptyList:
        return table   # Empty list projection is always an empty list.

    if isinstance(table, ast.ResolveParametersString):
        raise exc.InsufficientAccessLevel()

    assert isinstance(table, objects.AbsInstance), table

    if not (table.type <= T.union[T.collection, T.struct]):
        raise pql_TypeError.make(state, proj, f"Cannot project objects of type {table.type}")

    fields = _expand_ellipsis(state, table, proj.fields)

    # Test duplicates in field names. If an automatic name is used, collision should be impossible
    dup = find_duplicate([f for f in list(proj.fields) + list(proj.agg_fields) if f.name], key=lambda f: f.name)
    if dup:
        raise pql_TypeError.make(state, dup, f"Field '{dup.name}' was already used in this projection")

    attrs = table.all_attrs()

    with state.use_scope(attrs):
        fields = _process_fields(state, fields)

    for name, f in fields:
        if not (f.type <= T.union[T.primitive, T.struct, T.null]):
            raise exc.pql_TypeError.make(state, proj, f"Cannot project values of type: {f.type}")

    if isinstance(table, objects.StructInstance):
        t = T.struct(**{n:c.type for n,c in fields})
        return objects.StructInstance(t, dict(fields))

    agg_fields = []
    if proj.agg_fields:
        with state.use_scope({n:objects.aggregate(c) for n,c in attrs.items()}):
            agg_fields = _process_fields(state, proj.agg_fields)

    all_fields = fields + agg_fields

    # Make new type
    elems = {}

    codename = state.unique_name('proj')
    for name_, inst in all_fields:
        assert isinstance(inst, objects.AbsInstance)

        # TODO what happens if automatic name preceeds and collides with user-given name?
        name = name_
        i = 1
        while name in elems:
            name = name_ + str(i)
            i += 1
        elems[name] = inst.type

    # TODO inherit primary key? indexes?
    new_table_type = T.table(**elems).set_options(temporary=True)

    # Make code
    flat_codes = [code
                    for _, inst in all_fields
                    for code in inst.flatten_code()]

    # TODO if nn != on
    sql_fields = [
        sql.ColumnAlias.make(code, nn)
        for code, (nn, _nt) in safezip(flat_codes, flatten_type(new_table_type))
    ]

    # Make Instance
    new_table = objects.TableInstance.make(sql.null, new_table_type, [table] + [inst for _, inst in all_fields])

    groupby = []
    limit = None
    if proj.groupby:
        if fields:
            # groupby = [new_table.get_column(n).primary_key().code for n, rc in fields]
            groupby = [sql.Primitive(T.int, str(i+1)) for i in range(len(fields))]
        else:
            limit = sql.Primitive(T.int, '1')
            # Alternatively we could
            #   groupby = [sql.null]
            # But postgres doesn't support it

    code = sql.Select(new_table_type, table.code, sql_fields, group_by=groupby, limit=limit)

    # Make Instance
    return new_table.replace(code=code)

@dy
def compile_to_inst(state: State, order: ast.Order):
    table = evaluate(state, order.table)
    assert_type(table.type, T.table, state, order, "'order'")

    with state.use_scope(table.all_attrs()):
        fields = evaluate(state, order.fields)

    code = sql.table_order(table, [c.code for c in fields])

    return objects.TableInstance.make(code, table.type, [table] + fields)

@dy
def compile_to_inst(state: State, expr: ast.DescOrder):
    obj = evaluate(state, expr.value)
    return obj.replace(code=sql.Desc(obj.code))



@dy
def compile_to_inst(state: State, lst: list):
    return [evaluate(state, e) for e in lst]


@dy
def compile_to_inst(state: State, like: ast.Like):
    s = evaluate(state, like.str)
    p = evaluate(state, like.pattern)
    if s.type != T.string:
        raise pql_TypeError.make(state, like.str, f"Like (~) operator expects two strings")
    if p.type != T.string:
        raise pql_TypeError.make(state, like.pattern, f"Like (~) operator expects two strings")

    code = sql.Like(s.code, p.code)
    return objects.Instance.make(code, T.bool, [s, p])



## Contains
@pql_dp
def _contains(state, op, a: T.string, b: T.string):
    f = {
        'in': 'str_contains',
        '!in': 'str_notcontains',
    }[op]
    return call_pql_func(state, f, [a, b])

@pql_dp
def _contains(state, op, a: T.primitive, b: T.collection):
    from .pql_functions import _cast
    b_list = _cast(state, b.type, T.list, b)
    if not (a.type <= b_list.type.elem):
        raise pql_TypeError.make(state, op, f"Error in contains: Mismatch between {a.type} and {b.type}")

    if op == '!in':
        op = 'not in'
    code = sql.Contains(op, [a.code, b_list.code])
    return objects.Instance.make(code, T.bool, [a, b_list])

@pql_dp
def _contains(state, op, a: T.any, b: T.any):
    raise pql_TypeError.make(state, op, f"Contains not implemented for {a.type} and {b.type}")


## Compare
@pql_dp
def _compare(state, op, a: T.any, b: T.any):
    raise pql_TypeError.make(state, op, f"Compare not implemented for {a.type} and {b.type}")

@pql_dp
def _compare(state, op, a: T.null, b: T.null):
    return objects.new_value_instance(True)

@pql_dp
def _compare(state, op, a: T.type, b: T.null):
    assert not a.type.nullable
    return objects.new_value_instance(False)
@pql_dp
def _compare(state, op, a: T.null, b: T.type):
    return _compare(state, op, b, a)

@pql_dp
def _compare(state, op, a: T.null, b: T.object):
    # TODO Enable this type-based optimization:
    # if not b.type.nullable:
    #     return objects.new_value_instance(False)
    if b.type <= T.struct:
        b = b.primary_key()
    code = sql.Compare(op, [a.code, b.code])
    return objects.Instance.make(code, T.bool, [a, b])
@pql_dp
def _compare(state, op, a: T.object, b: T.null):
    return _compare(state, op, b, a)

@pql_dp
def _compare(state, op, a: T.primitive, b: T.primitive):
    if settings.optimize and isinstance(a, objects.ValueInstance) and isinstance(b, objects.ValueInstance):
                f = {
                    '=': operator.eq,
                    '!=': operator.ne,
                    '<>': operator.ne,
                    '>': operator.gt,
                    '<': operator.lt,
                    '>=': operator.ge,
                    '<=': operator.le,
                }[op]
                return objects.new_value_instance(f(a.local_value, b.local_value))

    # TODO regular equality for primitives? (not 'is')
    code = sql.Compare(op, [a.code, b.code])
    return objects.Instance.make(code, T.bool, [a, b])

@pql_dp
def _compare(state, arith, a: T.aggregate, b: T.aggregate):
    res = _compare(state, arith, a.elem, b.elem)
    return objects.aggregate(res)

@pql_dp
def _compare(state, arith, a: T.aggregate, b: T.int):
    res = _compare(state, arith, a.elem, b)
    return objects.aggregate(res)

@pql_dp
def _compare(state, arith, a: T.int, b: T.aggregate):
    return _compare(state, arith, b, a)

@pql_dp
def _compare(state, op, a: T.type, b: T.type):
    return objects.new_value_instance(a == b)

@pql_dp
def _compare(state, op, a: T.number, b: T.row):
    return _compare(state, op, a, b.primary_key())

@pql_dp
def _compare(state, op, a: T.row, b: T.number):
    return _compare(state, op, b, a)

@pql_dp
def _compare(state, op, a: T.row, b: T.row):
    return _compare(state, op, a.primary_key(), b.primary_key())



@dy
def compile_to_inst(state: State, cmp: ast.Compare):
    insts = evaluate(state, cmp.args)

    if cmp.op == 'in' or cmp.op == '!in':
        return _contains(state, cmp.op, insts[0], insts[1])
    else:
        op = {
            '==': '=',
            '<>': '!=',
        }.get(cmp.op, cmp.op)
        return _compare(state, op, insts[0], insts[1])

@dy
def compile_to_inst(state: State, neg: ast.Neg):
    expr = evaluate(state, neg.expr)
    assert_type(expr.type, T.number, state, neg, "Negation")

    return objects.Instance.make(sql.Neg(expr.code), expr.type, [expr])


@dy
def compile_to_inst(state: State, arith: ast.Arith):
    args = evaluate(state, arith.args)

    return _compile_arith(state, arith, *args)

@pql_dp
def _compile_arith(state, arith, a: T.any, b: T.any):
    raise pql_TypeError.make(state, arith.op, f"Operator '{arith.op}' not implemented for {a.type} and {b.type}")

@pql_dp
def _compile_arith(state, arith, a: T.collection, b: T.collection):
    # TODO validate types
    ops = {
        "+": 'concat',
        "&": 'intersect',
        "|": 'union',
        "-": 'subtract',
    }
    # TODO compile preql funccall?
    try:
        op = ops[arith.op]
    except KeyError:
        raise pql_TypeError.make(state, arith.op, f"Operation '{arith.op}' not supported for tables")

    return state.get_var(op).func(state, a, b)


@pql_dp
def _compile_arith(state, arith, a: T.aggregate, b: T.aggregate):
    res = _compile_arith(state, arith, a.elem, b.elem)
    return objects.aggregate(res)

@pql_dp
def _compile_arith(state, arith, a: T.aggregate, b: T.primitive):
    res = _compile_arith(state, arith, a.elem, b)
    return objects.aggregate(res)
@pql_dp
def _compile_arith(state, arith, a: T.primitive, b: T.aggregate):
    return _compile_arith(state, arith, b, a)

@pql_dp
def _compile_arith(state, arith, a: T.string, b: T.int):
    if arith.op != '*':
        raise pql_TypeError.make(state, arith.op, f"Operator '{arith.op}' not supported between string and integer.")
    return call_pql_func(state, "repeat", [a, b])

@pql_dp
def _compile_arith(state, arith, a: T.number, b: T.number):
    if arith.op == '/' or a.type <= T.float or b.type <= T.float:
        res_type = T.float
    else:
        res_type = T.int

    if settings.optimize and isinstance(a, objects.ValueInstance) and isinstance(b, objects.ValueInstance):
            # Local folding for better performance (optional, for better performance)
        f = {
            '+': operator.add,
            '-': operator.sub,
            '*': operator.mul,
            '/': operator.truediv,
            '/~': operator.floordiv,
        }[arith.op]
        return new_value_instance(f(a.local_value, b.local_value), res_type)

    code = sql.arith(res_type, arith.op, [a.code, b.code])
    return objects.make_instance(code, res_type, [a, b])

@pql_dp
def _compile_arith(state, arith, a: T.string, b: T.string):
    if arith.op != '+':
        raise exc.pql_TypeError.make(state, arith.op, f"Operator '{arith.op}' not supported for strings.")

    if settings.optimize and isinstance(a, objects.ValueInstance) and isinstance(b, objects.ValueInstance):
        # Local folding for better performance (optional, for better performance)
        return new_value_instance(a.local_value + b.local_value, T.string)

    code = sql.arith(T.string, arith.op, [a.code, b.code])
    return objects.make_instance(code, T.string, [a, b])



@dy
def compile_to_inst(state: State, x: ast.Ellipsis):
    raise pql_SyntaxError.make(state, x, "Ellipsis not allowed here")


@dy
def compile_to_inst(state: State, c: ast.Const):
    if c.type == T.null:
        assert c.value is None
        return objects.null
    return new_value_instance(c.value, c.type)

@dy
def compile_to_inst(state: State, d: ast.Dict_):
    assert isinstance(d.elems, dict), d

    # TODO handle duplicate key names
    elems = {k or guess_field_name(v): evaluate(state, v) for k, v in d.elems.items()}
    t = T.table(**{k: v.type for k,v in elems.items()})
    return objects.RowInstance(T.row[t], elems)

@dy
def compile_to_inst(state: State, lst: ast.List_):
    # TODO generate (a,b,c) syntax for IN operations, with its own type
    # sql = "(" * join([e.code.text for e in objs], ",") * ")"
    # type = length(objs)>0 ? objs[1].type : nothing
    # return Instance(Sql(sql), ArrayType(type, false))
    # Or just evaluate?

    if not lst.elems and tuple(lst.type.elems) == (T.any,):
        # XXX a little awkward
        return objects.EmptyList

    elems = evaluate(state, lst.elems)

    type_set = {e.type for e in elems}
    if len(type_set) > 1:
        raise pql_TypeError.make(state, lst, "Cannot create a list of mixed types: (%s)" % ', '.join(repr(t) for t in type_set))
    elif type_set:
        elem_type ,= type_set
    else:
        elem_type = lst.type.elem

    if not (elem_type <= T.primitive):
        raise pql_TypeError.make(state, lst, "Cannot create lists of type %s" % elem_type)

    # XXX should work with a better type system where isa(int, any) == true
    # assert isinstance(elem_type, type(lst.type.elemtype)), (elem_type, lst.type.elemtype)

    # code = sql.TableArith(table_type, 'UNION ALL', [ sql.SelectValue(e.type, e.code) for e in elems ])
    list_type = T.list[elem_type]
    name = state.unique_name("list_")
    table_code, subq = sql.create_list(list_type, name, [e.code for e in elems])

    inst = objects.ListInstance.make(table_code, list_type, elems)
    inst.subqueries[name] = subq
    return inst


@dy
def compile_to_inst(state: State, s: ast.Slice):
    obj = evaluate(state, s.table)

    assert_type(obj.type, T.union[T.string, T.collection], state, s, "Slice")

    instances = [obj]
    if s.range.start:
        start = evaluate(state, s.range.start)
        instances += [start]
    else:
        start = new_value_instance(0)

    if s.range.stop:
        stop = evaluate(state, s.range.stop)
        instances += [stop]
    else:
        stop = None

    if obj.type <= T.string:
        code = sql.StringSlice(obj.code, start.code, stop and stop.code)
    else:
        code = sql.table_slice(obj, start.code, stop and stop.code)

    return objects.make_instance(code, obj.type, instances)

@dy
def compile_to_inst(state: State, sel: ast.Selection):
    table = evaluate(state, sel.table)
    if isinstance(table, Type):
        return _apply_type_generics(state, table, sel.conds)

    if not isinstance(table, objects.Instance):
        return sel.replace(table=table)

    assert_type(table.type, T.collection, state, sel, "Selection")

    with state.use_scope(table.all_attrs()):
        conds = evaluate(state, sel.conds)

    for i, c in enumerate(conds):
        if not (c.type <= T.bool):
            raise exc.pql_TypeError.make(state, sel.conds[i], f"Selection expected boolean, got {c.type}")

    code = sql.table_selection(table, [c.code for c in conds])

    return objects.TableInstance.make(code, table.type, [table] + conds)

@dy
def compile_to_inst(state: State, param: ast.Parameter):
    if state.access_level == state.AccessLevels.COMPILE:
        return objects.make_instance(sql.Parameter(param.type, param.name), param.type, [])
    else:
        return state.get_var(param.name)

@dy
def compile_to_inst(state: State, attr: ast.Attr):
    inst = evaluate(state, attr.expr)
    try:
        return evaluate(state, inst.get_attr(attr.name))
    except exc.pql_AttributeError as e:
        raise exc.pql_AttributeError.make(state, attr, e.message) from e



def _apply_type_generics(state, gen_type, type_names):
    type_objs = evaluate(state, type_names)
    if not type_objs:
        raise pql_TypeError.make(state, None, f"Generics expression expected a type, got nothing.")
    for o in type_objs:
        if not isinstance(o, Type):
            raise pql_TypeError.make(state, None, f"Generics expression expected a type, got '{o}'.")

    if len(type_objs) > 1:
        raise pql_TypeError.make(state, None, "Union types not yet supported!")
    else:
        t ,= type_objs

    try:
        return gen_type[t]
    except TypeError:
        raise pql_TypeError.make(state, None, f"Type {t} isn't a container!")



@dy
def guess_field_name(f):
    return '_'
@dy
def guess_field_name(f: ast.Attr):
    return guess_field_name(f.expr) + "." + f.name
@dy
def guess_field_name(f: ast.Name):
    return str(f.name)
@dy
def guess_field_name(f: ast.Projection):
    return guess_field_name(f.table)
@dy
def guess_field_name(f: ast.FuncCall):
    return guess_field_name(f.func)


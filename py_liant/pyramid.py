from pyramid.request import Request
from pyramid.httpexceptions import (
    HTTPNotFound, HTTPServerError, HTTPConflict, HTTPOk)
from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.orm.exc import NoResultFound, StaleDataError
from sqlalchemy.orm.base import NOT_EXTENSION
from sqlalchemy.orm import ColumnProperty, RelationshipProperty
from sqlalchemy import String, orm, and_
from sqlalchemy.inspection import inspect
import transaction


from .json_encoder import JSONEncoder
from .json_decoder import JSONDecoder
from .monkeypatch import coerce_value
from .hints_parser import hints_parser


# Returns a renderer for pyramid; base_type (SQLAlchemy declarative base) is
# needed to detect sqlalchemy object instances
# Sample usage:
#     config.add_renderer('json', pyramid_json_renderer_factory(Base))

def pyramid_json_renderer_factory(base_type=None, stream_method=2,
                                  separators=(',', ':')):
    def _json_renderer(info):
        def _render(value, system):
            request = system.get('request')
            if request is not None:
                # this is optimal as we will stream our JSON from the iterator
                response = request.response
                response.content_type = 'application/json'
                response.charset = 'utf8'

                json_encoder = JSONEncoder(request, base_type=base_type,
                                           separators=separators,
                                           check_circular=False)

                # solution 1: write to stream from renderer
                if stream_method == 0:
                    for chunk in json_encoder.iterencode(value):
                        response.write(chunk)
                    return None

                # solution 2: provide iterable to wsgi layer
                else:
                    def _iterencode(val):
                        for chunk in json_encoder.iterencode(val):
                            yield chunk.encode()

                    response.app_iter = _iterencode(value)
                    return None
            else:
                # fallback for direct calls, useful for rapid

                json_encoder = JSONEncoder(
                    None, base_type=base_type, separators=separators)
                return json_encoder.encode(value)
        return _render
    return _json_renderer


# Sample usage:
#     config.add_request_method(pyramid_json_decoder, 'json', reify=True)
def pyramid_json_decoder(request):
    return JSONDecoder(encoding=request.charset).decode(request.body)


# exception to be thrown when a
class VersionCheckError(RuntimeError):
    pass


# Extend this and decorate accordingly; methods list, get, insert, update,
# delete should be decorated with pyramid @view_config
class CRUDView(object):
    filters = dict()
    accept_order = dict()
    # json_hints = dict()
    # update_hints = dict()
    context = None
    target_type = object
    target_name = "object"
    request = None
    update_lock = False
    # this means accept_order will be rewritten
    use_subquery_after_filter = False

    def __init__(self, request):
        """:type request: Request"""
        self.request = request
        self.context = request.context
        # self.context._json_hints = self.json_hints
        assert(isinstance(self.request, Request))

    @property
    def identity_filter(self):
        return False

    def sanitize_input(self):
        return self.request.json[self.target_name]

    @property
    def context_filter(self):
        return True

    def get_by_id(self, update_lock=False):
        try:
            query = self.get_identity_base()
            if update_lock:
                # (partially a) SQLAlchemy bug: Postgres doesn't allow
                # qualified table names in FOR UPDATE OF statements, so
                # whenever targeted locks are required an alias is necessary,
                # for us at least since we use schemas for all tables
                if isinstance(self.target_type, AliasedClass):
                    query = query.with_for_update(of=self.target_type)
                else:
                    query = query.with_for_update()
            return query.filter(self.context_filter, self.identity_filter) \
                .one()
        except NoResultFound:
            raise HTTPNotFound()

    def get_query_filters(self, exclude=None):
        tmp = [self.filters[key](self.request.GET[key]) for key in self.filters
               if key in self.request.GET and
               (exclude is None or key not in exclude)]
        return [i for i in tmp if not isinstance(i, (list, tuple))
                and i is not None] + \
               [i for j in tmp if isinstance(j, (list, tuple))
                and j is not None for i in j if i is not None] + \
            self.runtime_filters()

    def runtime_filters(self):
        return []

    @property
    def query_filters(self):
        return self.get_query_filters()

    @property
    def order_clauses(self):
        if 'order' not in self.request.GET or self.request.GET['order'] == "":
            return [False]
        orders = [(item[:-5], True) if item.endswith(' desc')
                  else(item, False)
                  for item in self.request.GET['order'].split(",")]
        if any([order[0] not in self.accept_order for order in orders]):
            invalid_order = [order[0] for order in orders
                             if order[0] not in self.accept_order]
            raise HTTPServerError(
                f'not implemented, order by {", ".join(invalid_order)}'
            )

        selected = [(self.accept_order[order[0]], order[1])
                    for order in orders]
        for item in selected:
            if isinstance(item[0], list):
                for elem in item[0]:
                    yield elem.desc() if item[1] else elem
            else:
                yield item[0].desc() if item[1] else item[0]

    @property
    def pager_slice(self):
        if 'pageSize' not in self.request.GET:
            return False
        page_size = int(self.request.GET['pageSize'])
        if page_size <= 0:
            return False
        page = int(self.request.GET['page']
                   ) if 'page' in self.request.GET else 0
        return slice(page * page_size, page * page_size + page_size)

    def get_base_query(self):
        return self.request.dbsession.query(self.target_type)

    def get_identity_base(self):
        return self.get_base_query()

    def get_list_base(self):
        return self.get_base_query()

    def get_search_results(self, query=None):
        if query is None:
            query = self.get_list_base().filter(self.context_filter)
        query = query.filter(*self.query_filters)
        pager = self.pager_slice
        count = query.count()

        if self.use_subquery_after_filter:
            query = query.subquery()
            self.accept_order = dict(query.c)
            query = self.request.dbsession.query(
                *query.c).order_by(*self.order_clauses)
        else:
            query = query.order_by(*self.order_clauses)
        return query[pager] if pager else query.all(), count

    def get(self):
        return {self.target_name: self.get_by_id()}

    def list(self):
        items, count = self.get_search_results()
        return dict(items=items, total=count)

    def apply_changes(self, obj, data, for_update=True):
        obj.apply_changes(data)

    def update(self):
        try:
            with transaction.manager, self.request.dbsession.no_autoflush:
                old = self.get_by_id(update_lock=self.update_lock)
                values = self.sanitize_input()
                try:
                    self.apply_changes(old, values, True)
                except VersionCheckError as ex:
                    raise HTTPConflict(str(ex))
        except StaleDataError as ex:
            raise HTTPConflict(str(ex))
        return self.request.dbsession.merge(old)

    def insert(self):
        obj = self.target_type()
        values = self.sanitize_input()
        with transaction.manager:
            self.request.dbsession.add(obj)
            self.apply_changes(obj, values, False)
        obj = self.request.dbsession.merge(obj)
        return {self.target_name: obj}

    def delete(self):
        with transaction.manager:
            old = self.get_by_id()
            self.request.dbsession.delete(old)
        return HTTPOk()

    @classmethod
    def auto_fields(cls, target):
        return [item for item in inspect(target).all_orm_descriptors.values()
                if item.is_attribute and item.extension_type == NOT_EXTENSION
                and isinstance(item.property, ColumnProperty)]

    @classmethod
    def auto_filters(cls, target=None, prefix=None):
        if target is None:
            target = cls.target_type

        def coerce_func(x, attr):
            return coerce_value(target, attr.property.columns[0], x, False)

        ret = dict()

        for item in cls.auto_fields(target):
            key = prefix + item.key if prefix is not None else item.key
            ret[key] = lambda x, attr=item: attr == coerce_func(x, attr)
            if isinstance(item.property.columns[0].type, String):
                ret[f'{key}_like'] = \
                    lambda x, attr=item: attr.ilike('%' + x + '%')
            ret[f'{key}_gt'] = \
                lambda x, attr=item: attr > coerce_func(x, attr)
            ret[f'{key}_ge'] = \
                lambda x, attr=item: attr >= coerce_func(x, attr)
            ret[f'{key}_lt'] = \
                lambda x, attr=item: attr < coerce_func(x, attr)
            ret[f'{key}_le'] = \
                lambda x, attr=item: attr <= coerce_func(x, attr)
        return ret

    @classmethod
    def auto_order(cls, target=None, prefix=None):
        if target is None:
            target = cls.target_type
        ret = dict()
        for attr in cls.auto_fields(target):
            key = prefix + attr.key if prefix is not None else attr.key
            ret[key] = attr
        return ret


class ConvertMatchdictPredicate:
    def __init__(self, args, config):
        assert len(args) >= 1 and (type(args[0]) == tuple or
                                   callable(args[0]))
        self.args = args

    def text(self):
        return f'convert_matchdict={self.args!r}'

    phash = text

    def __call__(self, context, request):
        args = self.args if type(self.args[0]) == tuple else(self.args,)
        match = request.matchdict
        for argList in args:
            argType = argList[0]
            assert callable(argType)
            for arg in (argList[1:]):
                if arg in match:
                    try:
                        match[arg] = argType(match[arg])
                    except (TypeError, ValueError):
                        return False
        return True


class CatchallPredicate:
    def __init__(self, targets, config):
        self.targets = {
            key: (cls, CatchallPredicate.get_hints(hint, cls))
            if type(hint) is str else (cls, hint)
            for key, (cls, hint) in targets.items()
        }

    def text(self):
        return f'catchall={self.targets!r}'

    phash = text

    def __call__(self, context, request):
        match = request.matchdict

        # convert target to type
        if 'target' not in match:
            return False

        target = match['target']
        if target not in self.targets:
            return False

        match['target'] = self.targets[target][0]

        insp = inspect(match['target'])

        # convert pkey
        if 'pkey' in match:
            pkey = match['pkey'].split(',')
            if len(pkey) != len(insp.primary_key):
                return False

            try:
                pkey = [col == coerce_value(target, col, val)
                        for col, val in zip(insp.primary_key, pkey)]
            except ValueError:
                return False

            match['pkey'] = pkey

        # decode hints
        if 'hints' in match:
            match['hints'] = self.get_hints(match['hints'], match['target'])
        else:
            match['hints'] = self.targets[target][1]

        return True

    @staticmethod
    def get_hints(value, cls=None, base=orm):
        # hints structure: [atom(,atom)+]
        # atom is one of:
        #  +field_name: undefer field (include)
        #  -field_name: defer field (exclude)
        #  TODO if field_name is "ALL", defer or undefer all fields
        #   (DOES NOT AFFECT COLLECTIONS, see below)
        #  *collection(-x)?(\(hints\))?:
        #   eager load collection (-x marks subquery laod preference);
        #   hints in parantheses recursively apply to elements of
        #   the collection
        if type(value) is str:
            value = hints_parser.parseString(value, True)
        hints = []
        insp = inspect(cls)
        # keep track of joined relationship in current batch
        # joined_relationship = False
        for item in value:
            name = item['name']
            op = item['op']
            prop = insp.get_property(name)
            hint = None
            if isinstance(prop, ColumnProperty):
                if op == '+':
                    hint = base.undefer(name)
                elif op == '-':
                    hint = base.defer(name)
                else:
                    raise AssertionError("invalid op")
            elif isinstance(prop, RelationshipProperty):
                # if joined_relationship:
                #     hint = base.subqueryload(name)
                # else:
                #     hint = base.joinedload(name)
                #     joined_relationship = True
                hint = base.selectinload(name)

                if 'children' in item:
                    remote_table = {col.table for col in prop.remote_side}
                    assert len(remote_table) == 1
                    remote_table = remote_table.pop()
                    remote_class = next(_ for _ in cls._decl_class_registry
                                        .values() if isinstance(_, type)
                                        and hasattr(_, '__table__')
                                        and _.__table__ is remote_table)
                    # attach joined class hints
                    CatchallPredicate.get_hints(item['children'], remote_class,
                                                hint)
            hints.append(hint)
        return hints


# base class to decorate with a CatchAllPredicate
class CatchallView(CRUDView):
    context = None
    request = None
    hints = []

    def __init__(self, request):
        super().__init__(request)

        self.target_type = self.request.matchdict['target']
        self.target_name = self.target_type.__tablename__

        self.filters = self.auto_filters(target=self.target_type)
        self.accept_order = self.auto_order(target=self.target_type)

        if 'pkey' in self.request.matchdict:
            self.key = self.request.matchdict['pkey']
        if 'hints' in self.request.matchdict:
            self.hints = self.request.matchdict['hints']

    @property
    def identity_filter(self):
        return and_(*self.key)

    def get_identity_base(self):
        query = self.get_base_query()
        if self.hints and len(self.hints):
            query = query.options(*self.hints)
        return query

    def get_list_base(self):
        query = self.get_base_query()
        if self.hints and len(self.hints):
            query = query.options(*self.hints)
        return query

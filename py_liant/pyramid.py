from pyparsing import ParseException
from pyramid.request import Request
from pyramid.httpexceptions import (
    HTTPNotFound, HTTPServerError, HTTPConflict, HTTPOk)
from sqlalchemy.orm.util import AliasedClass
from sqlalchemy.orm.exc import (NoResultFound, StaleDataError)
from sqlalchemy.exc import InvalidRequestError
from sqlalchemy.orm.base import NOT_EXTENSION
from sqlalchemy.orm import ColumnProperty, RelationshipProperty, Mapper
from sqlalchemy import String, orm, and_
from sqlalchemy.inspection import inspect
from pyramid.settings import asbool
import transaction


from .json_encoder import JSONEncoder
from .json_decoder import JSONDecoder
from .monkeypatch import coerce_value, patch_sqlalchemy_base_class
from .parser import route_parser
from .interfaces import JsonGuardProvider


# Returns a renderer for pyramid; base_type (SQLAlchemy declarative base) is
# needed to detect sqlalchemy object instances
# Sample usage:
#     config.add_renderer('json', pyramid_json_renderer_factory(Base))

def pyramid_json_renderer_factory(base_type=None, wsgi_iter=False,
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
                if not wsgi_iter:
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
                # fallback for direct calls?
                json_encoder = JSONEncoder(base_type=base_type,
                                           separators=separators)
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

    def get_one_from_query(self, query):
        return query.filter(self.context_filter, self.identity_filter).one()

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
            return self.get_one_from_query(query)
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
            return None
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
                *query.c)

        order_clauses = self.order_clauses
        if order_clauses is not None:
            # reset and apply order_by
            query = query.order_by(None).order_by(*order_clauses)
        return query[pager] if pager else query.all(), count

    def get(self):
        return {self.target_name: self.get_by_id()}

    def list(self):
        items, count = self.get_search_results()
        return dict(items=items, total=count)

    def apply_changes(self, obj, data, for_update=True):
        obj.apply_changes(data, context=self.context, for_update=for_update)

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
            ret[f'{key}_isnull'] = \
                lambda x, attr=item: attr.is_(None) if asbool(x) else \
                attr.isnot(None)
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


def _get_assert_one(q):
    return q.one()


def _get_by_pkey(pkey):
    def _impl(q):
        return q.get(pkey)
    return _impl


# TODO: reverse order for negative indexes (?)
def _get_by_index(index):
    def _impl(q):
        ret = q[index:index + 1]
        if len(ret) == 0:
            raise NoResultFound()
        return ret[0]
    return _impl


class CatchallPredicate:
    def __init__(self, targets, config):
        def _adapt(obj):
            if type(obj) is not tuple:
                return (obj,)
            return obj

        self.targets = {
            key: _adapt(obj) for key, obj in targets.items()
        }

    def text(self):
        return f'catchall={self.targets!r}'

    phash = text

    def __call__(self, context, request):
        match = request.matchdict

        # convert target to type
        if 'catchall' not in match:
            return False

        try:
            route = route_parser.parseString(match['catchall'], True)
        except ParseException:
            return False

        if route['verb'] not in self.targets:
            return False

        target = self.targets[route['verb']][0]
        getter = None

        insp = inspect(target)
        if 'cast' in route:
            if insp.polymorphic_on is None:
                return False
            try:
                value = coerce_value(target, insp.polymorphic_on,
                                     route['cast'])
            except ValueError:
                return False
            if value not in insp.polymorphic_map:
                return False
            insp = insp.polymorphic_map[value]
            target = insp.class_

        query = request.dbsession.query(target)

        # convert pkey
        if 'pkey' in route:
            pkey = route['pkey']
            if len(pkey) != len(insp.primary_key):
                return False
            try:
                pkey = tuple(coerce_value(target, col, val)
                             for col, val in zip(insp.primary_key, pkey))
            except ValueError:
                return False
            getter = _get_by_pkey(pkey)

        if 'drilldown' in route:
            # cannot drilldown property if result is list
            if getter is None:
                return False

            drilldown = route['drilldown']
            try:
                prop = insp.get_property(drilldown)
            except InvalidRequestError:
                return False

            if isinstance(context, JsonGuardProvider):
                if not context.guardDrilldown(prop.class_attribute):
                    return False

            if not isinstance(prop, RelationshipProperty):
                return False

            # target switch in drilldown
            target = prop.entity.class_

            if prop.lazy == 'dynamic':
                # special drilldown love for dynamic props
                query = getattr(getter(query), prop.key)
                # getter no longer useful
                getter = None
            else:
                # manually construct drilldown query for non-dynamic props
                pkey_filter = (col == val for col, val in
                               zip(insp.primary_key, pkey))
                query = request.dbsession.query(prop.entity) \
                    .select_from(prop.parent) \
                    .join(prop.class_attribute) \
                    .filter(and_(*pkey_filter))
                getter = None

            if not prop.uselist:
                # target is single item fk
                getter = _get_assert_one

        if 'slice' in route:
            # cannot slice if pkey or single item drilldown encountered
            if getter is not None:
                return False

            slicer = route['slice']
            if 'index' in slicer:
                getter = _get_by_index(int(slicer['index']))
            else:
                match['slicer'] = slicer

        # decode hints
        if 'hints' in route:
            try:
                hints = self.get_hints(route['hints'], target,
                                       context=context)
                query = query.options(*hints)
            except AssertionError:
                return False

        match['query'] = query
        match['getter'] = getter

        return True

    @staticmethod
    def get_hints(value, cls, base=orm, context=None):
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

        if type(cls) is Mapper:
            # caller passed us a mapper (drilldown)
            insp = cls
            cls = insp.class_
        else:
            insp = inspect(cls)

        hints = {}
        ret = []
        for item in value:
            op = item['op']
            if op == '!':
                if insp.polymorphic_on is None:
                    raise AssertionError("invalid cast")
                try:
                    _type = coerce_value(cls, insp.polymorphic_on,
                                         item['type'])
                except ValueError:
                    raise AssertionError("invalid cast")
                if _type not in insp.polymorphic_map:
                    raise AssertionError("invalid cast")

                ret.extend(
                    CatchallPredicate.get_hints(
                        item['children'],
                        insp.polymorphic_map[_type],
                        base, context=context
                    ))
                continue

            name = item['name']
            prop = insp.get_property(name)
            hints[prop.class_attribute] = (op, item.get('children'))
        if isinstance(context, JsonGuardProvider):
            context.guardHints(cls, hints)
        for attr, (op, children) in hints.items():
            prop = attr.prop
            hint = None
            if isinstance(prop, ColumnProperty):
                if op == '+':
                    hint = base.undefer(attr)
                elif op == '-':
                    hint = base.defer(attr)
                else:
                    raise AssertionError("invalid op")
            elif isinstance(prop, RelationshipProperty):
                hint = base.selectinload(attr)

                if children:
                    CatchallPredicate.get_hints(children, attr.entity,
                                                hint, context=context)
            ret.append(hint)
        return ret


# base class to decorate with a CatchAllPredicate
class CatchallView(CRUDView):
    context = None
    request = None
    query = None
    getter = None
    slicer = None

    def __init__(self, request):
        super().__init__(request)
        self.query = self.request.matchdict['query']
        self.getter = self.request.matchdict.get('getter')

        self.target_type = self.query.column_descriptions[0]['type']
        self.target_name = self.target_type.__table__.name
        self.slicer = self.request.matchdict.get('slicer')

        self.filters = self.auto_filters(target=self.target_type)
        self.accept_order = self.auto_order(target=self.target_type)

    def get_base_query(self):
        return self.query

    def get_one_from_query(self, query):
        if self.getter is None:
            raise HTTPNotFound()
        query = query.filter(*self.query_filters)
        order_clauses = self.order_clauses
        if order_clauses is not None:
            query = query.order_by(None).order_by(*order_clauses)
        return self.getter(query)

    def process(self):
        if self.request.method == 'GET':
            if self.getter is not None:
                return self.get()
            return self.list()
        elif self.request.method == 'POST':
            if self.getter is not None:
                return self.update()
            return self.insert()
        elif self.request.method == 'DELETE':
            if self.getter is None:
                raise HTTPNotFound()
            return self.delete()
        raise HTTPNotFound()

    @ property
    def pager_slice(self):
        if self.slicer is not None:
            return slice(int(self.slicer['start']),
                         int(self.slicer['stop']))
        return super().pager_slice


def includeme_factory(base_class=None, config_json=True, add_predicates=True,
                      wsgi_iter=False, separators=(',', ':')):
    def includeme(config):
        if config_json and base_class is not None:
            config.add_renderer(
                'json',
                pyramid_json_renderer_factory(
                    base_class, wsgi_iter=wsgi_iter,
                    separators=separators))
            config.add_request_method(pyramid_json_decoder, 'json', reify=True)
        if add_predicates:
            config.add_view_predicate(
                'convert_matchdict', ConvertMatchdictPredicate)
            config.add_view_predicate('catchall', CatchallPredicate)
        if base_class is not None:
            patch_sqlalchemy_base_class(base_class)

    return includeme

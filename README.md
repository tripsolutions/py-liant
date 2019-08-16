- [Introduction](#introduction)
  - [RESTful API](#restful-api)
  - [Opinionated](#opinionated)
  - [Modified JSON](#modified-json)
- [How to use](#how-to-use)
- [Reference](#reference)
  - [JsonObject](#jsonobject)
  - [JSONEncoder](#jsonencoder)
  - [JSONDecoder](#jsondecoder)
  - [pyramid_json_renderer_factory](#pyramidjsonrendererfactory)
  - [pyramid_json_decoder](#pyramidjsondecoder)
  - [patch_sqlalchemy_base_class](#patchsqlalchemybaseclass)
  - [monkeypatch: obj.apply_changes](#monkeypatch-objapplychanges)
  - [CRUDView](#crudview)
  - [ConvertMatchdictPredicate](#convertmatchdictpredicate)
  - [CatchallPredicate](#catchallpredicate)
  - [CatchallView](#catchallview)
    - [Hints syntax](#hints-syntax)
    - [Drilldown support](#drilldown-support)
    - [Single element from collection](#single-element-from-collection)
    - [Filtering, sorting, pagination](#filtering-sorting-pagination)
  - [JsonGuardProvider](#jsonguardprovider)
  - [SearchPathSetter](#searchpathsetter)
  - [EnumAttrs and PythonEnum](#enumattrs-and-pythonenum)

# Introduction

Py-liant is a library of helpers for rapid creation of opinionated RESTful APIs
using pyramid and SQLAlchemy. It provides a read-write set of operations using
a slightly modified object-graph aware JSON structure which is tightly coupled
with the data models being exposed.

It was created by Trip Solutions for internal projects but we feel it may prove 
useful for general consumption.

## RESTful API

The [CRUDView](#crudview) base class assumes the API follows REST conventions
and provides CRUD ([C]reate, [R]ead, [U]pdate, [D]elete) functionality, or a
subset of that. It does not make any assumptions about the endpoints, which are
still defined in user code. There are assumptions being made about the format of
the payloads, see [Modified JSON](#modified-json) and [CrudView](#crudview)

## Opinionated

The [CatchallView](#catchallview) base class however provides a custom parser 
for the URL string and is heavily opinionated about the structure of the API. 
This allows it to be effortlessly deployed on top of existing SQLAlchemy data 
structures but has the disadvantage of being less customizable.

## Modified JSON

ORM data models are not always trees. Any real-world application beyond a
certain complexity level is bound to get to a point where mapping deep data
models directly to JSON is [not
feasible](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Errors/Cyclic_object_value).
In our first iterations we've worked around this issue by manually decoupling
the JSON from the structure, but any manual process quickly turns into a time
sink; it adds a lot of complexity for both client and server code.

Py-liant solves the graph awareness issue by reserving two keywords for internal
use in the JSON graph. Any object that needs to be referenced from within the
JSON structure will get a special key `_id` with a generated value. References
to an object are codified using an object with a sigle key `_ref` matching the
`_id` of the referenced object. Please note, this is only true for SQLAlchemy
model objects.

For example, given the model declaration below:
```python
from sqlalchemy.orm import relationship, backref
from sqlalchemy import Column, Integer, Text, ForeignKey
from sqlalchemy.ext.declarative import declarative_base
Base = declarative_base()


class Parent(Base):
    __tablename__ = 'parent'
    id = Column(Integer, primary_key=True)
    data = Column(Text)


class Child(Base):
    __tablename__ = 'child'
    id = Column(Integer, primary_key=True)
    parent_id = Column(ForeignKey(Parent.id))
    data = Column(Text)

    parent = relationship(Parent, backref=backref('children'))
```

the following code snippent

```python
from py_liant.json_encoder import JSONEncoder
encoder = JSONEncoder(base_type=Base, check_circular=False, indent=4*' ')
parent = Parent(id=1, data="parent object")
parent.children.extend([
    Child(id=1, data="child 1"),
    Child(id=2, data="child 2")
])
print(encoder.encode(parent))
```

will output

```json
{
    "id": 1,
    "data": "parent object",
    "children": [
        {
            "id": 1,
            "data": "child 1",
            "parent": {
                "_ref": 1
            },
            "_id": 2
        },
        {
            "id": 2,
            "data": "child 2",
            "parent": {
                "_ref": 1
            },
            "_id": 3
        }
    ],
    "_id": 1
}
```

The encoder will also extract metadata information from SQLAlchemy models to
support serialization. It will serialize only column and relationship
properties, which means it will not display any non-SQLAlchemy properties. It
also expects all relationships to be eagerly loaded and will avoid triggering
any lazy-loaded properties. Deferred columns are also avoided.

Conversely, the [JSONDecoder](#jsondecoder) will turn a simlarly codified JSON 
structure and return a completed graph, with potentially cyclic or multiple 
references, for use in the application.

The decoder will generate a structure of [JsonObject](#jsonobject)s. If the base
class is patched using [patch_sqlalchemy_base_class](#patchsqlalchemybaseclass),
the decoded object can be used to patch an existing or new SQLAlchemy model
instance.

We also provide a pair of encoder / decoder functions for use in javascript
in [pyliant.js](./pyliant.js).

# How to use

In pyramid's config block you can override the default JSON renderer using the
following:

```python
from py_liant.pyramid import pyramid_json_renderer_factory
config.add_renderer('json', pyramid_json_renderer_factory(Base))
```

Then use `renderer='json'` in any `@view_config()` or `add_view()`.

You can use py-liant's JSON decoder by adding the following in pyramid's config:

```python
from py_liant.pyramid import pyramid_json_decoder
config.add_request_method(pyramid_json_decoder, 'json', reify=True)
```

Thus, for any request with a JSON payload in body you can access the decoded
JsonObject structure using `request.json`.

Patching the SQLAlchemy model's base class:

```python
patch_sqlalchemy_base_class(Base)
```

Adding the view predicates:

```python
config.add_view_predicate('convert_matchdict', ConvertMatchdictPredicate)
config.add_view_predicate('catchall', CatchallPredicate)
```

Py-liant also provides a callable factory to do all of the above:

```python
from py_liant.pyramid import includeme_factory
config.include(includeme_factory(base_class=Base))
# identical to includeme_factory(base_class=Base)(config)
```

Concrete usage examples of [CRUDView](#crudview) and
[CatchallView](#catchallview) can be found in the reference documentation

# Reference

## JsonObject

This class is a `dict` implementation that exposes all string keys as
properties. It eliminates the need to access dictionary values using index
notation (`request.json['prop']` becomes `request.json.prop`). The
[JSONDecoder](#jsondecoder) returns instances of this class.

## JSONEncoder

A `simplejson.JSONEncoder` implementation that adds the following:
- converts `date`, `time` and `datetime` objects to ISO8859 strings
- converts `byte` values to Base64
- strigifies python `Enum` values to their name, `uuid.UUID` values
- tracks SQLAlchemy models (if provided a base class) as discussed in [Modified JSON](#modified-json)
  
Constructor arguments:
```python
JSONDecoder(request=None, base_type=None, **kwargs)
```
`request` should be a pyramid request object. If provided it's used to apply 
[JsonGuardProvider](#jsonguardprovider) fencing for serialization.

`base_type` is the SQLAlchemy models base class. If not provided the
functionality related to SQLAlchemy is disabled.

`kwargs` is passed to `simplejson.JSONEncoder`'s constructor
## JSONDecoder

A `simplejson.JSONDecoder` implementation that returns a
[JsonObject](#jsonobject) as a result and handles `_id`/`_ref` logic as
described in [Modified JSON](#modified-json).

Constructor argumets:
```python
JSONDecoder(**kwargs)
```

`**kwargs` is passed to `simplejson.JSONDecoder`'s constructor.

## pyramid_json_renderer_factory

Factory for a pyramid renderer that provides JSON serialization using
[JSONEncoder](#jsonencoder). See [How to use](#how-to-use) for usage.

Arguments:
```python
pyramid_json_renderer_factory(base_type=None, wsgi_iter=False, 
                              separators=(',',':'))
```

`base_type` and `separators` are passed to [JSONEncoder](#jsonencoder)'s
constructor. The default value for `separators` is meant to minimize payload
size by skipping any unnecessary spaces.

`wsgi_iter` can be used to optimize rendering of JSON by passing an iterable
directly to the WSGI layer. By default the renderer writes directly in the
pyramid `response` object. When activated pyramid can no longer handle error
redirects for execptions thrown during serialization. 

## pyramid_json_decoder

This is a fucnction that can be added to pyramid using
`config.add_request_method`. See [How to use](#how-to-use) for usage.

## patch_sqlalchemy_base_class

This is the function that adds the method
[apply_changes](#monkeypatch-objapplychanges) to SQLAlchemy's base class.

## monkeypatch: obj.apply_changes

```python
obj.apply_changes(data, object_dict=None, context=None, for_update=True)
```

Once SQLAlchemy's base class is patched using
[patch_sqlalchemy_base_class](#patchsqlalchemybaseclass) all model instances get
a method that can be used to apply patches. This can be used directly but most
of the time, if you use [CRUDView](#crudview) and/or
[CatchallView](#catchallview), you won't have to.

The method will apply changes in any depth required. It converts the data types
based on metadata extracted from SQLAlchemy. It handles relationships, both
collections and instances, by tracking and comparing the primary keys provided in JSON. Where needed it will add new instances.

For an object without relationships it applies the values from `data` to their
corresponding column properties in `obj`. No property values are overwritten
unless specified in the `data` object.

If an object has relationships the `data` object can drill down into them. For
collection relationships the `apply_changes` method expects all objects to be
provided in the corresponding array, at a minimum with their primary key
present. If a member of the array does not provide a primary key it is presumed
to be a new instance. If a member of the object's collection cannot be tracked
back to a member of the array in data, it will be removed from the collection.

If the primary key of the descendants is a composite that includes any of the
columns in the foreign key the caller can provide the partial primary key and
py-liant will reconstruct the remaining columns based on the relatonship to the
parent.

If a pyramid `context` is provided that implements
[JsonGuardProvider](#jsonguardprovider), it will be used for security fencing
the patching.

## CRUDView

This class provides CRUD functionality for a given model class. You can
configure the routes and views as needed for your application but the
recommended way is shown below:

```python
config.add_route('parent_pk', 'parent/{id}')
config.add_route('parent_list', 'parent')

@view_config(route_name='parent_pk', request_method='GET', attr='get')
@view_config(route_name='parent_pk', request_method='POST', attr='update')
@view_config(route_name='parent_pk', request_method='DELETE', attr='delete')
@view_config(route_name='parent_list', request_method='GET', attr='list')
@view_config(route_name='parent_list', request_method='POST', attr='insert')
class ParentView(CRUDView):
    target_type = Parent
    target_name = 'parent'

    def __init__(self, request):
        super().__init__(request)
        self.filters = self.auto_filters()
        self.accept_order = self.auto_order()
    
    def identity_filter(self):
        return Parent.id == int(self.request.matchdict('id'))
```

This is enough to provide a complete read-write endpoint for objects of type
`Parent`.

Use `GET /parent/1 HTTP/1.1` to retrieve parent with id=1. It should return
something along the lines of: 

```json
{
  "parent": {
    "id": 1,
    "data": "parent object",
    "_id": 1
  }
}
```
Use  
```
POST /parent/1 HTTP/1.1

{
  "parent": {
    "data": "parent object changed"
  }
}
```

to update the data in instance of parent with id=1.

Posting to `/parent` instead of `/parent/1` will create a new instance instead
of updating an existing one.

`DELETE /parent/2 HTTP/1.1` will delete the parent with id=2.

Finally, `GET /parent HTTP/1.1` will provide a list of all parent instances in
the database. 

For the listing endpoint the following response will be returned:

```json
{
  "items": [
    {
      "id": 1,
      "data": "parent object",
      "_id": 1
    }
  ],
  "total": 1
}
```

The `CRUDView` class also offers pagination support, implicit and explicit
filtering, implicit and explicity sorting.

Pagination is supported via GET parameters `page` and `pageSize` (i.e., `GET
/parent?page=3&pageSize=20`).

Implicit filters and sorting are provided for all column properties. Assuming
column properties `id` and `data` for class User, the following filters will be
added to `self.filters` (in the example usage above, during construction, see
the `auto_filters()` call): id, id_lt, id_le, id_gt, id_ge, data, data_lt,
data_le, data_gt, data_ge, data_like. The filters [field_name]_[operator]
provide filtering using the less-than, less-or-equal, greater-than,
greater-or-equal and contains operators. The last one is automatically generated
for string column properties only.

Automatic filters are also be added (in the example usage above see the call to
`auto_order()`) for both fields.

Filtering in a listing endpoint is done as such: `GET /parent?data_like=object`.
Multiple filters can be applied, i.e. `GET /parent?id_lt=10&id_gt=5`.

Sorting is done by using the GET parameter `order`, i.e. `GET
/parent?order=data`. Multipe sorting expressions can be applied, i.e.
`order=data,id`. In other words the value passed in `order` is a comma-separated
list of sorting keys. Each sorting key also accepts the descending modifier,
i.e. `order=data+desc,id`.

Sorting and filtering keys can also be manually defined. In the usage example above we could have defined some filters and orderings by hand as such:

```python
class ParentView(CRUDView):
    filters = {
        'id': lambda _: Parent.id == int(_),
        'id_lt': lambda _: Parent.id < int(_),
        'data': lambda _: Parent.data == _,
        'data_like': lambda _: Parent.data.contains(_)
    }
    accept_order = {
        'data': Parent.data,
        'data_lowercase': func.lower(Parent.data)
    }
```

Doing this is obviously more laborious but allows you to define custom filters or soring expressions.

The implementation assumes `request.dbsession` is a request method that returns
a SQLAlchemy database session valid for the model.

## ConvertMatchdictPredicate

If pyramid has been configured to use this predicate as indicated in [How to
use](#how-to-use) you can get around the need to convert matchdict parameters.

Pyramid's [URL
Dispatch](https://docs.pylonsproject.org/projects/pyramid/en/latest/narr/urldispatch.html#custom-route-predicates)
documentation page shows the following example for URL matchdict conversion:

```python
def integers(*segment_names):
    def predicate(info, request):
        match = info['match']
        for segment_name in segment_names:
            try:
                match[segment_name] = int(match[segment_name])
            except (TypeError, ValueError):
                pass
        return True
    return predicate

ymd_to_int = integers('year', 'month', 'day')

config.add_route('ymd', '/{year}/{month}/{day}',
                 custom_predicates=(ymd_to_int,))
```

This code ensures both that the route will not match unless predicate executes
succesfully (returns `True`) and that the view will see integer values for keys
`year`, `month` and `day` in `request.matchdict`. While this is very useful it
is unfortunately deprecated functionality. Sice pyramid-1.5 you will get a
deprecation warnin when using `custom_predicates` in routes or views.

To replace this functionality with supported mechanisms we've implemented a
generic new-style route predicate class. To use this class in your routes you
first have to configure it as described in [How to use](#how-to-use). Then in
the example in the previous section the view configs for route `parent_pk`
should change as follows:

```python
@view_config(route_name='parent_pk', request_method='GET', attr='get',
    convert_matchdict=(int, 'id'))
@view_config(route_name='parent_pk', request_method='POST', attr='update',
    convert_matchdict=(int, 'id'))
@view_config(route_name='parent_pk', request_method='DELETE', attr='delete',
    convert_matchdict=(int, 'id'))
```

Please note that while in the old `custom_predicates` method the conversion of
the matchdict parameters was done at route level, the new-style route predicates
do not have access to the matchdict. Therefore we have to use view predicates to
achieve the same.

After these changes you no longer need the `int()` cast in the
`identity_filter()` method. You'll also avoid the need to catch the `ValueError`
exception.

## CatchallPredicate

This is a supporting predicate to be used with [CatchallView](#catchallview). It
assumes the route contains a fizzle parameter of the form `{catchall:.*}` (NOT
`*catchall`}, since the star format creates an array of string values from the
match) that is then parsed internally and converted to values better suited for the [CatchallView](#catchallview) class.

## CatchallView

This is an extension of the [CrudView](#crudview) class that adds support for a
far richer route format based on internal parsing done by the [CatchallPredicate](#catchallpredicate) and has the ability to:
- expose multiple entity types in a single place
- offer arbitrary eager loading depth, as specified in the route's loading hints
- drill into both dynamic and static relationships
- offer slice syntax for easier pagination

To use this class:

```python
# setup route
config.add_route("catchall", '{catchall:.*}')

# declare the class

@view_defaults(renderer='json', catchall={
    'parent': Parent,
    'child': Child
})
@view_config(route_name="catchall", attr='process')
class MyCatchallView(CatchallView):
    pass
```

This code is enough to expose routes such as: 
- `GET /parent` or `GET /child` to list all parents or children
- `GET /parent@1` or `GET /child@1` to get parent with id=1 or child with id=1
- `POST /parent` or `POST /child` to add a new parent
- `POST /parent@1` to update properties for parent with id=1
- `DELETE /parent@1`, `DELETE /child@1` to delete parent with id=1 or child with
  id=1

In other words, both entity types `Parent` and `Child` are accessible from a
single point. 

### Hints syntax

However from your application's perspective alllowing access to
`Child` at the root level might not be something useful, in other words you
might want your API to regard `Child` as tightly bound to `Parent`. CatchallView
allows you to get a parent entity and all children attached in one go using `GET
/parent@1:*children`. The CatchallView will see the portion of the route coming
after the column character as a list of loading hints for `Parent` entity. In
this case, it attaches a `selectinload(Parent.children)` option to the query.

The hints will also allow you to hide properties that might be too large, by
deferring them. I.e. if you added a `blob` property on `Parent` and the caller
might want to avoid retrieving it, they could call `GET /parent@1:-blob`.
Conversely, if the `blob` property is mared as a deferred column in the model
declaration but the caller would want it included in the response they can
undefer it by calling `GET /parent@1:+blob`.

If we also added a `blob` column for the `Child` entity (let's assume it's a
deferred column in the code), the caller can get a parent with all children
including the blob for each by calling `GET /parent@1:*children(+blob)`.
Multiple hints can be provided by comma separating them. This is also the case
for relationship hints: 
- `GET /parent@1:-blob,*children` means "load `Parent` with all `children`
  included and defer loading the column `Parent.blob`".
- `GET /parent@1:-blob,*children(+blob,-blob2,*second_parent)` means "load `Parent`
  with all `children` included, defer column `Parent.blob` and `Child.blob2` and
  undefer column `Child.blob`. For each child also load the relationship `Child.second_parent`.

The hints can have arbitrary depth. Each relationship hint can have hints
referring to the entities of that relationship.

Hints are also applicable to listing requests: `GET /parent:*children` will
effectively retrieve all parents and all associated children.

Please note: dynamic relationship properties cannot be the target of a
relationship hint.

### Drilldown support

If the caller wanted to retrieve just the children of a parent of known id they
could call `GET /parent@1/children`. The last bit of the route is not a hint,
it's a drilldown specifier. This constructs a query that retrieves all children
for parent with id=1, by reading the foreign key constraint of relationship
`Parent.children`.

The drilldown supports both normal relationship properties as well as dynamic
relationship properties. It automatically determines if the target property is a
list or a single entity (i.e. `GET /child@1/parent` also works). All hints
provided must come after the drilldown specifier and they will refer to the
entities in the relationship being drilled down into. For example in the request
`GET /parent@1/children:+blob` the hint will defer loading of column
`Child.blob`.

If the property being drilled into is a collection all [Filtering, sorting and
pagination](#filtering-sorting-pagination) considerations apply. 

### Single element from collection

If the request either refers to a collection property via
[Drilldown](#drilldown-support) or refers to a collection of entities because it
does not contain a primary key specifier the caller can select a single item
from the list by using subscript notation. For example, `GET
/parent@1/children[0]` will retrieve the first child of the `Parent.children`
collection. [Filtering and sorting](#filtering-sorting-pagination) are applied
first.

### Filtering, sorting, pagination

Filtering, sorting and pagination are applied as described in the
[CRUDView](#crudview) section. Only `auto_filters` and `auto_order` are used.
Support for custom expressions is upcoming.

Pagination as supported by [CRUDView](#crudview) is also supported however the
same subscript notation as described in the previous section can be used for
slicing: `GET /parent[0:10]?order_by=data+desc` retrieves the first 10 `Parent`
entities in descending `data` order.

## JsonGuardProvider

For security considerations the flexibility offered by this library can be
detrimental. Model classes can contain references to entities that need to be
protected from the API, both in terms of reading them (when using
[CatchallView](#catchallview)) and in terms of updating them (concerns [any
insert/update method](#monkeypatch-objapplychanges)).

The `JsonGuardProvider` interface allows you to add security fencing for four
areas:
- method `guardSerialize` allows you to control how much information gets
  serialized to JSON
- method `guardUpdate` allows you to control what can be written into the
  entities whenever `obj.apply_changes()` get called
- method `guardHints` allows you to control what [CatchallView
  hints](#hints-syntax) are permitted
- method `guardDrilldown` allows you to control what properties can be 
- [drilled down](#drilldown-support) into via `CatchallView`

To use a `JsonGuardProvider` implement this interface in a Pyramid
[context](https://docs.pylonsproject.org/projects/pyramid/en/latest/narr/urldispatch.html#route-factories)
and attach it to the route and view using `add_route`'s `factory`.

For example:

```python
from py_liant.interfaces import JsonGuardProvider

class MyContext(JsonGuardProvider):
    request = None

    # provide some ACLs, for use with ACLAutorizationPolicy
    def __acl__(self):
        # let's assume any authenticated user should have read access
        if self.request.method == 'GET':
            return [(Allow, Authenticated, "process")]
        # if request verb is POST or DELETE, require admin role
        return [(Allow, "role:admin", "process")]

    def __init__(self, request):
        # we need to look at the request in the implementation
        self.request = request

    def guardSerialize(self, obj, value):
        # always hide Child.second_parent
        if isinstance(obj, Child) and 'second_parent' in value:
            # do NOT modify obj, just change value (JsonObject)
            del value.second_parent

    def guardUpdate(self, obj, data, for_update=True):
        # apply custom changes to the input data, for example encrypt passwords
        if isinstance(obj, User) and 'password' in data:
            # for example passwords can be encrypted
            data.password = hash(data.password)

        # or prevent certain properties being written into by the update
        if isinstance(obj, Parent):
            if 'property' in data:
                del data.property

        # or apply mandatory changes to certain objects
        # TrackedInstanceMixin could be a mixin that adds 'added' and 
        # 'last_updated' columns to entities
        if isinstance(obj, TrackedInstanceMixin):
            # for_update is set to true when obj is newly instantiated
            if not for_update:
                obj.added = datetime.now(timezone.utc)
            obj.last_updated = datetime.now(timezone.utc)

        # if returning falsey value processing for this entity and all 
        # descendants is prevented
        return True

    def guardHints(self, cls, hints):
        # maniupate the hints provided by the caller

        # e.g. remove any hint for Parent.data
        if cls is Parent and Parent.data in hints:
            del hints[Parent.data]
        
        # or add default hints for certain classes
        if cls is Child and Child.data not in hints:
            hints[Child.data] = ('-', None)
        
        if cls is Child and Child.parent not in hints:
            hints[Child.parent] = ('*', [('+', Parent.blob)])

    def guardDrilldown(self, prop) -> bool:
        if prop is Parent.children:
            return False
        return True

# change the rotue definition to include context factory
config.add_route("catchall", '{catchall:.*}', factory=MyContext)
```

## SearchPathSetter

This is a PostgreSQL specific addition that can be used to set up the schema
search path for all newly created database connection. It's implemented as a
SQLAlchemy `PoolListener` (deprecated since version 0.7). A replacement that
uses the modern events API is currently in the works.

It is very unlikely you will need to use this class in your project unless you
need to use multi-tenant databases with configurable schemas.

## EnumAttrs and PythonEnum

`PythonEnum` is a custom implementation of `sqlalchemy.types.Enum` that is
useful in PostgreSQL for declaring named enum types.

Usage:

```python
from enum import Enum
from py_liant.enum import EnumAttrs, PythonEnum

# in PostgreSQL this will generate:
# CREATE TYPE user_type AS ENUM ('admin', 'operator', 'user')
@EnumAttrs('user_type')
class user_type(Enum):
    admin = 'admin'
    operator = 'oeprator'
    user = 'user'

class User(Base):
    __tablename__ = 'parent'
    id = Column(Integer, primary_key=True)
    name = Column(Text)
    user_type = Column(PythonEnum(user_type))
```
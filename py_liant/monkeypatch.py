from sqlalchemy.inspection import inspect
from sqlalchemy.ext.hybrid import HYBRID_PROPERTY
from sqlalchemy.dialects.postgresql import HSTORE
from sqlalchemy.ext.associationproxy import ASSOCIATION_PROXY
from sqlalchemy import (Column, String, DateTime, Time)
from sqlalchemy.orm import (ColumnProperty, CompositeProperty, SynonymProperty, RelationshipProperty, Mapper, Session)
from collections import OrderedDict
from decimal import Decimal
from distutils.util import strtobool
from datetime import date, time, datetime
from dateutil import parser, tz
from enum import Enum
import base64


def coerce_value(cls, column, value, size_check=True):
    if value is None:
        if not column.nullable:
            raise ValueError("Null value not allowed for property %s of type %r" %
                             (column.key, cls))
        return None

    # specialized types
    if type(column.type) == HSTORE:
        if value is None or isinstance(value, dict):
            # coerce all non-string values of hstore dict
            return {key: str(val) for key,val in value.items()}
        raise ValueError("Invalid type %r for property %s of class %r" % (type(value), column.key, cls))

    python_type = column.type.python_type
    if python_type is str:
        if value is not str:
            value = str(value)
        if isinstance(column.type, String) and hasattr(column.type, "length") \
                and value is not None and column.type.length is not None \
                and column.type.length < len(value) and size_check:
            raise ValueError("Text value too large for property %s of class %r, limit %d" %
                             (column.key, cls, column.type.length))

    if python_type is int:
        try:
            value = int(value)
        except ValueError:
            raise ValueError("Could not convert value to target type for property %s of class %r" %
                             (column.key, cls))

    if python_type is Decimal and value is not None and type(value) is not Decimal:
        try:
            value = Decimal(value)
        except ArithmeticError:
            raise ValueError("Could not convert value to target type for property %s of class %r" %
                             (column.key, cls))

    if python_type is float and value is not None and type(value) is not float:
        try:
            value = float(value)
        except ValueError:
            raise ValueError("Could not convert value to target type for property %s of class %r" %
                             (column.key, cls))

    if python_type is bool and value is not None and type(value) is not bool:
        try:
            if type(value) is str:
                value = bool(strtobool(value))
            elif type(value) is int:
                if value not in [0, 1]:
                    raise ValueError
                value = bool(value)
            else:
                raise ValueError
        except ValueError:
            raise ValueError("Expected boolean value for property %s of class %r, received %r instead" %
                             (column.key, cls, type(value)))

    if python_type in (datetime, date, time):
        try:
            use_timezone = False
            if isinstance(column.type, (DateTime, Time)):
                use_timezone = column.type.timezone
            value = parser.parse(value, tzinfos=lambda name, offset: tz.tzoffset(name, offset)
                                 if offset is not None else tz.gettz(name) or None, ignoretz=not use_timezone)
            if python_type is date:
                value = value.date()
            if python_type is time:
                value = value.timetz() if use_timezone else value.time()
        except ValueError:
            raise ValueError("Could not convert value to target type for property %s of class %r" %
                             (column.key, cls))

    if python_type is bytes:
        return base64.b64decode(value)

    if issubclass(python_type, Enum) and value is not None:
        value = str(value)
        value = next(item for item in python_type if item.name == value or item.value == value)

    return value


def _get_pk_from_json(cls, pk_tuple, child_data, fk_pairs=None, parent=None):
    ret = list()

    for col in pk_tuple:
        if col.key in child_data:
            value = child_data[col.key]
            value = coerce_value(cls, col, value)
            ret.append(value)
            continue
        if fk_pairs is not None and parent is not None:
            parent_col = next((pair[0] for pair in fk_pairs if pair[1] == col), None)
            if parent_col is not None:
                parent_mapper = inspect(type(parent))
                value = getattr(parent, parent_mapper.get_property_by_column(parent_col).class_attribute.key)
                ret.append(value)
                continue
        # a foreign key column's value could not be found - return None (it's up to the caller to
        #                                                               determine what this means)
        ret.append(None)
    return ret


def _polymorphic_constructor(cls, data):
    polymorphic_col = cls.__mapper__.polymorphic_on
    if polymorphic_col is None:
        return cls()
    polymorphic_prop = cls.__mapper__.get_property_by_column(polymorphic_col)
    identity = coerce_value(cls, data[polymorphic_prop.key], polymorphic_col)
    return cls.__mapper__.polymorphic_map[identity].class_()


def _apply_changes(self, data, object_dict=None):
    if object_dict is None:
        object_dict = dict()
    # register in object dictionary to prevent loops and worse
    if self in object_dict:
        return
    object_dict[data] = self
    mapper = inspect(type(self))

    for key, attr in mapper.all_orm_descriptors.items():
        if key not in data:
            continue

        value = data[key]

        if attr.extension_type == HYBRID_PROPERTY:
            # for hybrid properties we cannot rely on any typing system;
            # all checks and conversions if any are needed should be done by the setter method
            setter = getattr(attr, 'fset', None)
            if setter is None:
                continue
            setter(self, value)
        elif attr.is_attribute and hasattr(attr, 'property'):
            prop = attr.property
            if isinstance(prop, ColumnProperty):
                # guaranteed there is only one column in the property?
                assert len(prop.columns) == 1
                column = prop.columns[0]
                if not isinstance(column, Column):
                    continue

                # updating a primary key column with autoincrement: no
                if column in (table._autoincrement_column for table in mapper.tables
                              if table._autoincrement_column is not None):
                    continue

                value = coerce_value(type(self), column, value)
                attr.__set__(self, value)
                pass
            elif isinstance(prop, CompositeProperty):
                # composite properties are also quite hard to get right;
                # should rely either on constructor or on value coercion
                attr.__set__(self, value)
            elif isinstance(prop, SynonymProperty):
                raise NotImplementedError("synonym properties not supported")
            elif isinstance(prop, RelationshipProperty):
                # I _REALLY_ want this implemented right... (personal wet dream)
                child_class = prop.argument

                if isinstance(child_class, Mapper):
                    child_class = child_class.class_

                child_mapper = inspect(child_class, False)

                # might need to resolve (class resolver instance)
                if child_mapper is None:
                    child_class = child_class()
                    child_mapper = inspect(child_class)

                if prop.uselist:
                    # list of items
                    # TODO implement mapped collections?
                    collection = attr.__get__(self, None)

                    # empty list/dict or null
                    if not value:
                        collection.clear()
                        continue

                    object_map = OrderedDict()
                    remote_pk_map = {tuple(child_mapper.primary_key_from_instance(item)): item for item in collection}
                    for item in value:
                        if item in object_dict:
                            object_map[item] = object_dict[item]
                            continue

                        pk = _get_pk_from_json(child_class, child_mapper.primary_key, item,
                                               prop.local_remote_pairs, self)
                        pk_incomplete = any(i is None for i in pk)

                        if pk_incomplete or tuple(pk) not in remote_pk_map:
                            if pk_incomplete:
                                child_obj = _polymorphic_constructor(child_class, item)
                            else:
                                child_obj = Session.object_session(self).query(child_class).get(pk)
                                if child_obj is None:
                                    if child_class.__table__._autoincrement_column is None:
                                        child_obj = _polymorphic_constructor(child_class, value)
                                    else:
                                        raise AssertionError("Could not find object of %r[%r] in database" %
                                                             (child_class, pk))
                            object_map[item] = child_obj
                        else:
                            object_map[item] = remote_pk_map[tuple(pk)]

                    for local, remote in object_map.items():
                        if remote not in collection:
                            collection.append(remote)
                        _apply_changes(remote, local, object_dict)

                    for item in collection:
                        if item not in object_map.values():
                            collection.remove(item)

                    # reorder if collection is ordering list
                    if hasattr(collection, 'reorder') and callable(collection.reorder):
                        template = list(object_map.values())
                        collection.sort(key=lambda x: template.index(x))
                        collection.reorder()
                else:
                    # single item
                    if value is None:
                        attr.__set__(self, value)
                    else:
                        if value in object_dict:
                            attr.__set__(self, object_dict[value])
                        else:
                            pk = _get_pk_from_json(child_class, child_mapper.primary_key, value,
                                                   prop.local_remote_pairs, self)

                            current_value = attr.__get__(self, None)
                            # TODO 1) we need to establish definitively how we tell apart bogus data from
                            # TODO    genuinely new objects; some keys may be natural? for now check nulls in PK
                            # TODO     and assume that null PKs means this is a new object
                            # TODO 2) Are there any cases when deferring this would be useful?

                            pk_incomplete = any(i is None for i in pk)

                            if pk_incomplete or current_value is None \
                                    or child_mapper.primary_key_from_instance(current_value) != pk:
                                if pk_incomplete:
                                    child_obj = _polymorphic_constructor(child_class, value)
                                else:
                                    child_obj = Session.object_session(self).query(child_class).get(pk)
                                    if child_obj is None:
                                        if child_class.__table__._autoincrement_column is None:
                                            child_obj = _polymorphic_constructor(child_class, value)
                                        else:
                                            raise AssertionError("Could not find object of %r[%r] in database" %
                                                                 (child_class, pk))
                                _apply_changes(child_obj, value, object_dict)
                                attr.__set__(self, child_obj)
                            else:
                                _apply_changes(current_value, value, object_dict)
            else:
                raise AssertionError("Unexpected property type %r in %r: %s" % (type(prop), type(self), key))
        elif attr.extension_type == ASSOCIATION_PROXY:
            raise NotImplementedError("association proxy updates not supported")
        else:
            raise AssertionError("Unexpected extension type %r in %r: %s" % (attr.extension_type, type(self), key))


def patch_sqlalchemy_base_class(base_class: type):
    base_class.apply_changes = _apply_changes

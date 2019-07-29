import simplejson
from datetime import (datetime, date, time, timedelta)
from isodate import duration_isoformat
from sqlalchemy.inspection import inspect
from sqlalchemy.ext.hybrid import HYBRID_METHOD
from sqlalchemy.ext.associationproxy import ASSOCIATION_PROXY
import base64
from collections import OrderedDict
from enum import Enum
import uuid
from .interfaces import JsonGuardProvider
from .json_object import JsonObject


class JSONEncoder(simplejson.JSONEncoder):
    request = None
    obj_index = None
    base_type = None

    def __init__(self, request=None, base_type=None, **kwargs):
        super().__init__(encoding=None, **kwargs)
        self.request = request
        self.obj_index = set()
        self.base_type = base_type

    def default(self, o):
        # handle date and time format
        if isinstance(o, (datetime, date, time)):
            return o.isoformat()
        if isinstance(o, timedelta):
            return duration_isoformat(o)

        # bytes
        if type(o) == bytes:
            return base64.b64encode(o).decode('utf8')

        # enums
        if isinstance(o, Enum):
            return o.name

        # UUIDs
        if isinstance(o, uuid.UUID):
            return str(o)

        # database objects
        if self.base_type is not None and isinstance(o, self.base_type):
            mapper = inspect(type(o))
            state = inspect(o)

            composite_props = {
                prop for comp in mapper.composites for prop in comp.props}

            pk = mapper.primary_key_from_instance(o)
            pk_index = (type(o),) + tuple(pk)
            if all([val is None for val in pk]):
                pk_str = None
            else:
                json_type = getattr(type(o), '__json_name__', type(o).__name__)
                pk_str = json_type + ':' + ','.join([str(col) for col in pk])
                if pk_index in self.obj_index:
                    return dict(_ref=pk_str)

            self.obj_index.add(pk_index)

            # making sure all paths are explicitly loaded eliminates most of
            # the pressure to provide JSON hints at runtime; instead make sure
            # all relevant paths are loaded
            # TODO: investigate how to improve selection criteria or
            # TODO:   modularize them
            ret = JsonObject({
                key: getattr(o, key)
                for key, value in mapper.all_orm_descriptors.items()
                if key != '__mapper__' and key not in state.unloaded
                and value.extension_type not in (HYBRID_METHOD,
                                                 ASSOCIATION_PROXY)
                and (not hasattr(value, 'property')
                     or value.property not in composite_props)
            })
            ret.update(_id=pk_str)
            if self.request is not None and isinstance(self.request.context,
                                                       JsonGuardProvider):
                self.request.context.guardSerialize(o, ret)
            return OrderedDict(ret)

        # end of handled types, we cannot serialize this type
        return super().default(o)

import simplejson
from datetime import (datetime, date, time, timedelta)
from isodate import duration_isoformat
from sqlalchemy.inspection import inspect
from sqlalchemy.ext.hybrid import HYBRID_METHOD
from sqlalchemy.ext.associationproxy import ASSOCIATION_PROXY
import base64
from enum import Enum
import uuid
from .interfaces import JsonGuardProvider
from .json_object import JsonObject, JsonOrderedObject


class JSONEncoder(simplejson.JSONEncoder):
    request = None
    obj_index = None
    base_type = None
    counter = 0
    sort = False

    def __init__(self, request=None, base_type=None, sort=False, **kwargs):
        super().__init__(encoding=None, **kwargs)
        self.request = request
        self.obj_index = dict()
        self.base_type = base_type
        self.sort = sort

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
            if not all([val is None for val in pk]):
                if pk_index in self.obj_index:
                    referred = self.obj_index[pk_index]
                    return dict(_ref=referred['_id'])

            # making sure all paths are explicitly loaded eliminates most of
            # the pressure to provide JSON hints at runtime; instead make sure
            # all relevant paths are loaded
            ret = JsonOrderedObject() if self.sort else JsonObject()
            descriptors = mapper.all_orm_descriptors.items()
            if self.sort:
                descriptors = sorted(descriptors)
            ret.update({
                key: getattr(o, key)
                for key, value in descriptors
                if key != '__mapper__' and key not in state.unloaded
                and value.extension_type not in (HYBRID_METHOD,
                                                 ASSOCIATION_PROXY)
                and (not hasattr(value, 'property')
                     or value.property not in composite_props)
            })
            self.counter += 1
            ret['_id'] = self.counter
            self.obj_index[pk_index] = ret
            if self.request is not None and isinstance(self.request.context,
                                                       JsonGuardProvider):
                self.request.context.guardSerialize(o, ret)
            return ret

        # end of handled types, we cannot serialize this type
        return super().default(o)

import simplejson
from datetime import (datetime, date, time, timedelta)
from isodate import duration_isoformat
from sqlalchemy.inspection import inspect
from sqlalchemy.ext.hybrid import HYBRID_METHOD
import base64


class JSONEncoder(simplejson.JSONEncoder):
    request = None
    obj_index = None
    base_type = None

    def __init__(self, request, base_type, **kwargs):
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

        # database objects
        if self.base_type is not None and isinstance(o, self.base_type):
            # We had in mind to cache these but looking at the implementation this is already highly optimized
            mapper = inspect(type(o))
            state = inspect(o)

            composite_props = {prop for comp in mapper.composites for prop in comp.props}

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

            # making sure all paths are explicitly loaded eliminates most of the pressure to provide
            # JSON hints at runtime; instead make sure all relevant paths are loaded
            ret = dict(_id=pk_str)
            ret.update({key: getattr(o, key) for key, value in mapper.all_orm_descriptors.items()
                        if key != '__mapper__' and key not in state.unloaded
                        and value.extension_type != HYBRID_METHOD
                        and not (hasattr(value, 'property') and value.property in composite_props)
                        })
            return ret

        # end of handled types, we cannot serialize this type
        return super().default(o)

import simplejson
from .json_object import JsonObject


class JSONDecoder(simplejson.JSONDecoder):
    resolved = None
    unresolved = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, object_hook=self.custom_object_hook, **kwargs)
        self.resolved = dict()
        self.unresolved = dict()

    def custom_object_hook(self, value):
        # references will probably be resolved late
        if len(value) == 1 and "_ref" in value:
            _id = value['_ref']
            if _id in self.resolved:
                return self.resolved[_id]
            if _id in self.unresolved:
                return self.unresolved[_id]
            self.unresolved[_id] = JsonObject()
            return self.unresolved[_id]

        if '_id' in value:
            _id = value['_id']
            del value['_id']

            if _id in self.resolved:
                raise AssertionError(
                    f'two objects with the same _id in payload: {_id}')

            if _id in self.unresolved:
                ret = self.unresolved[_id]
                del self.unresolved[_id]
                ret.update(value)
            else:
                ret = JsonObject(value)
            self.resolved[_id] = ret

            return ret

        return JsonObject(value)

    def decode(self, s, *args, **kwargs):
        ret = super().decode(s, *args, **kwargs)
        if self.unresolved:
            raise AssertionError(
                'Unresolved references: ' +
                ", ".join([str(i) for i in self.unresolved.keys()]))
        self.resolved.clear()
        return ret

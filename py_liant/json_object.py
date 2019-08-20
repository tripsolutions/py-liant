from collections import OrderedDict


class JsonObject(dict):

    def __getattr__(self, item):
        if item not in self:
            raise AttributeError("No such attribute: " + item)
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, item):
        if item in self:
            del self[item]
        else:
            raise AttributeError("No such attribute: " + item)

    def __hash__(self):
        return id(self)


class JsonOrderedObject(OrderedDict):
    # same as JsonObject but based on OrderedDict, useful to get predictable
    # results during testing

    def __getattr__(self, item):
        if item not in self:
            raise AttributeError("No such attribute: " + item)
        return self[item]

    def __setattr__(self, key, value):
        self[key] = value

    def __delattr__(self, item):
        if item in self:
            del self[item]
        else:
            raise AttributeError("No such attribute: " + item)

    def __hash__(self):
        return id(self)

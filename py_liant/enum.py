import re
from sqlalchemy.types import SchemaType, TypeDecorator, Enum


def EnumAttrs(name, schema=None):
    def f(cls):
        if schema is not None:
            cls.__db_schema = schema
        cls.__db_name = name
        return cls
    return f


class PythonEnum(TypeDecorator, SchemaType):
    impl = Enum

    def __init__(self, enum_class, **kw):
        if hasattr(enum_class, "__db_name") and 'name' not in kw:
            kw['name'] = getattr(enum_class, "__db_name")
        if hasattr(enum_class, "__db_schema") and 'schema' not in kw:
            kw['schema'] = getattr(enum_class, "__db_schema")
        if 'name' not in kw:
            kw['name'] = "ck%s" % re.sub('([A-Z])',
                                         lambda m: '_' + m.group(1).lower(),
                                         enum_class.__name__)
        self.impl = Enum(*(m.name for m in enum_class), **kw)
        # super().__init__(*(m.name for m in enum_class), **kw)
        self._enum_class = enum_class

    def process_bind_param(self, value, dialect):
        if isinstance(value, self._enum_class):
            return value.name
        return value

    def process_result_value(self, value, dialect):
        if value is not None:
            return self._enum_class[value]

    @property
    def python_type(self):
        return self._enum_class

    def copy(self):
        return PythonEnum(self._enum_class)

    def _set_table(self, table, column):
        self.impl._set_table(table, column)

    def __repr__(self):
        return repr(self.impl)

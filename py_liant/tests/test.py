import unittest
from pyramid import testing
import transaction


class EngineTest(unittest.TestCase):
    # base for tests that require an engine
    def setUp(self):
        self.config = testing.setUp(settings={
            'sqlalchemy.url': 'sqlite:///:memory:'
        })
        from .models import includeme
        self.config.include(includeme)
        settings = self.config.get_settings()

        from ..tests.models import (
            get_engine,
            get_session_factory,
            get_tm_session
        )

        self.engine = get_engine(settings)
        session_factory = get_session_factory(self.engine)

        self.session = get_tm_session(session_factory, transaction.manager)

    def init_database(self):
        from ..tests.models import Base
        from py_liant.monkeypatch import patch_sqlalchemy_base_class
        patch_sqlalchemy_base_class(Base)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        from ..tests.models import Base

        testing.tearDown()
        transaction.abort()
        Base.metadata.drop_all(self.engine)


class TestJsonEncoderDecoder(EngineTest):

    expected_json = '''\
{
    "children": [
        {
            "data": "child value",
            "id": 1,
            "parent": {
                "_ref": 1
            },
            "parent_id": 1,
            "_id": 2
        }
    ],
    "data1": "parent value",
    "data2": "2000-01-01T00:00:00",
    "data3": "P1DT7H12M",
    "data4": "dGVzdA==",
    "data5": "type1",
    "id": 1,
    "_id": 1
}'''

    expected_json2 = '''\
{
    "children": [
        {
            "data": "child value",
            "id": 1,
            "parent_id": 1,
            "_id": 2
        }
    ],
    "data1": "parent value",
    "data2": "2000-01-01T00:00:00",
    "data3": "P1DT7H12M",
    "data4": "dGVzdA==",
    "data5": "type1",
    "id": 1,
    "_id": 1
}'''

    json_to_decode = '''\
{
    "children": [
        {
            "data": "child value",
            "id": 1,
            "parent": {
                "_ref": 1
            },
            "parent_id": 1,
            "_id": 2
        }
    ],
    "other_collection": [
        {"_ref": "custom"},
        {"_ref": 1},
        {
            "_id": "custom",
            "data": "custom object data"
        }
    ],
    "_id": 1
}'''

    def setUp(self):
        super().setUp()
        self.init_database()

        from ..tests.models import Parent, Child, ParentType
        from datetime import datetime, timedelta
        parent = Parent(data1='parent value',
                        data2=datetime(2000, 1, 1, 0, 0, 0),
                        data3=timedelta(days=1.3),
                        data4=b'test',
                        data5=ParentType.type1)
        parent.children.append(Child(data='child value'))
        self.session.add(parent)

    def test_json_serialization(self):
        from py_liant.json_encoder import JSONEncoder
        from ..tests.models import Base, Parent
        from sqlalchemy.orm import joinedload
        encoder = JSONEncoder(base_type=Base, check_circular=False,
                              indent=4 * ' ', sort=True)
        obj = self.session.query(Parent).options(
            joinedload(Parent.children)).get(1)
        info = encoder.encode(obj)
        self.assertMultiLineEqual(info, self.expected_json,
                                  "JSON encoded correctly")

    def test_json_deserialization(self):
        from py_liant.json_decoder import JSONDecoder
        from py_liant.json_object import JsonObject
        decoder = JSONDecoder()
        obj = decoder.decode(self.json_to_decode)
        self.assertIsNotNone(obj, "JSON decoded successfully")
        self.assertIsInstance(obj, JsonObject,
                              "expected JsonObject from decoder")
        self.assertFalse('_id' in obj, "_id succesfully removed from object")
        self.assertIsNotNone(obj.children, "children present")
        self.assertEqual(len(obj.children), 1, "expected one child")
        self.assertIs(obj.children[0].parent, obj,
                      "child's parent loops back on object")
        self.assertEqual(len(obj.other_collection), 3,
                         "other collection size check")
        custom_obj = obj.other_collection[0]
        self.assertEqual(custom_obj.data, "custom object data",
                         "check data in custom object")
        self.assertIs(
            obj.other_collection[2], custom_obj,
            "reference check custom object inverse/delayed resolution")

    # JSON serialization test after parent expunged;
    # we're join-loading children but parent should not show up anymore

    def test_json_serialization_after_expunge(self):
        from py_liant.json_encoder import JSONEncoder
        from ..tests.models import Base, Parent
        from sqlalchemy.orm import joinedload
        encoder = JSONEncoder(base_type=Base, check_circular=False,
                              indent=4 * ' ', sort=True)
        obj = self.session.query(Parent).get(1)
        self.session.expunge(obj)
        obj = self.session.query(Parent).options(joinedload(Parent.children))\
            .get(1)
        info = encoder.encode(obj)
        self.assertMultiLineEqual(info, self.expected_json2,
                                  "JSON encoded correctly")

    # test failure mode: unresolved reference
    def test_json_unresolved(self):
        from py_liant.json_decoder import JSONDecoder
        decoder = JSONDecoder()
        self.assertRaisesRegex(AssertionError, "Unresolved references",
                               decoder.decode, '{"_ref": 1}')

    # test failure mode: _id collision
    def test_json_id_collision(self):
        from py_liant.json_decoder import JSONDecoder
        decoder = JSONDecoder()
        self.assertRaisesRegex(
            AssertionError, "two objects with the same _id", decoder.decode,
            '{"items": [{"_id": 1}, {"_id": 1}]}')

    def test_json_uuid_value(self):
        from py_liant.json_encoder import JSONEncoder
        import uuid
        encoder = JSONEncoder()
        info = encoder.encode({'uuid': uuid.uuid5(uuid.NAMESPACE_DNS,
                                                  "test.com")})
        self.assertEqual(
            info, '{"uuid": "1c39b279-6010-55d9-b677-859bffab8081"}',
            "check UUID encoding")


class TestApplyChanges(EngineTest):
    def setUp(self):
        super().setUp()
        self.init_database()

        from ..tests.models import Parent, Child, ParentType
        from datetime import datetime, timedelta
        parent = Parent(data1='parent value',
                        data2=datetime(2000, 1, 1, 0, 0, 0),
                        data3=timedelta(days=1.3),
                        data4=b'test',
                        data5=ParentType.type1)
        parent.children.append(Child(data='child value'))
        self.session.add(parent)

    def test_monkeypatch(self):
        from ..tests.models import Parent
        obj = Parent()
        self.assertTrue(callable(obj.apply_changes), "patch method exists")

    def test_property_change(self):
        from py_liant.json_decoder import JSONDecoder
        from ..tests.models import Parent
        decoder = JSONDecoder()

        obj = self.session.query(Parent).get(1)
        data = decoder.decode('''{"data1": "changed value"}''')
        obj.apply_changes(data)
        self.assertEqual(obj.data1, "changed value",
                         "Parent.data correctly changed")

    def test_deep_property_change(self):
        from py_liant.json_decoder import JSONDecoder
        from ..tests.models import Parent
        decoder = JSONDecoder()

        obj = self.session.query(Parent).get(1)
        obj_child = obj.children[0]

        data = decoder.decode('''\
            {
                "children": [
                    { "id": 1, "data": "child changed value" },
                    { "data": "new child value" }
                ]
            }
            ''')
        obj.apply_changes(data)
        self.assertIs(obj.children[0], obj_child, "first child preserved")
        self.assertEqual(obj_child.data, "child changed value")
        self.assertEqual(len(obj.children), 2, "two children present now")
        obj_child2 = obj.children[1]
        self.assertIsNone(obj_child2.id, "second child is transient")
        self.assertEqual(obj_child2.data, "new child value",
                         "second child correct value")

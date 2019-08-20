# metadata, base, engine and models used in testing

from sqlalchemy import (
    engine_from_config, Column,
    ForeignKey,
    Integer, Text, DateTime, Interval, BLOB
)
from py_liant.enum import PythonEnum
from sqlalchemy.orm import (
    sessionmaker, configure_mappers,
    relationship, backref
)
from sqlalchemy.ext.declarative import declarative_base, declared_attr
from sqlalchemy.schema import MetaData
import zope.sqlalchemy
from enum import Enum


class BaseBase:
    # dynamic table name should be OK for testing

    @declared_attr
    def __tablename__(cls):
        return cls.__name__.lower()


# model definition
metadata = MetaData()
Base = declarative_base(metadata=metadata, cls=BaseBase)


class ParentType(Enum):
    type1 = 'type1'
    type2 = 'type2'
    type3 = 'type3'


class Parent(Base):
    id = Column(Integer, primary_key=True)
    data1 = Column(Text)
    data2 = Column(DateTime)
    data3 = Column(Interval)
    data4 = Column(BLOB)
    data5 = Column(PythonEnum(ParentType))


class Child(Base):
    id = Column(Integer, primary_key=True)
    parent_id = Column(ForeignKey(Parent.id, ondelete="CASCADE"), index=True)
    data = Column(Text)

    parent = relationship(Parent, backref=backref(
        'children', cascade='all, delete-orphan'))


# finalize mappers
configure_mappers()


def get_engine(settings, prefix='sqlalchemy.'):
    return engine_from_config(settings, prefix)


def get_session_factory(engine):
    factory = sessionmaker()
    factory.configure(bind=engine)
    return factory


def get_tm_session(session_factory, transaction_manager):
    """
    Get a ``sqlalchemy.orm.Session`` instance backed by a transaction.

    This function will hook the session to the transaction manager which
    will take care of committing any changes.

    - When using pyramid_tm it will automatically be committed or aborted
      depending on whether an exception is raised.

    - When using scripts you should wrap the session in a manager yourself.
      For example::

          import transaction

          engine = get_engine(settings)
          session_factory = get_session_factory(engine)
          with transaction.manager:
              dbsession = get_tm_session(session_factory, transaction.manager)

    """
    dbsession = session_factory()
    zope.sqlalchemy.register(
        dbsession, transaction_manager=transaction_manager)
    return dbsession


def includeme(config):
    """
    Initialize the model for a Pyramid app.

    Activate this setup using ``config.include('cumulus_auth.models')``.

    """
    settings = config.get_settings()
    settings['tm.manager_hook'] = 'pyramid_tm.explicit_manager'

    # use pyramid_tm to hook the transaction lifecycle to the request
    config.include('pyramid_tm')

    # use pyramid_retry to retry a request when transient exceptions occur
    # config.include('pyramid_retry')

    session_factory = get_session_factory(get_engine(settings))
    config.registry['dbsession_factory'] = session_factory

    # make request.dbsession available for use in Pyramid
    config.add_request_method(
        # r.tm is the transaction manager used by pyramid_tm
        lambda r: get_tm_session(session_factory, r.tm),
        'dbsession',
        reify=True
    )

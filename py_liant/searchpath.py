from sqlalchemy.interfaces import PoolListener
import logging

sa_logger = logging.getLogger('sqlalchemy')


class SearchPathSetter(PoolListener):
    '''
    Dynamically sets the search path on connections checked out from a pool.
    '''

    def __init__(self, search_path='public'):
        self.search_path = search_path

    @staticmethod
    def quote_schema(dialect, schema):
        return dialect.identifier_preparer.quote_schema(schema, False)

    def checkout(self, dbapi_con, con_record, con_proxy):
        dialect = con_proxy._pool._dialect
        search_path = [
            self.quote_schema(dialect, _.strip())
            for _ in self.search_path.split(',')
            if _.strip()
        ]
        if 'public' not in search_path:
            search_path.append('public')
        cursor = dbapi_con.cursor()
        statement = "SET search_path TO %s;" % ', '.join(search_path)
        sa_logger.info(statement)
        cursor.execute(statement)
        dbapi_con.commit()
        cursor.close()

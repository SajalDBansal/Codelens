"""Small result/transaction wrapper around a single embedded connection.

Results are materialized and closed before another query can reuse a native
plan. Transactions roll back on error. No network driver is involved.
"""

import re


class Rows(list):
    def single(self):
        if len(self) > 1:
            raise ValueError("Expected at most one row")
        return self[0] if self else None

    def consume(self):
        return None


class LocalSession:
    def __init__(self, connection, invalidate):
        self.connection = connection
        self.invalidate = invalidate

    def run(self, statement, **parameters):
        # Only cache invalidation uses this classification, never query rewriting.
        if re.search(r"\b(SET|CREATE|MERGE|DELETE|DROP|COPY|ALTER)\b", statement, re.I):
            self.invalidate()
        result = self.connection.execute(statement, parameters)
        try:
            names = result.get_column_names()
            return Rows(dict(zip(names, values)) for values in result.get_all())
        finally:
            result.close()

    def execute_write(self, callback):
        self.run("BEGIN TRANSACTION")
        try:
            result = callback(self)
            self.run("COMMIT")
            return result
        except BaseException:
            self.run("ROLLBACK")
            raise

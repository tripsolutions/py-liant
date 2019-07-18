from pyparsing import (Group, oneOf, Word, alphas, alphanums, delimitedList,
                       Literal, Forward, Optional, Suppress
                       )
field_chars = alphanums + '_'
field = Group(oneOf('+ -')('op') + Word(alphas, field_chars)('name'))
collection = Forward()
hints_parser = delimitedList(field | collection, ',')
collection << Group(Literal('*')('op') + Word(alphas, field_chars)('name') +
                    Optional(Suppress('(') +
                             hints_parser +
                             Suppress(')'))('children'))

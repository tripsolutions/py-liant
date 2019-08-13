from pyparsing import (Group, oneOf, Word, alphas, alphanums, nums,
                       delimitedList, Literal, Forward, Optional, Suppress
                       )

# a field
field_chars = alphanums + '_'
fieldname = Word(alphas, field_chars)

field = Group(oneOf('+ -')('op') + fieldname('name'))
collection = Forward()
hints_parser = delimitedList(field | collection)
collection << Group(Literal('*')('op') + fieldname('name') +
                    Optional(Suppress('(') + hints_parser +
                             Suppress(')'))('children'))

# route syntax is: verb@pkey,pkey/drilldown[slice]:hints

pkey_chars = alphanums + '_- '

integer_number = Word('-'+nums, nums)

index_parser = Group(
    integer_number('start') + Suppress(':') + integer_number('stop')
    | integer_number('index'))


route_parser = fieldname('verb') + \
    Optional(Suppress('@') + delimitedList(Word(pkey_chars))('pkey')) + \
    Optional(Suppress('/') + fieldname('drilldown')) + \
    Optional(Suppress('[') + index_parser('slice') + Suppress(']')) + \
    Optional(Suppress(':') + hints_parser('hints'))

from pyparsing import (Group, oneOf, Word, alphas, alphanums, nums,
                       delimitedList, Literal, Forward, Optional, Suppress
                       )

field_chars = alphanums + '_'
fieldname = Word(alphas, field_chars)

# a field -- value property to be ignored (-)
# or loaded if declared deferred (+)
field = Group(oneOf('+ -')('op') + fieldname('name'))
# a relationship, collection or single value, to be loaded
collection = Forward()
# a casting directive;
caster = Forward()
hints_parser = delimitedList(field | collection | caster)
hints_parser_nocast = delimitedList(field | collection)
collection << Group(Literal('*')('op') + fieldname('name') +
                    Optional(Suppress('(') + hints_parser +
                             Suppress(')'))('children'))
caster << Group(Literal("!")('op') + fieldname('type') +
                Suppress('(') + hints_parser_nocast('children') +
                Suppress(')')
                )

# route syntax is: verb@pkey,pkey/drilldown[slice]:hints

pkey_chars = alphanums + '_- '

integer_number = Word('-'+nums, nums)

index_parser = Group(
    integer_number('start') + Suppress(':') + integer_number('stop')
    | integer_number('index'))


route_parser = fieldname('verb') + \
    Optional(Suppress('!') + fieldname('cast')) + \
    Optional(Suppress('@') + delimitedList(Word(pkey_chars))('pkey')) + \
    Optional(Suppress('/') + fieldname('drilldown')) + \
    Optional(Suppress('[') + index_parser('slice') + Suppress(']')) + \
    Optional(Suppress(':') + hints_parser('hints'))

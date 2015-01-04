from jinja2 import Undefined


def get_undefined_info(undefined):
    if isinstance(undefined, Undefined):
        try:
            undefined._fail_with_undefined_error()
        except Exception as e:
            return e.message
    return 'defined value'


class RawValue(object):
    __slots__ = ('name', 'value', 'field')

    def __init__(self, name, value=None, field=None):
        self.name = name
        self.value = value
        self.field = field

    def _get_hint(self, prefix, reason):
        if self.field is not None:
            return '%s in field \'%s\': %s' % (prefix, self.field.name, reason)
        return '%s: %s' % (prefix, reason)

    def bad_value(self, reason):
        return Undefined(hint=self._get_hint('Bad value', reason),
                         obj=self.value)

    def missing_value(self, reason):
        return Undefined(hint=self._get_hint('Missing value', reason),
                         obj=self.value)


class Type(object):

    def value_to_raw(self, value):
        return unicode(value)

    def value_from_raw(self, raw):
        return raw

    def __repr__(self):
        return '%s()' % self.__class__.__name__


from lektor.types.primitives import \
     StringType, UuidType, TextType, HtmlType, IntegerType, FloatType, \
     BooleanType, DateType
from lektor.types.special import SortKeyType, SlugType, UrlType
from lektor.types.formats import MarkdownType


builtin_types = {
    # Primitive
    'string': StringType(),
    'text': TextType(),
    'html': HtmlType(),
    'uuid': UuidType(),
    'integer': IntegerType(),
    'float': FloatType(),
    'boolean': BooleanType(),
    'date': DateType(),

    # Special
    'sort_key': SortKeyType(),
    'slug': SlugType(),
    'url': UrlType(),

    # Formats
    'markdown': MarkdownType(),
}

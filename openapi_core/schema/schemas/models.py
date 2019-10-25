"""OpenAPI core schemas models module"""
import attr
import functools
import logging
from collections import defaultdict
from datetime import date, datetime
from uuid import UUID
import re
import warnings

from six import iteritems, integer_types, binary_type, text_type
from jsonschema.exceptions import ValidationError

from openapi_core.extensions.models.factories import ModelFactory
from openapi_core.schema.schemas._format import oas30_format_checker
from openapi_core.schema.schemas.enums import SchemaFormat, SchemaType
from openapi_core.schema.schemas.exceptions import (
    CastError, InvalidSchemaValue,
    UnmarshallerError, UnmarshalValueError, UnmarshalError,
)
from openapi_core.schema.schemas.util import (
    forcebool, format_date, format_datetime, format_byte, format_uuid,
    format_number,
)
from openapi_core.schema.schemas.validators import OAS30Validator

log = logging.getLogger(__name__)


@attr.s
class Format(object):
    unmarshal = attr.ib()
    validate = attr.ib()


class Schema(object):
    """Represents an OpenAPI Schema."""

    TYPE_CAST_CALLABLE_GETTER = {
        SchemaType.INTEGER: int,
        SchemaType.NUMBER: float,
        SchemaType.BOOLEAN: forcebool,
    }

    DEFAULT_UNMARSHAL_CALLABLE_GETTER = {
    }

    def __init__(
            self, schema_type=None, properties=None, items=None,
            schema_format=None, required=None, default=None, nullable=False,
            enum=None, deprecated=False, all_of=None, one_of=None,
            additional_properties=True, min_items=None, max_items=None,
            min_length=None, max_length=None, pattern=None, unique_items=False,
            minimum=None, maximum=None, multiple_of=None,
            exclusive_minimum=False, exclusive_maximum=False,
            min_properties=None, max_properties=None, extensions=None,
            _source=None):
        self.type = SchemaType(schema_type)
        self.properties = properties and dict(properties) or {}
        self.items = items
        self.format = schema_format
        self.required = required or []
        self.default = default
        self.nullable = nullable
        self.enum = enum
        self.deprecated = deprecated
        self.all_of = all_of and list(all_of) or []
        self.one_of = one_of and list(one_of) or []
        self.additional_properties = additional_properties
        self.min_items = int(min_items) if min_items is not None else None
        self.max_items = int(max_items) if max_items is not None else None
        self.min_length = int(min_length) if min_length is not None else None
        self.max_length = int(max_length) if max_length is not None else None
        self.pattern = pattern and re.compile(pattern) or None
        self.unique_items = unique_items
        self.minimum = int(minimum) if minimum is not None else None
        self.maximum = int(maximum) if maximum is not None else None
        self.multiple_of = int(multiple_of)\
            if multiple_of is not None else None
        self.exclusive_minimum = exclusive_minimum
        self.exclusive_maximum = exclusive_maximum
        self.min_properties = int(min_properties)\
            if min_properties is not None else None
        self.max_properties = int(max_properties)\
            if max_properties is not None else None

        self.extensions = extensions and dict(extensions) or {}

        self._all_required_properties_cache = None
        self._all_optional_properties_cache = None

        self._source = _source

    @property
    def __dict__(self):
        return self._source or self.to_dict()

    def to_dict(self):
        from openapi_core.schema.schemas.factories import SchemaDictFactory
        return SchemaDictFactory().create(self)

    def __getitem__(self, name):
        return self.properties[name]

    def get_all_properties(self):
        properties = self.properties.copy()

        for subschema in self.all_of:
            subschema_props = subschema.get_all_properties()
            properties.update(subschema_props)

        return properties

    def get_all_properties_names(self):
        all_properties = self.get_all_properties()
        return set(all_properties.keys())

    def get_all_required_properties(self):
        if self._all_required_properties_cache is None:
            self._all_required_properties_cache =\
                self._get_all_required_properties()

        return self._all_required_properties_cache

    def _get_all_required_properties(self):
        all_properties = self.get_all_properties()
        required = self.get_all_required_properties_names()

        return dict(
            (prop_name, val)
            for prop_name, val in iteritems(all_properties)
            if prop_name in required
        )

    def get_all_required_properties_names(self):
        required = self.required[:]

        for subschema in self.all_of:
            subschema_req = subschema.get_all_required_properties()
            required += subschema_req

        return set(required)

    def get_cast_mapping(self):
        mapping = self.TYPE_CAST_CALLABLE_GETTER.copy()
        mapping.update({
            SchemaType.ARRAY: self._cast_collection,
        })

        return defaultdict(lambda: lambda x: x, mapping)

    def cast(self, value):
        """Cast value from string to schema type"""
        if value is None:
            return value

        cast_mapping = self.get_cast_mapping()

        cast_callable = cast_mapping[self.type]
        try:
            return cast_callable(value)
        except ValueError:
            raise CastError(value, self.type)

    def _cast_collection(self, value):
        return list(map(self.items.cast, value))

    def get_unmarshal_mapping(self, custom_formatters=None, strict=True):
        primitive_unmarshallers = self.get_primitive_unmarshallers(
            custom_formatters=custom_formatters)

        primitive_unmarshallers_partial = dict(
            (t, functools.partial(u, type_format=self.format, strict=strict))
            for t, u in primitive_unmarshallers.items()
        )

        pass_defaults = lambda f: functools.partial(
          f, custom_formatters=custom_formatters, strict=strict)
        mapping = self.DEFAULT_UNMARSHAL_CALLABLE_GETTER.copy()
        mapping.update(primitive_unmarshallers_partial)
        mapping.update({
            SchemaType.ANY: pass_defaults(self._unmarshal_any),
            SchemaType.ARRAY: pass_defaults(self._unmarshal_collection),
            SchemaType.OBJECT: pass_defaults(self._unmarshal_object),
        })

        return defaultdict(lambda: lambda x: x, mapping)

    def get_validator(self, resolver=None):
        return OAS30Validator(
            self.__dict__, resolver=resolver, format_checker=oas30_format_checker)

    def validate(self, value, resolver=None):
        validator = self.get_validator(resolver=resolver)
        try:
            return validator.validate(value)
        except ValidationError:
            errors_iter = validator.iter_errors(value)
            raise InvalidSchemaValue(value, self.type, errors_iter)

    def unmarshal(self, value, custom_formatters=None, strict=True):
        """Unmarshal parameter from the value."""
        from openapi_core.unmarshalling.schemas.factories import (
            SchemaUnmarshallersFactory,
        )
        unmarshallers_factory = SchemaUnmarshallersFactory(
            custom_formatters)
        unmarshaller = unmarshallers_factory.create(self)
        return unmarshaller(value, strict=strict)

    def _unmarshal_any(self, value, custom_formatters=None, strict=True):
        types_resolve_order = [
            SchemaType.OBJECT, SchemaType.ARRAY, SchemaType.BOOLEAN,
            SchemaType.INTEGER, SchemaType.NUMBER, SchemaType.STRING,
        ]
        unmarshal_mapping = self.get_unmarshal_mapping()
        if self.one_of:
            result = None
            for subschema in self.one_of:
                try:
                    unmarshalled = subschema.unmarshal(value, custom_formatters)
                except UnmarshalError:
                    continue
                else:
                    if result is not None:
                        log.warning("multiple valid oneOf schemas found")
                        continue
                    result = unmarshalled

            if result is None:
                log.warning("valid oneOf schema not found")

            return result
        else:
            for schema_type in types_resolve_order:
                unmarshal_callable = unmarshal_mapping[schema_type]
                try:
                    return unmarshal_callable(value)
                except (UnmarshalError, ValueError):
                    continue

        log.warning("failed to unmarshal any type")
        return value

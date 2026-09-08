"""Register and safely execute explicitly declared local Agent tools."""

import copy
import re


_TOOL_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SUPPORTED_SCHEMA_TYPES = frozenset({
    "array", "boolean", "integer", "number", "object", "string",
})


class ToolRegistryError(RuntimeError):
    """Base error for rejected registrations and executions."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a decision names a tool that is not registered."""


class ToolArgumentError(ToolRegistryError):
    """Raised when tool arguments do not match the declared JSON schema."""


class ToolExecutionError(ToolRegistryError):
    """Wrap an exception raised inside an approved tool callable."""


class ToolRegistry:
    """Keep tool metadata separate from callables and validate every call."""

    def __init__(self):
        self._tools = {}

    def register_tool(self, name, description, parameters, tool_callable):
        """Register one named callable with a strict JSON-style object schema."""
        if not isinstance(name, str) or not _TOOL_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                "Tool name must use lowercase letters, numbers, and underscores."
            )
        if name in self._tools:
            raise ValueError(f"Tool '{name}' is already registered.")
        if not isinstance(description, str) or not description.strip():
            raise ValueError("Tool description must be non-empty text.")
        if not callable(tool_callable):
            raise ValueError("Tool callable must be callable.")

        schema = copy.deepcopy(parameters)
        _validate_schema_definition(schema, path="parameters")
        if schema.get("type") != "object":
            raise ValueError("Tool parameters must use an object schema.")
        if schema.get("additionalProperties") is not False:
            raise ValueError("Tool parameters must reject additional properties.")

        self._tools[name] = {
            "name": name,
            "description": description.strip(),
            "parameters": schema,
            "callable": tool_callable,
        }
        return self.get_tool(name)

    def get_tool(self, name):
        """Return public metadata for one tool or None when it is unknown."""
        record = self._tools.get(name)
        return _public_tool(record) if record is not None else None

    def get_tools(self):
        """Return serializable tool descriptions in registration order."""
        return [_public_tool(record) for record in self._tools.values()]

    def execute_tool(self, name, arguments):
        """Validate structured arguments before invoking a registered callable."""
        if not isinstance(name, str) or name not in self._tools:
            raise ToolNotFoundError(f"Tool '{name}' is not registered.")
        if not isinstance(arguments, dict):
            raise ToolArgumentError("Tool arguments must be a JSON object.")

        record = self._tools[name]
        _validate_value(arguments, record["parameters"], path="arguments")
        try:
            return record["callable"](**arguments)
        except ToolRegistryError:
            raise
        except Exception as error:
            raise ToolExecutionError(
                f"Tool '{name}' could not complete: {error}"
            ) from error


def _public_tool(record):
    return {
        "name": record["name"],
        "description": record["description"],
        "parameters": copy.deepcopy(record["parameters"]),
    }


def _validate_schema_definition(schema, path):
    if not isinstance(schema, dict):
        raise ValueError(f"{path} must be a schema object.")

    schema_type = schema.get("type")
    if schema_type not in _SUPPORTED_SCHEMA_TYPES:
        raise ValueError(f"{path}.type is unsupported.")

    enum = schema.get("enum")
    if enum is not None and (not isinstance(enum, list) or not enum):
        raise ValueError(f"{path}.enum must be a non-empty JSON array.")

    if schema_type == "object":
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if not isinstance(properties, dict) or not isinstance(required, list):
            raise ValueError(f"{path} object properties and required must be valid.")
        if any(not isinstance(name, str) for name in required):
            raise ValueError(f"{path}.required must contain property names.")
        if not set(required).issubset(properties):
            raise ValueError(f"{path}.required contains an unknown property.")
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"{path} must reject additional properties.")
        for name, child_schema in properties.items():
            if not isinstance(name, str) or not name:
                raise ValueError(f"{path} has an invalid property name.")
            _validate_schema_definition(child_schema, f"{path}.{name}")
    elif schema_type == "array":
        if "items" not in schema:
            raise ValueError(f"{path}.items is required for arrays.")
        _validate_schema_definition(schema["items"], f"{path}.items")


def _validate_value(value, schema, path):
    expected_type = schema["type"]
    valid = {
        "array": isinstance(value, list),
        "boolean": type(value) is bool,
        "integer": type(value) is int,
        "number": type(value) in {int, float},
        "object": isinstance(value, dict),
        "string": isinstance(value, str),
    }[expected_type]
    if not valid:
        raise ToolArgumentError(f"{path} must be {expected_type}.")

    if "enum" in schema and value not in schema["enum"]:
        raise ToolArgumentError(f"{path} is not an allowed value.")

    if expected_type == "object":
        properties = schema.get("properties", {})
        missing = [name for name in schema.get("required", []) if name not in value]
        extras = set(value) - set(properties)
        if missing:
            raise ToolArgumentError(
                f"{path} is missing required field '{missing[0]}'."
            )
        if extras:
            raise ToolArgumentError(
                f"{path} contains unknown field '{sorted(extras)[0]}'."
            )
        for name, child_value in value.items():
            _validate_value(child_value, properties[name], f"{path}.{name}")
    elif expected_type == "array":
        if len(value) < schema.get("minItems", 0):
            raise ToolArgumentError(f"{path} has too few items.")
        if len(value) > schema.get("maxItems", float("inf")):
            raise ToolArgumentError(f"{path} has too many items.")
        for index, item in enumerate(value):
            _validate_value(item, schema["items"], f"{path}[{index}]")
    elif expected_type == "string":
        if len(value) < schema.get("minLength", 0):
            raise ToolArgumentError(f"{path} is too short.")
        if len(value) > schema.get("maxLength", float("inf")):
            raise ToolArgumentError(f"{path} is too long.")
    elif expected_type in {"integer", "number"}:
        if value < schema.get("minimum", float("-inf")):
            raise ToolArgumentError(f"{path} is below the allowed minimum.")
        if value > schema.get("maximum", float("inf")):
            raise ToolArgumentError(f"{path} exceeds the allowed maximum.")


_DEFAULT_REGISTRY = ToolRegistry()


def register_tool(name, description, parameters, tool_callable):
    """Register a tool in the module-level registry."""
    return _DEFAULT_REGISTRY.register_tool(
        name, description, parameters, tool_callable,
    )


def get_tools():
    """Return tools from the module-level registry."""
    return _DEFAULT_REGISTRY.get_tools()


def execute_tool(name, arguments):
    """Execute a tool from the module-level registry."""
    return _DEFAULT_REGISTRY.execute_tool(name, arguments)

# The few parts of configobj that asc uses. configobj ships no types.

import builtins
from collections.abc import Iterable, Mapping
from typing import Any

class ConfigObjError(SyntaxError):
    line_number: int

class ConfigObj(builtins.dict[str, Any]):
    def __init__(
        self,
        infile: Iterable[str] | Mapping[str, Any] | None = None,
        *,
        interpolation: bool = ...,
        raise_errors: bool = ...,
        indent_type: str = ...,
    ) -> None: ...
    def dict(self) -> builtins.dict[str, Any]: ...
    def write(self) -> list[str]: ...

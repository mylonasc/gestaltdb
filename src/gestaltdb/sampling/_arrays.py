"""Internal array contracts shared by sampling containers and kernels.

Dimensions are integers but rank/shape and alignment remain runtime contracts.
These aliases do not convert data, require RAM arrays, or exclude memmaps.
Input normalization may accept other dtypes; the aliases describe its output.
Scalar-reduction callable casts at use sites work around NumPy's scalar
min/max overloads rejected by mypy 2.4; they preserve the native method calls.
"""

from typing import TypeAlias, TypeVar

import numpy as np


ScalarT = TypeVar("ScalarT", bound=np.generic)
TypedArray: TypeAlias = np.ndarray[tuple[int, ...], np.dtype[ScalarT]]
Array: TypeAlias = TypedArray[np.generic]
IntArray: TypeAlias = np.ndarray[tuple[int, ...], np.dtype[np.int64]]
FloatArray: TypeAlias = np.ndarray[tuple[int, ...], np.dtype[np.float64]]
BoolArray: TypeAlias = np.ndarray[tuple[int, ...], np.dtype[np.bool_]]
StringArray: TypeAlias = np.ndarray[tuple[int, ...], np.dtype[np.str_]]

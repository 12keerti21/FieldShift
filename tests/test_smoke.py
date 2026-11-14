"""Placeholder: proves the package imports and the toolchain runs.
Replace once the agro modules land."""

def test_package_imports():
    import fieldshift  # noqa: F401


def test_numerics_available():
    import numpy as np
    import pandas as pd
    import scipy  # noqa: F401

    assert np.isclose(np.mean([1.0, 2.0, 3.0]), 2.0)
    assert len(pd.Series([1, 2, 3])) == 3

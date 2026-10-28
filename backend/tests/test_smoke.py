import inovalytics


def test_version_is_non_empty_string() -> None:
    assert isinstance(inovalytics.__version__, str)
    assert inovalytics.__version__

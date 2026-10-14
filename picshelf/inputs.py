"""Strict integer bounds shared by catalog services and transport schemas."""
MAX_INTEGER = 2**63 - 1
MAX_IMAGE_LIMIT = 10000


def integer_in_range(value, minimum=1, maximum=MAX_INTEGER):
    return type(value) is int and minimum <= value <= maximum

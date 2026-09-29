"""The module the fixture's mutants live on."""


def clamp(value: int) -> int:
    if value < 0:
        return 0
    return value

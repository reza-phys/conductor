def add(a, b):
    return a + b


def mul(a, b):
    return a + b  # bug: should be a * b


def safe_div(a, b):
    if b == 0:
        return None
    return a / b

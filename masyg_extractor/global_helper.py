def transform_value(input_str: str, default_value: str) -> str:
    """
    Transforms the input value by returning a default if it's "n/a" (case-insensitive) or empty.

    Args:
        input_str (str): The input string to transform.
        default_value (str): The default string to return if the input is "n/a" or empty.

    Returns:
        str: The original input if it's valid, otherwise the default_value.
    """
    if not input_str or input_str.strip().lower() == "n/a":
        return default_value
    return input_str
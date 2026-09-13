import random
import re
import string


def generate_sku(product_name: str, random_part_length: int = 4) -> str:
    """
    Generates an SKU code based on the product name.

    The SKU code is composed as follows:
      - A prefix derived from the first 3 alphanumeric characters (in uppercase)
        of the product name (padded with 'X' if there are fewer than 3).
      - A random numeric suffix of length defined by random_part_length (default is 4).

    Args:
        product_name (str): The name of the product.
        random_part_length (int, optional): The number of random digits to append. Defaults to 4.

    Returns:
        str: The generated SKU code.
    """
    # Remove non-alphanumeric characters and convert to uppercase.
    clean_name = re.sub(r'\W+', '', product_name).upper()

    # Use the first 3 characters as a prefix; pad with "X" if needed.
    if len(clean_name) < 3:
        prefix = clean_name.ljust(3, 'X')
    else:
        prefix = clean_name[:3]

    # Generate a random numeric part.
    random_number = ''.join(random.choices(string.digits, k=random_part_length))

    # Combine prefix and random numeric part.
    return f"{prefix}{random_number}"

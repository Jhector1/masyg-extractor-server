import os
from cryptography.fernet import Fernet

# Retrieve your encryption key from the environment variable.
ENCRYPTION_KEY = os.getenv("ENCRYPTION_KEY")
if not ENCRYPTION_KEY:
    raise ValueError("Missing ENCRYPTION_KEY environment variable.")

# Create a Fernet instance using the key.
cipher = Fernet(ENCRYPTION_KEY.encode())

def encrypt_state(user_id: str) -> str:
    """
    Encrypts the user_id and returns a token that can be safely used as the state parameter.
    """
    # Encrypt the user_id (convert to bytes and then decode back to string)
    token = cipher.encrypt(user_id.encode()).decode()
    return token

def decrypt_state(encrypted_state: str) -> str:
    """
    Decrypts the encrypted state parameter and returns the original user_id.
    """
    decrypted_bytes = cipher.decrypt(encrypted_state.encode())
    return decrypted_bytes.decode()

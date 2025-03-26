from dotenv import load_dotenv
import os
import redis
import logging
# Load .env file
load_dotenv()

FIREBASE_CONFIG = {
    "type": os.getenv("FIREBASE_TYPE"),
    "project_id": os.getenv("FIREBASE_PROJECT_ID"),
    "private_key_id": os.getenv("FIREBASE_PRIVATE_KEY_ID"),
    "private_key": os.getenv("FIREBASE_PRIVATE_KEY").replace('\\n', '\n'),
    "client_email": os.getenv("FIREBASE_CLIENT_EMAIL"),
    "client_id": os.getenv("FIREBASE_CLIENT_ID"),
    "auth_uri": os.getenv("FIREBASE_AUTH_URI"),
    "token_uri": os.getenv("FIREBASE_TOKEN_URI"),
    "auth_provider_x509_cert_url": os.getenv("FIREBASE_AUTH_PROVIDER_CERT_URL"),
    "client_x509_cert_url": os.getenv("FIREBASE_CLIENT_CERT_URL"),
    "universe_domain": os.getenv("FIREBASE_UNIVERSE_DOMAIN"),
}
import os

class Config:
    """Base configuration."""
    SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key")
    SESSION_PERMANENT = False
    SESSION_TYPE = "filesystem"
    SESSION_COOKIE_SAMESITE = "Lax"
    CACHE_TYPE = "SimpleCache"
    DEBUG = False
    TESTING = False


class DevelopmentConfig(Config):
    """Development configuration."""
    DEBUG = True
    SESSION_COOKIE_SECURE = False  # Disable secure cookies for local development
    CACHE_TYPE = "SimpleCache"
    SERVER_PORT = 5000
    CLIENT_URL = os.getenv("DEV_CLIENT_URL", "http://localhost:3000")


# class ProductionConfig(Config):
#     """Production configuration."""
#     SESSION_COOKIE_SECURE = True  # Secure cookies for HTTPS
#     SESSION_TYPE = "redis"  # Use Redis for session storage
#     SESSION_REDIS = redis.StrictRedis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
#     CACHE_TYPE = "RedisCache"
#     CACHE_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
#     CLIENT_URL = os.getenv("PROD_CLIENT_URL")
#     LOG_LEVEL = logging.WARNING
# import os
# import logging
# import redis

class ProductionConfig(Config):
    """Production configuration."""
    SESSION_TYPE = "redis"
    SESSION_COOKIE_DOMAIN = os.getenv('SERVER_URL', None)
    SESSION_COOKIE_SECURE = False #True
    SESSION_COOKIE_HTTPONLY = False #True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_REDIS = redis.StrictRedis.from_url(
        os.getenv("REDIS_URL", "redis://localhost:6379/0")
    )
    SECRET_KEY = os.getenv("SECRET_KEY", "fallback-secret-key")
    CACHE_TYPE = "RedisCache"
    CACHE_REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    CLIENT_URL = os.getenv("PROD_CLIENT_URL")
    LOG_LEVEL = logging.WARNING

    @staticmethod
    def init_app(app):
        # Log configuration for debugging
        logging.basicConfig(level=ProductionConfig.LOG_LEVEL)
        app.logger.info(f"SESSION_TYPE: {app.config['SESSION_TYPE']}")
        app.logger.info(f"SESSION_COOKIE_DOMAIN: {app.config['SESSION_COOKIE_DOMAIN']}")
        app.logger.info(f"SESSION_REDIS: {app.config['SESSION_REDIS']}")
        # app.logger.info(f"CACHE_REDIS_URL: {app.config['CACHE_REDIS_URL']}")

# class ProductionConfig(Config):
#     """Production configuration."""
#     SESSION_COOKIE_SECURE = True  # Secure cookies for HTTPS
#     SESSION_TYPE = "filesystem"  # Use Filesystem for session storage
#     SESSION_FILE_DIR = os.getenv("SESSION_FILE_DIR", "/tmp/FAST_API_sessions")  # Default directory for sessions
#     SESSION_FILE_THRESHOLD = 500  # Maximum number of session files before cleanup
#     CACHE_TYPE = "SimpleCache"
#     CLIENT_URL = os.getenv("PROD_"
#                            "CLIENT_URL")
#     LOG_LEVEL = logging.WARNING
import os

import os
import base64

import os
import base64

def setup_google_credentials():
    credentials_base64 = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS_BASE64")
    if not credentials_base64:
        raise Exception("GOOGLE_APPLICATION_CREDENTIALS_BASE64 is not set!")

    # Fix base64 padding
    missing_padding = len(credentials_base64) % 4
    if missing_padding:
        credentials_base64 += "=" * (4 - missing_padding)

    # Decode the base64 string
    try:
        credentials_json = base64.b64decode(credentials_base64).decode("utf-8")
        # print("Decoded JSON Length:", len(credentials_json))  # Debugging: log length
        # print("Decoded JSON (partial):", credentials_json[:100])  # Debugging: log preview
    except Exception as e:
        raise Exception(f"Failed to decode base64: {str(e)}")

    # Write the JSON content to a temporary file
    credentials_path = "/tmp/google_credentials.json"
    try:
        with open(credentials_path, "w") as file:
            file.write(credentials_json)
        # print("Credentials written to:", credentials_path)  # Debugging
    except Exception as e:
        raise Exception(f"Failed to write credentials file: {str(e)}")

    # Set the GOOGLE_APPLICATION_CREDENTIALS environment variable
    os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = credentials_path
import os

def verify_tmp_access():
    try:
        test_path = "/tmp/test_tmp_access.txt"
        with open(test_path, "w") as test_file:
            test_file.write("This is a test.")
        print("Write to /tmp succeeded.")
        os.remove(test_path)
    except Exception as e:
        raise Exception(f"Cannot write to /tmp: {str(e)}")
from google.cloud import vision
setup_google_credentials()

def test_google_vision():
    try:
        client = vision.ImageAnnotatorClient()
        # print("Google Cloud Vision client initialized successfully.")
    except Exception as e:
        raise Exception(f"Failed to initialize Vision API client: {str(e)}")

# test_google_vision()
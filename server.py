import os
from flask import Flask,session
from flask_session import Session
from flask_caching import Cache
from flask_cors import CORS
from dotenv import load_dotenv, find_dotenv
from config import DevelopmentConfig, ProductionConfig
from firebase.firebase_init import firebase_init
from services.helper import init_mail
from routes import register_blueprints
import logging
import redis
import stripe
from config import *

# Load environment variables
load_dotenv(find_dotenv())


# Initialize Flask app
app = Flask(__name__)
# Determine the environment
ENV = os.getenv("FLASK_ENV", "development").lower()
logging.basicConfig(
    level=logging.INFO,  # Ensure DEBUG level logging is enabled
    format='%(asctime)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]  # Send logs to the console
)

app.config["SSE_REDIS_URL"] = "redis://localhost:6379/0"


logging.info(f"Session env: {ENV}")
stripe.set_app_info(
    'stripe-samples/checkout-single-subscription',
    version='0.0.1',
    url='https://github.com/stripe-samples/checkout-single-subscription'
)
stripe.api_key = os.getenv('MASYG_EXTRACTOR_STRIPE_SECRET_KEY')
# test_google_vision()
# verify_tmp_access()
# setup_google_credentials()


# Load the appropriate configuration
# if ENV == "production":
#     app.config.from_object(ProductionConfig)
#     ProductionConfig.init_app(app)
if ENV == "production":
    # Secure secret key for the application
    app.secret_key = os.getenv('SECRET_KEY', default='BAD_SECRET_KEY')
    app.config['SESSION_TYPE'] = 'redis'
    app.config['SESSION_PERMANENT'] = False
    app.config['SESSION_USE_SIGNER'] = True
    app.config['SESSION_REDIS'] = redis.from_url(os.getenv('REDIS_URL', 'redis://127.0.0.1:6379'))
    app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
    app.config['SESSION_COOKIE_HTTPONLY'] = True

    app.config['SESSION_COOKIE_SECURE'] = True
    app.config['SESSION_COOKIE_DOMAIN'] = os.getenv('SERVER_URL', None)
    # Configure Redis for storing the session data on the server-side
    # app.config['SESSION_TYPE'] = 'redis'
    # app.config['SESSION_PERMANENT'] = False
    # app.config['SESSION_USE_SIGNER'] = True
    # app.config['SESSION_REDIS'] = redis.from_url('redis://127.0.0.1:6379')
    # app.secret_key = os.getenv('SECRET_KEY', 'BAD_SECRET_KEY')
    #
    # # Configure Redis for session management
    # app.config.update(
    #     SESSION_TYPE='redis',
    #     SESSION_PERMANENT=False,
    #     SESSION_USE_SIGNER=True,
    #     SESSION_REDIS=redis.from_url(os.getenv('REDIS_URL', 'redis://localhost:6379/0')),
    #     SESSION_COOKIE_DOMAIN=os.getenv('SERVER_URL', None),
    #     SESSION_COOKIE_SECURE=False,  # Set to True in production
    #     SESSION_COOKIE_HTTPONLY=False,  # Set to True in production
    #     SESSION_COOKIE_SAMESITE='Lax',
    #     CACHE_TYPE='RedisCache',
    #     CACHE_REDIS_URL=os.getenv('REDIS_URL', 'redis://localhost:6379/0'),
    #     LOG_LEVEL=logging.WARNING
    # )
    #
    # # Set the client URL for production environment
    # CLIENT_URL = os.getenv('PROD_CLIENT_URL')

else:
    app.config.from_object(DevelopmentConfig)

# Initialize extensions
Session(app)
cache = Cache(app)
firebase_init()
init_mail(app)

# Enable CORS
CORS(app, supports_credentials=True, resources={r"/*": {"origins": os.getenv('CLIENT_URL')}})
import os

# Set the path dynamically using the home directory
os.environ["GOOGLE_APPLICATION_CREDENTIALS"] = "masyg-extractor-f7610ba16076.json"
# print(os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))
# logging.basicConfig(level=logging.DEBUG)
# @app.before_request
# def log_session():
#     try:
#         redis_client = redis.StrictRedis.from_url(os.getenv("REDIS_URL", "redis://localhost:6379/0"))
#         redis_client.ping()
#         # print("Redis connection successful!")
#     except Exception as e:
#         print(f"Redis connection failed: {e}")
#
#     logging.info(f"Session contents before request: {dict(session)}")
#
# @app.after_request
# def log_session_save(response):
#     logging.info(f"Session contents after request: {dict(session)}")
#     return response
# Register routes
register_blueprints(app)



if __name__ == "__main__":
    if os.getenv("FLASK_ENV") == "production":
        app.run(debug=False, port=os.getenv("SERVER_PORT", 5000))
    else:
        app.run(debug=True, port=os.getenv("SERVER_PORT", 5000))

    # app.run(debug=app.config["DEBUG"], port=os.getenv("SERVER_PORT", 5000))

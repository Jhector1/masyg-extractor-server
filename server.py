import os
from flask import Flask
from flask_session import Session
from flask_caching import Cache
from flask_cors import CORS
from dotenv import load_dotenv, find_dotenv

from config import DevelopmentConfig, ProductionConfig
from firebase.firebase_init import firebase_init
from tools.helper import init_mail
from routes import register_blueprints

# Load environment variables
load_dotenv(find_dotenv())

# Determine the environment
ENV = os.getenv("FLASK_ENV", "development").lower()

# Initialize Flask app
app = Flask(__name__)

# Load the appropriate configuration
if ENV == "production":
    app.config.from_object(ProductionConfig)
else:
    app.config.from_object(DevelopmentConfig)

# Initialize extensions
Session(app)
cache = Cache(app)
firebase_init()
init_mail(app)

# Enable CORS
CORS(app, supports_credentials=True, resources={r"/*": {"origins": os.getenv('CLIENT_URL')}})

# Register routes
register_blueprints(app)

if __name__ == "__main__":
    app.run(debug=app.config["DEBUG"], port=os.getenv("SERVER_PORT", 5000))

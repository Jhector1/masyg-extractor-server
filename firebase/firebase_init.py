import firebase_admin
from firebase_admin import credentials, db
import os
from config import FIREBASE_CONFIG

def firebase_init():
    # BASE_DIR = os.path.dirname(os.path.abspath(__file__))
    # cred_path = os.path.join(FIREBASE_CONFIG)

    cred = credentials.Certificate(FIREBASE_CONFIG)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://masyg-extractor-db-default-rtdb.firebaseio.com/'
    })


# # Reference the database
# ref = db.reference('/')
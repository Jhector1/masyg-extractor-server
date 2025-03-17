import firebase_admin
from firebase_admin import credentials
import os

from masyg_extractor.config import FIREBASE_CONFIG


# def firebase_init():
#     # BASE_DIR = os.path.dirname(os.path.abspath(__file__))
#     # cred_path = os.path.join(FIREBASE_CONFIG)
#
#     cred = credentials.Certificate(FIREBASE_CONFIG)
#     firebase_admin.initialize_app(cred, {
#         'databaseURL': os.getenv('FIREBASE_DATABASE_URL')
#     })

def firebase_init():
    try:
        # If the default app exists, this will succeed.
        firebase_admin.get_app()
        print("Firebase initialized")
    except ValueError:
        # If the default app does not exist, initialize it.
        # cred_path = os.path.join(FIREBASE_CONFIG)

        cred = credentials.Certificate(FIREBASE_CONFIG)
        firebase_admin.initialize_app(cred, {
            'databaseURL': os.getenv('FIREBASE_DATABASE_URL')
        })
        print("Firebase initialized")
# # Reference the database
# ref = db.reference('/')
import flask
from flask_session import Session

from routes import register_blueprints
from tools.file_extractor_tools import *
from tools.helper import init_mail
from firebase.firebase_init import firebase_init
# The rest of your Flask app code (e.g., the route handler) goes here
from flask_caching import Cache
from concurrent.futures import ThreadPoolExecutor
from dotenv import load_dotenv, find_dotenv
load_dotenv(find_dotenv())
import os
app = flask.Flask(__name__)

app.config['SECRET_KEY'] = 'masyg extractor'
app.config['SESSION_PERMANENT'] = False  # Ensure sessions don't expire prematurely
app.config['SESSION_TYPE'] = 'filesystem'  # Store sessions locally in files
app.config['SESSION_COOKIE_SECURE'] = False  # Secure cookies are not necessary in dev mode
app.config['SESSION_COOKIE_SAMESITE'] = 'Lax'
Session(app)
firebase_init()
# Allow CORS for requests from the React frontend
CORS(app, supports_credentials=True, resources={r"/*": {"origins": [
    "http://localhost:3000",
    "http://127.0.0.1:3000"
]}})

init_mail(app)

# @app.before_request
# def handle_options_request():
#     print("Hello")
#     if flask.request.method == "OPTIONS":
#         response = flask.make_response('')
#         response.headers['Access-Control-Allow-Origin'] = flask.request.headers.get('Origin')
#         response.headers['Access-Control-Allow-Credentials'] = 'true'
#         response.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
#         response.headers['Access-Control-Allow-Headers'] = 'Content-Type, Authorization'
#
#         return response
# app.config["CACHE_TYPE"] = "RedisCache"
# app.config["CACHE_REDIS_URL"] = "redis://localhost:6379/0"  # Adjust for your Redis setup
# app.secret_key = 'masyg'
# cache = Cache(app)
# # Load the NLP pipeline for summarization
# keyword_extractor = pipeline("summarization", model="facebook/bart-large-cnn")
#
# executor = ThreadPoolExecutor(max_workers=4)
#
# def parse_json_to_dataframe(json_content):
#     try:
#         data = json.loads(json_content)
#         return pd.DataFrame(data)
#     except Exception as e:
#         logging.error(f"JSON parsing error: {e}")
#         return pd.DataFrame()
#
#     # Flask routes
# @app.route('/api/extract-data', methods=['POST'])
# def extract_data():
#     if 'files' not in flask.request.files:
#         return flask.jsonify({'error': 'No files uploaded'}), 400
#
#     uploaded_files = flask.request.files.getlist('files')
#     if not uploaded_files:
#         return flask.jsonify({'error': 'No files provided'}), 400
#
#     results = {}
#
#     def process_file(file):
#         text = extract_text_from_pdf(file)
#         if not text:
#             return [], file.filename
#         json_content = process_text_with_gpt(text)
#         if not json_content:
#             return [], file.filename
#         return parse_json_to_dataframe(json_content).to_dict(orient='records'), file.filename
#
#     # Asynchronously process files
#     futures = [executor.submit(process_file, file) for file in uploaded_files]
#     for future in futures:
#         data, filename = future.result()
#         results[filename] = data
#
#     return flask.jsonify({'keywords': results})
#
# # @app.vffgggggyyyyggyyygvvvyroute('/api/extract-data', methods=['POST'])
# # def extract_data():
# #     try:
# #         # Validate request payload
# #         if 'files' not in flask.request.files:
# #             return flask.jsonify({'error': 'No files uploaded'}), 400
# #
# #         uploaded_files = flask.request.files.getlist('files')
# #         if not uploaded_files:
# #             return flask.jsonify({'error': 'No files provided'}), 400
# #
# #         # Initialize results dictionary
# #         extracted_data = {}
# #
# #         # Process each file
# #         for file in uploaded_files:
# #             logging.info(f"Processing file: {file.filename}")
# #
# #             # Extract data from PDF using GPT
# #             df = extract_pdf_data(file)
# #
# #             if not df.empty:
# #                 # Convert DataFrame to list of dictionaries
# #                 extracted_data[file.filename] = df.to_dict(orient='records')
# #             else:
# #                 logging.warning(f"No data extracted from file: {file.filename}")
# #                 extracted_data[file.filename] = []
# #
# #         if extracted_data:
# #             return flask.jsonify({'keywords': extracted_data})
# #         else:
# #             return flask.jsonify({'error': 'No data extracted from the provided PDFs.'}), 400
#
#     # except Exception as e:
#     #     logging.error(f"An error occurred: {e}")
#     #     return flask.jsonify({'error': 'Server error'}), 500
#
#
#
# @app.route('/api/extract-keywords', methods=['POST'])
# def extract_keywords():
#     try:
#         if 'files' not in flask.request.files:
#             return flask.jsonify({'error': 'No files uploaded'}), 400
#
#         uploaded_files = flask.request.files.getlist('files')
#         all_keywords = {}
#
#         for file in uploaded_files:
#             pdf_reader = PyPDF2.PdfReader(file)
#             text = " ".join((page.extract_text() or '') for page in pdf_reader.pages)
#
#             if len(text) > 1024:
#                 text = text[:1024]
#
#             response = keyword_extractor(text, max_length=50, min_length=10, do_sample=False)
#             keywords = response[0]['summary_text']
#             all_keywords[file.filename] = keywords.split()
#         print(all_keywords)
#         return flask.jsonify({'keywords': all_keywords})
#     except Exception as e:
#         logging.error(f"Error: {e}")
#         return flask.jsonify({'error': 'Server error'}), 500

register_blueprints(app)
if __name__ == '__main__':
    app.run(debug=True, port=5000)
